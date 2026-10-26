import hashlib
import io
import json
from pathlib import Path
import stat
from urllib.error import HTTPError, URLError
import zipfile

import pytest

from scriptolib.updates import (
    API_URL, OWNED_LIST, OWNERSHIP_MANIFEST, PACKAGE_MANIFEST, ReleaseInfo,
    UpdateError, activate_version, check_release, cleanup_versions,
    cleanup_staging, cleanup_download_cache, current_version, download_update, stage_update, version_tuple,
)


def make_zip(path, version="0.2.0", extra=None, manifest_change=None):
    contents = {"runtime/pythonw.exe": b"test runtime", "app/bootstrap.py": b"print('test')\n", "app/src/main.py": b"# test\n"}
    if extra:
        contents.update(extra)
    manifest = {"format": 1, "application": "ScriptoLib", "version": version,
                "files": {name: hashlib.sha256(data).hexdigest() for name, data in contents.items()}}
    if manifest_change:
        manifest_change(manifest)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(PACKAGE_MANIFEST, json.dumps(manifest))
        for name, content in contents.items():
            archive.writestr(name, content)
    return release_for(path, version)


def release_for(path, version="0.2.0"):
    name = f"ScriptoLib-{version}-windows-x64.zip"
    return ReleaseInfo(version, f"https://github.com/jumonja-maker/Scripto/releases/download/v{version}/{name}",
                       hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_size,
                       f"https://github.com/jumonja-maker/Scripto/releases/tag/v{version}")


def response_opener(data, calls=None):
    def open_response(request, timeout):
        if calls is not None:
            calls.append((request.full_url, timeout))
        return io.BytesIO(data)
    return open_response


def release_payload(release):
    return json.dumps({"tag_name": f"v{release.version}", "draft": False, "prerelease": False,
                       "assets": [{"name": f"ScriptoLib-{release.version}-windows-x64.zip",
                                   "browser_download_url": release.download_url, "size": release.size,
                                   "digest": "sha256:" + release.sha256}]}).encode()


def test_version_order_is_numeric_and_rejects_traversal():
    assert version_tuple("0.10.0") > version_tuple("0.9.9")
    for value in ("../0.1.0", "v0.1.0", "0.1.0-beta", "01.0.0", "0.1", None):
        with pytest.raises(UpdateError):
            version_tuple(value)


def test_check_release_no_writes_and_none_if_current(tmp_path):
    source = tmp_path / "source.zip"
    release = make_zip(source)
    calls = []
    opener = response_opener(release_payload(release), calls)
    assert check_release("0.1.0", opener=opener) == release
    assert check_release("0.2.0", opener=opener) is None
    assert calls[0][0] == API_URL
    assert list(tmp_path.iterdir()) == [source]


def test_no_published_release_is_not_network_failure():
    def missing(*args, **kwargs):
        raise HTTPError(API_URL, 404, "not found", {}, None)
    assert check_release("0.1.0", opener=missing) is None
    def offline(*args, **kwargs):
        raise URLError("offline")
    with pytest.raises(UpdateError, match="подключение"):
        check_release("0.1.0", opener=offline)


def test_release_requires_official_url_and_digest(tmp_path):
    release = make_zip(tmp_path / "source.zip")
    payload = json.loads(release_payload(release))
    payload["assets"][0]["browser_download_url"] = "https://example.com/evil.zip"
    with pytest.raises(UpdateError, match="официальном"):
        check_release("0.1.0", opener=response_opener(json.dumps(payload).encode()))
    payload["assets"][0]["browser_download_url"] = release.download_url
    payload["assets"][0].pop("digest")
    with pytest.raises(UpdateError, match="SHA-256"):
        check_release("0.1.0", opener=response_opener(json.dumps(payload).encode()))


def test_download_reuses_verified_local_cache(tmp_path):
    root = tmp_path / "установка с пробелами"
    root.mkdir()
    source = tmp_path / "source.zip"
    release = make_zip(source)
    calls = []
    opener = response_opener(source.read_bytes(), calls)
    destination = download_update(root, release, opener=opener)
    assert destination.is_relative_to(root)
    assert destination.read_bytes() == source.read_bytes()
    assert download_update(root, release, opener=opener) == destination
    assert len(calls) == 1
    assert not list(root.rglob("*.part"))


def test_failed_download_cleans_partial_and_preserves_current(tmp_path):
    root = tmp_path / "install"
    root.mkdir()
    (root / "current.txt").write_text("0.1.0\n")
    source = tmp_path / "source.zip"
    release = make_zip(source)
    with pytest.raises(UpdateError):
        download_update(root, release, opener=response_opener(b"bad"))
    assert not list((root / "cache" / "updates").iterdir())
    assert current_version(root) == "0.1.0"


def test_cache_symlink_cannot_write_outside_install(tmp_path):
    root = tmp_path / "install"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    try:
        (root / "cache").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable")
    source = tmp_path / "source.zip"
    release = make_zip(source)
    with pytest.raises(UpdateError, match="ссылкой"):
        download_update(root, release, opener=response_opener(source.read_bytes()))
    assert list(outside.iterdir()) == []


def test_stage_keeps_pointer_and_media_then_activate_is_atomic(tmp_path):
    root = tmp_path / "install"
    root.mkdir()
    (root / "media").mkdir()
    movie = root / "media" / "Фильм.mkv"
    movie.write_bytes(b"user video")
    (root / "current.txt").write_text("0.1.0\n")
    source = tmp_path / "source.zip"
    release = make_zip(source)
    cached = download_update(root, release, opener=response_opener(source.read_bytes()))
    folder = stage_update(root, cached, release)
    assert current_version(root) == "0.1.0"
    assert not cached.exists()
    assert not list((root / "versions").glob(".stage-*"))
    assert (folder / "runtime" / "pythonw.exe").read_bytes() == b"test runtime"
    assert (folder / OWNERSHIP_MANIFEST).is_file()
    assert (folder / OWNED_LIST).is_file()
    assert set((folder / OWNED_LIST).read_text().splitlines()) == {path.relative_to(folder).as_posix() for path in folder.rglob("*") if path.is_file()}
    assert activate_version(root, "0.2.0") == "0.1.0"
    assert current_version(root) == "0.2.0"
    assert not (root / "current.txt.new").exists()
    assert movie.read_bytes() == b"user video"


@pytest.mark.parametrize("name", ["../escape.txt", "/absolute.txt", "app/../../escape", "app\\..\\evil", "app/C:evil", "media/delete.mkv", "app/CON.txt", "app/a. ", "app/a?b"])
def test_unsafe_zip_paths_rejected_and_cache_cleaned(tmp_path, name):
    root = tmp_path / "install"
    root.mkdir()
    source = tmp_path / "source.zip"
    release = make_zip(source, extra={name: b"evil"})
    cached = download_update(root, release, opener=response_opener(source.read_bytes()))
    with pytest.raises(UpdateError):
        stage_update(root, cached, release)
    assert not cached.exists()
    assert list((root / "versions").iterdir()) == []
    assert source.exists()  # external input file is never removed


def test_symlink_zip_entry_rejected(tmp_path):
    root = tmp_path / "install"
    root.mkdir()
    source = tmp_path / "source.zip"
    make_zip(source)
    with zipfile.ZipFile(source, "a") as archive:
        link = zipfile.ZipInfo("app/link")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(link, "../../media")
    with pytest.raises(UpdateError, match="Ссылки"):
        stage_update(root, source, release_for(source))
    assert source.exists()


def test_windows_case_collisions_rejected(tmp_path):
    root = tmp_path / "install"
    root.mkdir()
    source = tmp_path / "source.zip"
    release = make_zip(source, extra={"app/BOOTSTRAP.py": b"other"})
    with pytest.raises(UpdateError, match="повторяются"):
        stage_update(root, source, release)


def test_file_hash_mismatch_rolls_back_staging(tmp_path):
    root = tmp_path / "install"
    root.mkdir()
    source = tmp_path / "source.zip"
    release = make_zip(source, manifest_change=lambda manifest: manifest["files"].update({"app/bootstrap.py": "0" * 64}))
    with pytest.raises(UpdateError, match="Контрольная сумма файла"):
        stage_update(root, source, release)
    assert list((root / "versions").iterdir()) == []


def test_corrupt_zip_rejected_without_touching_existing_version(tmp_path):
    root = tmp_path / "install"
    root.mkdir()
    source = tmp_path / "bad.zip"
    source.write_bytes(b"this is not a zip")
    with pytest.raises(UpdateError):
        stage_update(root, source, release_for(source))
    assert list((root / "versions").iterdir()) == []


def install_test_version(root, tmp_path, version):
    source = tmp_path / f"source-{version}.zip"
    release = make_zip(source, version)
    return stage_update(root, source, release)


def test_cleanup_removes_only_owned_inactive_version(tmp_path):
    root = tmp_path / "install"
    root.mkdir()
    old = install_test_version(root, tmp_path, "0.1.0")
    current = install_test_version(root, tmp_path, "0.2.0")
    (root / "media").mkdir()
    movie = root / "media" / "Keep.mp4"
    movie.write_bytes(b"media")
    activate_version(root, "0.2.0")
    assert cleanup_versions(root) == []
    assert not old.exists()
    assert current.exists()
    assert movie.read_bytes() == b"media"


def test_cleanup_preserves_unknown_media_inside_old_version(tmp_path):
    root = tmp_path / "install"
    root.mkdir()
    old = install_test_version(root, tmp_path, "0.1.0")
    install_test_version(root, tmp_path, "0.2.0")
    movie = old / "My movie.mkv"
    movie.write_bytes(b"user file")
    activate_version(root, "0.2.0")
    warnings = cleanup_versions(root)
    assert warnings and "пользовательские" in warnings[0]
    assert movie.read_bytes() == b"user file"
    assert (old / "runtime" / "pythonw.exe").exists()


def test_cleanup_without_pointer_preserves_every_version(tmp_path):
    root = tmp_path / "install"
    root.mkdir()
    old = install_test_version(root, tmp_path, "0.1.0")
    assert cleanup_versions(root)
    assert old.exists()


def test_locked_file_cleanup_is_retryable(tmp_path, monkeypatch):
    root = tmp_path / "install"
    root.mkdir()
    old = install_test_version(root, tmp_path, "0.1.0")
    install_test_version(root, tmp_path, "0.2.0")
    activate_version(root, "0.2.0")
    actual_unlink = Path.unlink
    def locked(path, *args, **kwargs):
        if path == old / "runtime" / "pythonw.exe":
            raise PermissionError("busy")
        return actual_unlink(path, *args, **kwargs)
    monkeypatch.setattr(Path, "unlink", locked)
    assert cleanup_versions(root)
    assert (old / OWNERSHIP_MANIFEST).exists()
    monkeypatch.setattr(Path, "unlink", actual_unlink)
    assert cleanup_versions(root) == []
    assert not old.exists()


def test_activation_rejects_missing_or_symlinked_runtime(tmp_path):
    root = tmp_path / "install"
    root.mkdir()
    folder = install_test_version(root, tmp_path, "0.2.0")
    (folder / "runtime" / "pythonw.exe").unlink()
    with pytest.raises(UpdateError, match="неполна"):
        activate_version(root, "0.2.0")
    assert not (root / "current.txt").exists()


def test_interrupted_staging_cleanup_preserves_unknown_files(tmp_path):
    root = tmp_path / "install"
    root.mkdir()
    folder = install_test_version(root, tmp_path, "0.2.0")
    staging = folder.with_name(".stage-0.2.0-abcd1234")
    folder.rename(staging)
    assert cleanup_staging(root) == []
    assert not staging.exists()
    folder = install_test_version(root, tmp_path, "0.3.0")
    staging = folder.with_name(".stage-0.3.0-abcd1234")
    folder.rename(staging)
    (staging / "my movie.mkv").write_bytes(b"user")
    assert cleanup_staging(root)
    assert (staging / "my movie.mkv").exists()


def test_download_cache_cleanup_only_owns_package_names(tmp_path):
    root = tmp_path / "install"
    cache = root / "cache" / "updates"
    cache.mkdir(parents=True)
    (cache / "ScriptoLib-0.1.0-windows-x64.zip.part").write_bytes(b"partial")
    (cache / "ScriptoLib-0.2.0-windows-x64.zip").write_bytes(b"keep")
    (cache / "film.mkv").write_bytes(b"user")
    assert cleanup_download_cache(root, keep={"0.2.0"}) == []
    assert sorted(path.name for path in cache.iterdir()) == ["ScriptoLib-0.2.0-windows-x64.zip", "film.mkv"]


def test_installer_inventory_may_include_its_metadata(tmp_path):
    root = tmp_path / "install"
    root.mkdir()
    old = install_test_version(root, tmp_path, "0.1.0")
    metadata = json.loads((old / OWNERSHIP_MANIFEST).read_text())
    metadata["files"].extend([OWNERSHIP_MANIFEST, OWNED_LIST])
    (old / OWNERSHIP_MANIFEST).write_text(json.dumps(metadata))
    activate_version(root, "0.1.0")
    install_test_version(root, tmp_path, "0.2.0")
    activate_version(root, "0.2.0")
    assert cleanup_versions(root) == []
    assert not old.exists()


def test_cleanup_rejects_ntfs_junction_before_deleting_any_file(tmp_path, monkeypatch):
    root = tmp_path / "install"
    root.mkdir()
    old = install_test_version(root, tmp_path, "0.1.0")
    install_test_version(root, tmp_path, "0.2.0")
    activate_version(root, "0.2.0")
    # Linux CI cannot create an NTFS junction; exercise the Windows detection
    # branch with an injected Path predicate. Real junction test remains Windows.
    monkeypatch.setattr(Path, "is_junction", lambda path: path == old / "runtime", raising=False)
    assert cleanup_versions(root)
    assert (old / "app" / "bootstrap.py").exists()
    assert (old / "runtime" / "pythonw.exe").exists()


def test_cancelled_download_removes_partial(tmp_path):
    root = tmp_path / "install"
    root.mkdir()
    source = tmp_path / "source.zip"
    release = make_zip(source)
    state = {"cancel": False}
    class CancelOnRead(io.BytesIO):
        def read(self, size=-1):
            data = super().read(size)
            state["cancel"] = True
            return data
        read1 = read
    with pytest.raises(UpdateError, match="отменено"):
        download_update(root, release, opener=lambda *_args, **_kwargs: CancelOnRead(source.read_bytes()), cancelled=lambda: state["cancel"])
    assert not list((root / "cache" / "updates").iterdir())


def test_cancelled_staging_preserves_pointer_and_cleans_zip(tmp_path):
    root = tmp_path / "install"
    root.mkdir()
    (root / "current.txt").write_text("0.1.0\n")
    source = tmp_path / "source.zip"
    release = make_zip(source)
    archive = download_update(root, release, opener=response_opener(source.read_bytes()))
    calls = []
    def cancelled():
        calls.append(True)
        return len(calls) > 12
    with pytest.raises(UpdateError, match="отменено"):
        stage_update(root, archive, release, cancelled=cancelled)
    assert not archive.exists()
    assert current_version(root) == "0.1.0"
    assert not list((root / "versions").iterdir())


def test_download_total_deadline_removes_partial(tmp_path, monkeypatch):
    import scriptolib.updates as updates
    root = tmp_path / "install"
    root.mkdir()
    source = tmp_path / "source.zip"
    release = make_zip(source)
    clock = {"now": 0.0}
    class SlowResponse(io.BytesIO):
        def read(self, size=-1):
            clock["now"] += 901
            return super().read(size)
        read1 = read
    monkeypatch.setattr(updates.time, "monotonic", lambda: clock["now"])
    with pytest.raises(UpdateError, match="время загрузки"):
        download_update(root, release, opener=lambda *_args, **_kwargs: SlowResponse(source.read_bytes()))
    assert not list((root / "cache" / "updates").iterdir())
