"""Application-owned storage and process caches, configured before importing Qt.

ScriptoLib never falls back to a system profile directory when its root is not
writable. This is a storage policy, not a sandbox for arbitrary third-party code.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import stat
import sys
import tempfile


class AppPathError(RuntimeError):
    """The selected application directory cannot safely hold application data."""


@dataclass(frozen=True)
class AppPaths:
    root: Path

    def __post_init__(self) -> None:
        # Do not resolve away links here: initialize must inspect their targets.
        object.__setattr__(self, "root", Path(os.path.abspath(os.fspath(self.root))))

    @property
    def data(self) -> Path:
        return self.root / "data"

    @property
    def cache(self) -> Path:
        return self.root / "cache"

    @property
    def media(self) -> Path:
        return self.root / "media"

    @property
    def database(self) -> Path:
        return self.data / "library.sqlite3"

    @property
    def settings(self) -> Path:
        return self.data / "settings.json"

    @property
    def temporary(self) -> Path:
        return self.cache / "tmp"

    def initialize(self) -> AppPaths:
        """Create owned directories and validate destinations without fallback.

        The parent of a new root must already exist. Symlinks/junctions which
        redirect owned destinations outside the selected root are rejected.
        """
        directories = (
            self.root, self.data, self.cache, self.media,
            self.data / "config", self.temporary, self.cache / "qml",
            self.cache / "pycache",
        )
        try:
            resolved_root = self.root.resolve(strict=False)
            if os.path.normcase(str(resolved_root)) != os.path.normcase(str(self.root)):
                raise AppPathError(
                    f"Папка ScriptoLib перенаправлена через ссылку: {self.root}. "
                    "Выберите её реальный путь."
                )
            # Validate all destinations before creating any child directories.
            files = (self.database, self.settings, self.settings.with_suffix(".tmp"),
                     self.data / "scriptolib.log", self.data / "scriptolib.log.1",
                     self.data / "app.lock")
            for destination in (*directories, *files):
                self._check_containment(destination, resolved_root)
            for destination in files:
                if destination.exists() and not destination.is_file():
                    raise AppPathError(f"Вместо файла обнаружена папка: {destination}.")
            for directory in directories:
                directory.mkdir(exist_ok=True)
                self._check_containment(directory, resolved_root)
                if not directory.is_dir():
                    raise AppPathError(f"Не удалось использовать папку: {directory}.")
                self._check_writable(directory)
        except AppPathError:
            raise
        except (OSError, RuntimeError, ValueError) as error:
            raise AppPathError(
                f"Не удалось подготовить папку ScriptoLib: {self.root}. "
                "Выберите существующий диск и папку с правом записи; "
                "данные в системный профиль перенесены не будут."
            ) from error
        return self

    @staticmethod
    def _check_containment(destination: Path, resolved_root: Path) -> None:
        if not destination.resolve(strict=False).is_relative_to(resolved_root):
            raise AppPathError(
                f"Путь выходит за пределы папки ScriptoLib: {destination}. "
                "Уберите внешнюю символическую ссылку или соединение папок."
            )

    @staticmethod
    def _check_writable(directory: Path) -> None:
        if not directory.stat().st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH):
            raise AppPathError(
                f"Папка доступна только для чтения: {directory}. "
                "ScriptoLib нужна папка с правом записи."
            )
        # The probe also checks Windows ACLs; mode bits alone cannot do that.
        probe: str | None = None
        try:
            descriptor, probe = tempfile.mkstemp(prefix=".scriptolib-write-test-", dir=directory)
            os.close(descriptor)
        except OSError as error:
            raise AppPathError(
                f"Нет права записи в папку: {directory}. "
                "Данные не будут сохранены за пределами ScriptoLib."
            ) from error
        finally:
            if probe is not None:
                try:
                    Path(probe).unlink()
                except OSError as error:
                    raise AppPathError(
                        f"Не удалось удалить проверочный файл в папке: {directory}."
                    ) from error


def prepare_environment(paths: AppPaths) -> AppPaths:
    """Prepare owned paths and redirect this process and its children's caches.

    Call before importing PySide6/Qt or starting workers. HOME, USERPROFILE,
    CODEX_HOME and certificate settings are intentionally left untouched.
    """
    paths.initialize()
    values = {
        "TEMP": paths.temporary,
        "TMP": paths.temporary,
        "TMPDIR": paths.temporary,
        "QML_DISK_CACHE_PATH": paths.cache / "qml",
        "XDG_CACHE_HOME": paths.cache,
        "XDG_DATA_HOME": paths.data,
        "XDG_CONFIG_HOME": paths.data / "config",
        "PYTHONPYCACHEPREFIX": paths.cache / "pycache",
    }
    os.environ.update({name: str(path) for name, path in values.items()})
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    # Qt's GPU shader/pipeline caches are independent of QML's cache on Windows.
    os.environ["QSG_RHI_DISABLE_DISK_CACHE"] = "1"
    os.environ["QT_DISABLE_SHADER_DISK_CACHE"] = "1"
    os.environ["QML_DISABLE_DISK_CACHE"] = "1"
    sys.dont_write_bytecode = True
    sys.pycache_prefix = str(paths.cache / "pycache")
    # tempfile may have cached the system temporary directory before bootstrap.
    tempfile.tempdir = str(paths.temporary)
    return paths


def cleanup_setup(paths: AppPaths) -> bool:
    """Remove only the owned setup engine after an interrupted setup wrapper.

    On Windows a still-running engine remains locked; leave it for next launch.
    Unknown files are never recursively removed here.
    """
    marker = paths.root / ".scriptolib-setup.json"
    setup = paths.cache / "setup"
    engine = setup / "engine.exe"
    try:
        for path in (marker, setup, engine):
            if path.is_symlink() or path.is_junction() or not path.resolve().is_relative_to(paths.root.resolve()):
                return False
        if not marker.is_file() or marker.stat().st_size > 4096:
            return False
        value = json.loads(marker.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("application") != "ScriptoLib" or value.get("setup") != 1:
            return False
        engine.unlink(missing_ok=True)
        if setup.exists():
            setup.rmdir()
        marker.unlink()
        return True
    except (OSError, ValueError):
        return False
