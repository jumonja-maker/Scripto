import os
from pathlib import Path
import stat
import sys
import tempfile

import pytest

from scriptolib.paths import AppPathError, AppPaths, prepare_environment


def test_initialize_creates_only_owned_directories(tmp_path):
    root = tmp_path / "ScriptoLib"
    paths = AppPaths(root)
    assert paths.initialize() is paths
    assert paths.database == root / "data" / "library.sqlite3"
    assert paths.settings == root / "data" / "settings.json"
    assert not paths.database.exists()
    assert not paths.settings.exists()
    assert {path.name for path in tmp_path.iterdir()} == {"ScriptoLib"}
    assert {path.name for path in root.iterdir()} == {"data", "cache", "media"}
    assert {path.name for path in paths.cache.iterdir()} == {"tmp", "qml", "pycache"}
    assert all(path.is_dir() for path in root.rglob("*"))
    assert paths.initialize() is paths


def test_existing_checkout_files_are_preserved(tmp_path):
    root = tmp_path / "ScriptoLib"
    root.mkdir()
    source = root / "README.md"
    source.write_text("existing repository", encoding="utf-8")
    paths = AppPaths(root).initialize()
    paths.settings.write_text('{"theme":"dark"}', encoding="utf-8")
    paths.initialize()
    assert source.read_text(encoding="utf-8") == "existing repository"
    assert paths.settings.read_text(encoding="utf-8") == '{"theme":"dark"}'


def test_environment_redirects_process_caches_without_changing_identity_or_tls(tmp_path, monkeypatch):
    controlled = (
        "TEMP", "TMP", "TMPDIR", "QML_DISK_CACHE_PATH", "XDG_CACHE_HOME",
        "XDG_DATA_HOME", "XDG_CONFIG_HOME", "PYTHONPYCACHEPREFIX",
        "PYTHONDONTWRITEBYTECODE",
    )
    for name in controlled:
        monkeypatch.setenv(name, "previous")
    protected = {name: f"unchanged-{name}" for name in (
        "HOME", "USERPROFILE", "CODEX_HOME", "SSL_CERT_FILE",
    )}
    for name, value in protected.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(sys, "dont_write_bytecode", False)
    monkeypatch.setattr(sys, "pycache_prefix", None)
    monkeypatch.setattr(tempfile, "tempdir", "/previous-temp")
    paths = AppPaths(tmp_path / "Моя медиатека")
    assert prepare_environment(paths) is paths
    for name in controlled[:-1]:
        assert Path(os.environ[name]).is_relative_to(paths.root)
        assert Path(os.environ[name]).is_dir()
    assert {name: os.environ[name] for name in protected} == protected
    assert os.environ["PYTHONDONTWRITEBYTECODE"] == "1"
    assert sys.dont_write_bytecode is True
    assert sys.pycache_prefix == str(paths.cache / "pycache")
    assert tempfile.gettempdir() == str(paths.temporary)
    with tempfile.NamedTemporaryFile() as file:
        assert Path(file.name).parent == paths.temporary


def test_does_not_create_missing_parent_directories(tmp_path):
    parent = tmp_path / "not-an-existing-disk-or-parent"
    with pytest.raises(AppPathError, match="правом записи"):
        AppPaths(parent / "ScriptoLib").initialize()
    assert not parent.exists()


def test_readonly_root_fails_without_system_directory_fallback(tmp_path, monkeypatch):
    root = tmp_path / "ScriptoLib"
    root.mkdir()
    monkeypatch.setenv("TEMP", "unchanged-temp")
    old_mode = root.stat().st_mode
    root.chmod(stat.S_IREAD | stat.S_IEXEC)
    try:
        with pytest.raises(AppPathError, match="только для чтения|правом записи|права записи"):
            prepare_environment(AppPaths(root))
        assert os.environ["TEMP"] == "unchanged-temp"
        assert list(root.iterdir()) == []
    finally:
        root.chmod(old_mode)


@pytest.mark.parametrize("relative", ["data", "cache", "media", "cache/tmp", "cache/qml", "data/config"])
def test_external_directory_link_is_rejected_before_external_writes(tmp_path, relative):
    root = tmp_path / "ScriptoLib"
    root.mkdir()
    external = tmp_path / "outside"
    external.mkdir()
    link = root / relative
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        link.symlink_to(external, target_is_directory=True)
    except OSError:
        pytest.skip("Creating directory symlinks is unavailable")
    with pytest.raises(AppPathError, match="за пределы"):
        AppPaths(root).initialize()
    assert list(external.iterdir()) == []


def test_root_link_is_rejected(tmp_path):
    real_root = tmp_path / "actual"
    real_root.mkdir()
    link = tmp_path / "linked"
    try:
        link.symlink_to(real_root, target_is_directory=True)
    except OSError:
        pytest.skip("Creating directory symlinks is unavailable")
    with pytest.raises(AppPathError, match="реальный путь"):
        AppPaths(link).initialize()
    assert list(real_root.iterdir()) == []


@pytest.mark.parametrize("name", ["library.sqlite3", "settings.json", "settings.tmp",
                                   "scriptolib.log", "scriptolib.log.1", "app.lock"])
def test_external_data_file_links_are_rejected(tmp_path, name):
    root = tmp_path / "ScriptoLib"
    (root / "data").mkdir(parents=True)
    outside = tmp_path / name
    outside.write_bytes(b"preserve")
    try:
        (root / "data" / name).symlink_to(outside)
    except OSError:
        pytest.skip("Creating file symlinks is unavailable")
    with pytest.raises(AppPathError, match="за пределы"):
        AppPaths(root).initialize()
    assert outside.read_bytes() == b"preserve"


def test_internal_directory_link_stays_inside_root(tmp_path):
    root = tmp_path / "ScriptoLib"
    (root / "internal-cache").mkdir(parents=True)
    try:
        (root / "cache").symlink_to(root / "internal-cache", target_is_directory=True)
    except OSError:
        pytest.skip("Creating directory symlinks is unavailable")
    paths = AppPaths(root).initialize()
    assert paths.temporary.resolve() == root / "internal-cache" / "tmp"
    assert paths.temporary.is_dir()


def test_file_where_directory_is_expected_is_clear_error(tmp_path):
    root = tmp_path / "ScriptoLib"
    root.mkdir()
    (root / "cache").write_text("keep", encoding="utf-8")
    with pytest.raises(AppPathError):
        AppPaths(root).initialize()
    assert (root / "cache").read_text(encoding="utf-8") == "keep"


def test_directory_where_database_is_expected_is_clear_error(tmp_path):
    root = tmp_path / "ScriptoLib"
    (root / "data" / "library.sqlite3").mkdir(parents=True)
    with pytest.raises(AppPathError, match="Вместо файла"):
        AppPaths(root).initialize()
