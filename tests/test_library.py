from pathlib import Path
import sqlite3

import pytest

from scriptolib.library import LibraryStore, SchemaVersionError


def media(path: Path, content: bytes = b"test media fixture") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def test_import_persists_unicode_metadata_without_changing_source(tmp_path):
    source = media(tmp_path / "Фильмы и музыка" / "Небо над нами.MKV")
    original = source.read_bytes()
    before = source.stat()
    database = tmp_path / "appdata" / "library.sqlite3"
    with LibraryStore(database) as store:
        report = store.add_files([source])
        assert (report.added, report.skipped, report.errors) == (1, 0, [])
    with LibraryStore(database) as store:
        [item] = store.list_items()
        assert item["path"] == str(source.resolve())
        assert item["title"] == "Небо над нами"
        assert item["extension"] == ".mkv"
        assert item["kind"] == "video"
        assert item["category"] == "unassigned"
        assert item["size_bytes"] == len(original)
        assert item["added_at"].endswith("+00:00")
    assert source.read_bytes() == original
    assert source.stat().st_mtime_ns == before.st_mtime_ns


def test_mixed_batch_continues_after_errors_and_reports_each_failure(tmp_path):
    empty = media(tmp_path / "empty.mp4", b"")
    unsupported = media(tmp_path / "notes.txt")
    valid = media(tmp_path / "last.mp4")
    with LibraryStore(tmp_path / "library.db") as store:
        report = store.add_files([tmp_path / "absent.mkv", empty, unsupported, tmp_path, valid])
        assert report.added == 1
        assert report.skipped == 0
        assert report.total == 5
        assert [error.code for error in report.errors] == ["missing", "empty", "unsupported", "not_file"]
        assert all(error.path and error.reason for error in report.errors)
        assert len(store.list_items()) == 1


def test_duplicates_across_batch_restart_and_relative_paths(tmp_path, monkeypatch):
    source = media(tmp_path / "media" / "movie.mp4")
    monkeypatch.chdir(tmp_path)
    database = tmp_path / "library.db"
    with LibraryStore(database) as store:
        report = store.add_files([source, Path("media/movie.mp4"), source])
        assert (report.added, report.skipped) == (1, 2)
    with LibraryStore(database) as store:
        report = store.add_files([source])
        assert (report.added, report.skipped) == (0, 1)
        assert len(store.list_items()) == 1


def test_symlink_alias_does_not_duplicate(tmp_path):
    source = media(tmp_path / "movie.mp4")
    alias = tmp_path / "alias.mp4"
    try:
        alias.symlink_to(source)
    except OSError:
        pytest.skip("Symlinks are not available to this user")
    with LibraryStore(tmp_path / "library.db") as store:
        report = store.add_files([source, alias])
        assert (report.added, report.skipped) == (1, 1)


def test_music_and_explicit_video_categories(tmp_path):
    audio = media(tmp_path / "Песня.FLAC")
    video = media(tmp_path / "Episode01.mkv")
    with LibraryStore(tmp_path / "library.db") as store:
        assert store.add_files([audio, video], "series").added == 2
        entries = {item["kind"]: item for item in store.list_items()}
        assert entries["audio"]["category"] == "music"
        assert entries["video"]["category"] == "series"
        assert store.update_category(entries["video"]["id"], "film")
        assert not store.update_category(-1, "film")
        with pytest.raises(ValueError):
            store.update_category(entries["audio"]["id"], "film")
        with pytest.raises(ValueError):
            store.update_category(entries["video"]["id"], "music")


def test_invalid_category_is_rejected_before_any_import(tmp_path):
    source = media(tmp_path / "movie.mp4")
    with LibraryStore(tmp_path / "library.db") as store:
        with pytest.raises(ValueError):
            store.add_files([source], "unknown")
        with pytest.raises(ValueError):
            store.update_category(1, "unknown")
        assert store.list_items() == []
        report = store.add_files([source], "music")
        assert report.added == 0
        assert report.errors[0].code == "category"


def test_catalogue_removal_keeps_original_file(tmp_path):
    source = media(tmp_path / "movie.mp4")
    with LibraryStore(tmp_path / "library.db") as store:
        store.add_files([source])
        [item] = store.list_items()
        assert store.remove_item(item["id"])
        assert not store.remove_item(item["id"])
        assert store.list_items() == []
    assert source.read_bytes() == b"test media fixture"


def test_missing_file_remains_in_catalogue_and_can_be_queried(tmp_path):
    source = media(tmp_path / "movie.mp4")
    with LibraryStore(tmp_path / "library.db") as store:
        store.add_files([source])
        [item] = store.list_items()
        assert store.availability(item["id"])
        source.unlink()
        assert not store.availability(item["id"])
        assert not store.availability(-1)
        assert store.list_items() == [item]


def test_unknown_future_schema_is_not_modified(tmp_path):
    database = tmp_path / "future.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE future_data (value TEXT)")
        connection.execute("INSERT INTO future_data VALUES ('keep me')")
        connection.execute("PRAGMA user_version = 999")
    with pytest.raises(SchemaVersionError):
        LibraryStore(database)
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 999
        assert connection.execute("SELECT value FROM future_data").fetchone()[0] == "keep me"


def test_unknown_unversioned_database_is_not_adopted(tmp_path):
    database = tmp_path / "unrelated.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE unrelated (value TEXT)")
    with pytest.raises(SchemaVersionError):
        LibraryStore(database)
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 0
        assert connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == [("unrelated",)]


def test_same_filename_in_different_folders_is_not_a_duplicate(tmp_path):
    first = media(tmp_path / "one" / "movie.mp4")
    second = media(tmp_path / "two" / "movie.mp4")
    with LibraryStore(tmp_path / "library.db") as store:
        report = store.add_files([first, second])
        assert report.added == 2
        assert len({item["path"] for item in store.list_items()}) == 2


def test_long_path_and_sql_metacharacters_are_data(tmp_path):
    source = media(tmp_path / ("folder" * 15) / ("длинный" * 10) / "O'Brien; DROP TABLE media_items.mp4")
    with LibraryStore(tmp_path / "library.db") as store:
        assert store.add_files([source]).added == 1
        assert store.list_items()[0]["title"] == "O'Brien; DROP TABLE media_items"


def test_extension_admission_does_not_read_or_claim_media_validity(tmp_path):
    # Decoder validation belongs to the playback milestone, not the catalogue.
    source = media(tmp_path / "not-a-real-video.mp4", b"not a video")
    with LibraryStore(":memory:") as store:
        assert store.add_files([source]).added == 1
        [item] = store.list_items()
        assert "playable" not in item


def test_close_is_idempotent(tmp_path):
    store = LibraryStore(tmp_path / "library.db")
    store.close()
    store.close()
