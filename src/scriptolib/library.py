"""Local media catalogue. Importing records a path; it never changes media files.

File extensions are an admission filter, not a claim that a file can be decoded.
Keep one LibraryStore per thread; SQLite connections are intentionally not shared.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3
import stat
from typing import Iterable


AUDIO_EXTENSIONS = frozenset({
    ".mp3", ".flac", ".wav", ".ogg", ".opus", ".m4a", ".aac", ".wma",
    ".aif", ".aiff", ".alac",
})
VIDEO_EXTENSIONS = frozenset({
    ".mp4", ".mkv", ".avi", ".mov", ".webm", ".m4v", ".mpg", ".mpeg",
    ".ts", ".m2ts", ".mts", ".wmv", ".ogv", ".flv", ".3gp", ".vob",
})
SUPPORTED_EXTENSIONS = AUDIO_EXTENSIONS | VIDEO_EXTENSIONS
CATEGORIES = frozenset({"unassigned", "film", "series", "music", "video"})
SCHEMA_VERSION = 1


class SchemaVersionError(RuntimeError):
    """The catalogue belongs to a different or unknown schema version."""


@dataclass(frozen=True)
class ImportError:
    path: str
    reason: str
    code: str


@dataclass
class ImportReport:
    added: int = 0
    skipped: int = 0
    errors: list[ImportError] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.added + self.skipped + len(self.errors)


class LibraryStore:
    def __init__(self, db_path: str | os.PathLike[str]):
        path = os.fspath(db_path)
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path, timeout=5)
        self._connection.row_factory = sqlite3.Row
        self._closed = False
        try:
            self._initialize_schema()
        except Exception:
            self.close()
            raise

    def _initialize_schema(self) -> None:
        version = self._connection.execute("PRAGMA user_version").fetchone()[0]
        if version == SCHEMA_VERSION:
            # Fail at startup, rather than silently replacing a malformed catalogue.
            self._connection.execute(
                "SELECT id, path, path_key, title, kind, category, extension, "
                "size_bytes, added_at FROM media_items LIMIT 0"
            )
            return
        if version != 0:
            raise SchemaVersionError(
                f"Версия каталога {version} не поддерживается этой версией ScriptoLib."
            )
        existing = self._connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        if existing:
            raise SchemaVersionError("Файл содержит неизвестную структуру каталога.")
        with self._connection:
            self._connection.execute(
                """CREATE TABLE media_items (
                    id INTEGER PRIMARY KEY,
                    path TEXT NOT NULL,
                    path_key TEXT NOT NULL UNIQUE,
                    title TEXT NOT NULL,
                    kind TEXT NOT NULL CHECK(kind IN ('audio', 'video')),
                    category TEXT NOT NULL CHECK(category IN
                        ('unassigned', 'film', 'series', 'music', 'video')),
                    extension TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL CHECK(size_bytes > 0),
                    added_at TEXT NOT NULL,
                    CHECK((kind = 'audio' AND category = 'music') OR
                          (kind = 'video' AND category <> 'music'))
                )"""
            )
            self._connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def add_files(
        self,
        paths: Iterable[str | os.PathLike[str]],
        category: str = "unassigned",
    ) -> ImportReport:
        """Import a batch, recording per-file failures without stopping the batch.

        Duplicate canonical paths count as skipped. Audio always goes into music.
        A caller-supplied music category is valid only for audio files.
        """
        self._validate_category(category)
        report = ImportReport()
        for value in paths:
            original = str(value)
            try:
                path = Path(value).expanduser().resolve(strict=True)
                info = path.stat()
            except FileNotFoundError:
                report.errors.append(ImportError(original, "Файл не найден.", "missing"))
                continue
            except (OSError, RuntimeError, TypeError, ValueError):
                report.errors.append(ImportError(
                    original, "Не удалось получить доступ к файлу.", "unreadable"
                ))
                continue
            if not stat.S_ISREG(info.st_mode):
                report.errors.append(ImportError(original, "Выберите файл, а не папку.", "not_file"))
                continue
            extension = path.suffix.lower()
            if extension not in SUPPORTED_EXTENSIONS:
                report.errors.append(ImportError(
                    original, "Этот тип файла пока не поддерживается.", "unsupported"
                ))
                continue
            if not info.st_size:
                report.errors.append(ImportError(original, "Файл пустой.", "empty"))
                continue
            kind = "audio" if extension in AUDIO_EXTENSIONS else "video"
            item_category = "music" if kind == "audio" else category
            if kind == "video" and item_category == "music":
                report.errors.append(ImportError(
                    original, "Видео нельзя добавить в раздел музыки.", "category"
                ))
                continue
            # normcase applies Windows case-insensitive path rules on Windows.
            # resolve also folds relative paths and filesystem symlink aliases.
            key = os.path.normcase(str(path))
            try:
                with self._connection:
                    cursor = self._connection.execute(
                        """INSERT INTO media_items
                        (path, path_key, title, kind, category, extension, size_bytes, added_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(path_key) DO NOTHING""",
                        (
                            str(path), key, path.stem, kind, item_category, extension,
                            info.st_size, datetime.now(timezone.utc).isoformat(),
                        ),
                    )
                if cursor.rowcount:
                    report.added += 1
                else:
                    report.skipped += 1
            except sqlite3.Error:
                report.errors.append(ImportError(
                    original, "Не удалось сохранить запись в каталоге.", "database"
                ))
        return report

    def list_items(self) -> list[dict]:
        """Read stored metadata without probing or reading the media files."""
        return [dict(row) for row in self._connection.execute(
            "SELECT id, path, title, kind, category, extension, size_bytes, added_at "
            "FROM media_items ORDER BY added_at DESC, id DESC"
        )]

    def update_category(self, item_id: int, category: str) -> bool:
        self._validate_category(category)
        row = self._connection.execute(
            "SELECT kind FROM media_items WHERE id = ?", (item_id,)
        ).fetchone()
        if row is None:
            return False
        if (row["kind"] == "audio") != (category == "music"):
            raise ValueError("Музыка и видео должны оставаться в своих разделах.")
        with self._connection:
            self._connection.execute(
                "UPDATE media_items SET category = ? WHERE id = ?", (category, item_id)
            )
        return True

    def remove_item(self, item_id: int) -> bool:
        """Remove a catalogue entry only. The original file is never deleted."""
        with self._connection:
            result = self._connection.execute("DELETE FROM media_items WHERE id = ?", (item_id,))
        return bool(result.rowcount)

    def availability(self, item_id: int) -> bool:
        """Check a single path on request; never hide a missing catalogue entry."""
        row = self._connection.execute(
            "SELECT path FROM media_items WHERE id = ?", (item_id,)
        ).fetchone()
        if row is None:
            return False
        try:
            info = Path(row["path"]).stat()
            return stat.S_ISREG(info.st_mode) and info.st_size > 0
        except OSError:
            return False

    @staticmethod
    def _validate_category(category: str) -> None:
        if category not in CATEGORIES:
            raise ValueError(f"Неизвестный раздел: {category}")

    def close(self) -> None:
        if not self._closed:
            self._connection.close()
            self._closed = True

    def __enter__(self) -> LibraryStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
