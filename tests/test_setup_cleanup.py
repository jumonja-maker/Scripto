import json
from scriptolib.paths import AppPaths, cleanup_setup


def prepared(tmp_path):
    paths = AppPaths(tmp_path / "ScriptoLib").initialize()
    folder = paths.cache / "setup"
    folder.mkdir()
    (folder / "engine.exe").write_bytes(b"setup engine")
    marker = paths.root / ".scriptolib-setup.json"
    marker.write_text(json.dumps({"application": "ScriptoLib", "setup": 1}))
    return paths, folder, marker


def test_cleanup_removes_only_owned_engine(tmp_path):
    paths, folder, marker = prepared(tmp_path)
    film = paths.media / "film.mkv"
    film.write_bytes(b"media")
    assert cleanup_setup(paths)
    assert not folder.exists() and not marker.exists()
    assert film.read_bytes() == b"media"


def test_unknown_setup_files_preserved(tmp_path):
    paths, folder, marker = prepared(tmp_path)
    (folder / "keep.txt").write_text("user file")
    assert not cleanup_setup(paths)
    assert (folder / "keep.txt").read_text() == "user file"
    assert marker.exists()


def test_unknown_marker_does_not_authorize_cleanup(tmp_path):
    paths, folder, marker = prepared(tmp_path)
    marker.write_text("{}")
    assert not cleanup_setup(paths)
    assert (folder / "engine.exe").exists()


def test_locked_engine_defers_cleanup(tmp_path, monkeypatch):
    paths, folder, marker = prepared(tmp_path)
    original = type(marker).unlink
    def locked(self, *args, **kwargs):
        if self.name == "engine.exe":
            raise PermissionError("locked")
        return original(self, *args, **kwargs)
    monkeypatch.setattr(type(marker), "unlink", locked)
    assert not cleanup_setup(paths)
    assert marker.exists() and (folder / "engine.exe").exists()
