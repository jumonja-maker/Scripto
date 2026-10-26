"""Verified, installation-local update primitives.

The UI may check/download/stage in a worker. ``activate_version`` is deliberately
separate: the launcher/helper must close the old GUI before calling it, launch
the new GUI, wait for its ready acknowledgement, and only then prune versions.
Nothing here deletes media or writes outside the installation root.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
import time
from typing import Callable, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
import zipfile


REPOSITORY = "jumonja-maker/Scripto"
API_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
MAX_METADATA = 1024 * 1024
MAX_DOWNLOAD = 768 * 1024 * 1024
MAX_UNPACKED = 2 * 1024 * 1024 * 1024
MAX_ENTRIES = 30_000
CHUNK_SIZE = 1024 * 1024
VERSION_PATTERN = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z")
PACKAGE_MANIFEST = "scriptolib-package.json"
OWNERSHIP_MANIFEST = ".scriptolib-version.json"
OWNED_LIST = ".scriptolib-owned.txt"
REQUIRED_FILES = {"runtime/pythonw.exe", "app/bootstrap.py"}
_RESERVED_WINDOWS_NAMES = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


class UpdateError(RuntimeError):
    """Expected update failure suitable for a short UI message."""


@dataclass(frozen=True)
class ReleaseInfo:
    version: str
    download_url: str
    sha256: str
    size: int
    release_url: str


def version_tuple(version: str) -> tuple[int, int, int]:
    """Stable X.Y.Z only. Prereleases intentionally are not an update channel."""
    if not isinstance(version, str) or not VERSION_PATTERN.fullmatch(version):
        raise UpdateError("Некорректный номер версии.")
    return tuple(int(part) for part in version.split("."))  # type: ignore[return-value]


def _request(url: str) -> Request:
    return Request(url, headers={"User-Agent": "ScriptoLib-Updater/1", "Accept": "application/vnd.github+json"})


def _release_name(version: str) -> str:
    version_tuple(version)
    return f"ScriptoLib-{version}-windows-x64.zip"


def _validate_release(release: ReleaseInfo) -> None:
    version_tuple(release.version)
    if not re.fullmatch(r"[0-9a-fA-F]{64}", release.sha256):
        raise UpdateError("В релизе нет корректной контрольной суммы SHA-256.")
    if type(release.size) is not int or not 0 < release.size <= MAX_DOWNLOAD:
        raise UpdateError("Недопустимый размер пакета обновления.")
    parsed = urlsplit(release.download_url)
    allowed_paths = {
        f"/{REPOSITORY}/releases/download/{tag}/{_release_name(release.version)}"
        for tag in (release.version, f"v{release.version}")
    }
    if parsed.scheme != "https" or parsed.netloc != "github.com" or parsed.path not in allowed_paths or parsed.query or parsed.fragment:
        raise UpdateError("Пакет обновления должен находиться в официальном репозитории ScriptoLib.")


def check_release(current_version: str, *, opener: Callable = urlopen) -> ReleaseInfo | None:
    """Return a verified stable release description; None means no newer release.

    No files are written and no update is installed. GitHub 404 also means that
    the repository does not yet have a published stable release.
    """
    current = version_tuple(current_version)
    try:
        with opener(_request(API_URL), timeout=15) as response:
            body = response.read(MAX_METADATA + 1)
        if len(body) > MAX_METADATA:
            raise UpdateError("Ответ сервера обновлений слишком большой.")
        payload = json.loads(body)
    except HTTPError as exc:
        if exc.code == 404:
            return None
        raise UpdateError(f"Сервер обновлений недоступен (HTTP {exc.code}).") from exc
    except (OSError, URLError, ValueError) as exc:
        raise UpdateError("Не удалось проверить обновления. Проверьте подключение к интернету.") from exc
    if not isinstance(payload, dict):
        raise UpdateError("Некорректный ответ сервера обновлений.")
    if payload.get("draft") or payload.get("prerelease"):
        return None
    tag = payload.get("tag_name", "")
    version = tag[1:] if isinstance(tag, str) and tag.startswith("v") else tag
    if version_tuple(version) <= current:
        return None
    assets = payload.get("assets")
    if not isinstance(assets, list):
        raise UpdateError("В релизе отсутствует пакет Windows x64.")
    matches = [asset for asset in assets if isinstance(asset, dict) and asset.get("name") == _release_name(version)]
    if len(matches) != 1:
        raise UpdateError("В релизе отсутствует однозначный пакет Windows x64.")
    asset = matches[0]
    digest = asset.get("digest", "")
    if not isinstance(digest, str) or not digest.startswith("sha256:"):
        raise UpdateError("Сервер не предоставил SHA-256 пакета. Обновление не установлено.")
    release = ReleaseInfo(version, asset.get("browser_download_url", ""), digest[7:].lower(), asset.get("size", 0), f"https://github.com/{REPOSITORY}/releases/tag/{tag}")
    _validate_release(release)
    return release


def _is_link(path: Path) -> bool:
    """Reject symbolic links AND Windows NTFS junctions/reparse directories."""
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


def _root(root: str | Path) -> Path:
    path = Path(root).absolute()
    if _is_link(path) or not path.is_dir():
        raise UpdateError("Папка установки отсутствует или является ссылкой.")
    return path.resolve()


def _local_dir(root: Path, *parts: str) -> Path:
    path = root
    for part in parts:
        path = path / part
        if _is_link(path):
            raise UpdateError("Служебная папка не должна быть символической ссылкой.")
        path.mkdir(exist_ok=True)
        if not path.is_dir() or not path.resolve().is_relative_to(root):
            raise UpdateError("Служебная папка находится вне установки.")
    return path


def _check_cancelled(cancelled: Callable[[], bool], deadline: float | None = None) -> None:
    if cancelled():
        raise UpdateError("Обновление отменено.")
    if deadline is not None and time.monotonic() > deadline:
        raise UpdateError("Превышено время загрузки обновления. Попробуйте ещё раз.")


def _sha256(path: Path, cancelled: Callable[[], bool] = lambda: False) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as source:
        while True:
            _check_cancelled(cancelled)
            chunk = source.read(CHUNK_SIZE)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def _verified_archive(path: Path, release: ReleaseInfo, cancelled: Callable[[], bool] = lambda: False) -> bool:
    return not _is_link(path) and path.is_file() and path.stat().st_size == release.size and _sha256(path, cancelled) == release.sha256.lower()


def download_update(root: str | Path, release: ReleaseInfo, *, opener: Callable = urlopen, cancelled: Callable[[], bool] = lambda: False) -> Path:
    """Download with bounded memory into root/cache/updates; reuse verified ZIPs."""
    _validate_release(release)
    _check_cancelled(cancelled)
    deadline = time.monotonic() + 15 * 60
    base = _root(root)
    cache = _local_dir(base, "cache", "updates")
    archive = cache / _release_name(release.version)
    partial = archive.with_suffix(".zip.part")
    if _is_link(archive) or _is_link(partial):
        raise UpdateError("Небезопасный путь к пакету обновления.")
    if _verified_archive(archive, release, cancelled):
        return archive
    archive.unlink(missing_ok=True)
    partial.unlink(missing_ok=True)
    try:
        free = shutil.disk_usage(cache).free
        if free < release.size + 64 * 1024 * 1024:
            raise UpdateError("Недостаточно места для загрузки обновления.")
        digest = hashlib.sha256()
        size = 0
        _check_cancelled(cancelled, deadline)
        with opener(_request(release.download_url), timeout=10) as response, partial.open("xb") as target:
            # HTTPResponse.read1 returns after at most one underlying read;
            # read(N) could keep waiting while a server slowly trickles bytes.
            # Rechecking the wall-clock deadline each chunk bounds that case.
            read_chunk = getattr(response, "read1", response.read)
            while True:
                _check_cancelled(cancelled, deadline)
                chunk = read_chunk(CHUNK_SIZE)
                _check_cancelled(cancelled, deadline)
                if not chunk:
                    break
                size += len(chunk)
                if size > release.size or size > MAX_DOWNLOAD:
                    raise UpdateError("Размер скачанного пакета не совпадает с релизом.")
                target.write(chunk)
                digest.update(chunk)
            _check_cancelled(cancelled, deadline)
            target.flush()
            os.fsync(target.fileno())
        if size != release.size or digest.hexdigest() != release.sha256.lower():
            raise UpdateError("Проверка пакета обновления не пройдена. Файлы программы не изменены.")
        os.replace(partial, archive)
        return archive
    except (OSError, URLError) as exc:
        raise UpdateError("Не удалось скачать обновление. Текущая версия сохранена.") from exc
    finally:
        partial.unlink(missing_ok=True)


def _safe_member(name: str) -> str:
    if not isinstance(name, str) or not name or any(character in name for character in '\\<>:"|?*') or any(ord(character) < 32 for character in name):
        raise UpdateError("Недопустимый путь внутри пакета.")
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in (".", "..", "") for part in name.rstrip("/").split("/")):
        raise UpdateError("Пакет пытается записать файлы вне папки программы.")
    for part in path.parts:
        if part.endswith((".", " ")) or part.split(".", 1)[0].upper() in _RESERVED_WINDOWS_NAMES:
            raise UpdateError("Пакет содержит недопустимое имя Windows.")
    normal = path.as_posix()
    if normal != PACKAGE_MANIFEST and path.parts[0] not in {"runtime", "app"}:
        raise UpdateError("Пакет содержит файлы вне runtime/ и app/.")
    return normal


def _read_json(path: Path, limit: int = 8 * MAX_METADATA) -> dict:
    if _is_link(path) or not path.is_file() or path.stat().st_size > limit:
        raise UpdateError("Некорректный манифест программы.")
    try:
        result = json.loads(path.read_bytes())
    except (OSError, ValueError) as exc:
        raise UpdateError("Не удалось прочитать манифест программы.") from exc
    if not isinstance(result, dict):
        raise UpdateError("Некорректный манифест программы.")
    return result


def _valid_manifest(manifest: dict, version: str) -> bool:
    return manifest.get("format") == 1 and manifest.get("application") == "ScriptoLib" and manifest.get("version") == version


def _discard_cached_archive(root: Path, archive: Path, version: str) -> None:
    expected = root / "cache" / "updates" / _release_name(version)
    # Only our exact downloaded path is ours to delete; externally supplied test
    # or installation ZIPs remain the caller's property.
    if archive.absolute() == expected and not _is_link(archive):
        archive.unlink(missing_ok=True)


def stage_update(root: str | Path, archive: str | Path, release: ReleaseInfo, *, cancelled: Callable[[], bool] = lambda: False) -> Path:
    """Verify/extract a package beside the current version without activating it.

    A package may include a ``files`` SHA-256 mapping; if present, it must match
    the exact payload. The trusted GitHub asset SHA-256 always covers the ZIP.
    """
    _validate_release(release)
    base = _root(root)
    source = Path(archive).absolute()
    versions = _local_dir(base, "versions")
    destination = versions / release.version
    if destination.exists() or _is_link(destination):
        _discard_cached_archive(base, source, release.version)
        raise UpdateError("Эта версия уже подготовлена. Повторная установка не требуется.")
    staging: Path | None = None
    try:
        _check_cancelled(cancelled)
        if not _verified_archive(source, release, cancelled):
            raise UpdateError("Контрольная сумма пакета не совпадает с релизом.")
        with zipfile.ZipFile(source) as package:
            entries = package.infolist()
            if len(entries) > MAX_ENTRIES:
                raise UpdateError("В пакете слишком много файлов.")
            total = 0
            names: set[str] = set()
            files: dict[str, zipfile.ZipInfo] = {}
            for entry in entries:
                _check_cancelled(cancelled)
                normal = _safe_member(entry.filename)
                key = normal.casefold()
                if key in names:
                    raise UpdateError("В пакете повторяются имена файлов.")
                names.add(key)
                mode = entry.external_attr >> 16
                if stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR)):
                    raise UpdateError("Ссылки и специальные файлы в пакете запрещены.")
                if entry.flag_bits & 1:
                    raise UpdateError("Зашифрованный пакет не поддерживается.")
                if not entry.is_dir():
                    total += entry.file_size
                    files[normal] = entry
                if total > MAX_UNPACKED or entry.file_size > MAX_DOWNLOAD:
                    raise UpdateError("Распакованный пакет слишком большой.")
            if not REQUIRED_FILES.issubset(files) or PACKAGE_MANIFEST not in files:
                raise UpdateError("Пакет не содержит полный запуск ScriptoLib.")
            if files[PACKAGE_MANIFEST].file_size > 8 * MAX_METADATA:
                raise UpdateError("Манифест пакета слишком большой.")
            manifest = json.loads(package.read(files[PACKAGE_MANIFEST]))
            _check_cancelled(cancelled)
            if not isinstance(manifest, dict) or not _valid_manifest(manifest, release.version):
                raise UpdateError("Манифест не соответствует версии обновления.")
            hashes = manifest.get("files")
            if hashes is not None:
                if not isinstance(hashes, dict) or set(hashes) != set(files) - {PACKAGE_MANIFEST}:
                    raise UpdateError("Список файлов пакета не совпадает с манифестом.")
                if any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", value) for value in hashes.values()):
                    raise UpdateError("Манифест содержит некорректные контрольные суммы.")
            if shutil.disk_usage(versions).free < total + 64 * 1024 * 1024:
                raise UpdateError("Недостаточно места для распаковки обновления.")
            staging = Path(tempfile.mkdtemp(prefix=f".stage-{release.version}-", dir=versions))
            # Record ownership before extraction so a killed process leaves an
            # identifiable staging folder that the next startup can clean.
            ownership = {"format": 1, "application": "ScriptoLib", "version": release.version, "files": sorted(files)}
            (staging / OWNERSHIP_MANIFEST).write_text(json.dumps(ownership, ensure_ascii=False), encoding="utf-8")
            (staging / OWNED_LIST).write_text("\n".join(sorted(set(files) | {OWNERSHIP_MANIFEST, OWNED_LIST})) + "\n", encoding="utf-8")
            for name, entry in files.items():
                _check_cancelled(cancelled)
                target = staging.joinpath(*PurePosixPath(name).parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                digest = hashlib.sha256()
                written = 0
                with package.open(entry) as incoming, target.open("xb") as outgoing:
                    while True:
                        _check_cancelled(cancelled)
                        chunk = incoming.read(CHUNK_SIZE)
                        _check_cancelled(cancelled)
                        if not chunk:
                            break
                        written += len(chunk)
                        if written > entry.file_size:
                            raise UpdateError("Размер файла пакета не совпадает с заголовком.")
                        outgoing.write(chunk)
                        digest.update(chunk)
                if hashes is not None and name != PACKAGE_MANIFEST and digest.hexdigest() != hashes[name].lower():
                    raise UpdateError("Контрольная сумма файла пакета не совпадает с манифестом.")
            _check_cancelled(cancelled)
            os.replace(staging, destination)
            staging = None
        return destination
    except (OSError, ValueError, zipfile.BadZipFile, RuntimeError) as exc:
        if isinstance(exc, UpdateError):
            raise
        raise UpdateError("Не удалось подготовить обновление. Текущая версия не изменена.") from exc
    finally:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)
        _discard_cached_archive(base, source, release.version)


def current_version(root: str | Path) -> str | None:
    base = _root(root)
    pointer = base / "current.txt"
    if _is_link(pointer):
        raise UpdateError("Указатель версии не должен быть ссылкой.")
    if not pointer.exists():
        return None
    if pointer.stat().st_size > 64:
        raise UpdateError("Некорректный указатель версии.")
    version = pointer.read_text(encoding="utf-8").strip()
    version_tuple(version)
    return version


def _owned_files(folder: Path, version: str) -> set[str]:
    own_path = folder / OWNERSHIP_MANIFEST
    if own_path.exists() or _is_link(own_path):
        manifest = _read_json(own_path)
        files = manifest.get("files")
        if not _valid_manifest(manifest, version) or not isinstance(files, list) or not all(isinstance(item, str) for item in files):
            raise UpdateError("Неизвестный владелец папки версии.")
        # Installer inventories may explicitly include their own two metadata
        # files. These names are allowed here, never in downloaded ZIP payloads.
        result = {item if item in {OWNERSHIP_MANIFEST, OWNED_LIST} else _safe_member(item) for item in files}
        result.add(OWNERSHIP_MANIFEST)
        result.add(OWNED_LIST)
    else:
        # Initial installer can use the build-time exact file/hash inventory.
        manifest = _read_json(folder / PACKAGE_MANIFEST)
        files = manifest.get("files")
        if not _valid_manifest(manifest, version) or not isinstance(files, dict):
            raise UpdateError("Отсутствует список файлов установленной версии.")
        result = {_safe_member(item) for item in files}
        result.add(PACKAGE_MANIFEST)
        result.add(OWNED_LIST)
    if not REQUIRED_FILES.issubset(result):
        raise UpdateError("Неполный список файлов установленной версии.")
    return result


def activate_version(root: str | Path, version: str) -> str | None:
    """Atomically switch current.txt. Caller must close the previous GUI first.

    Returns the previous version for rollback. Does not delete that version or
    mutate the user's data. The new app must acknowledge readiness before prune.
    """
    version_tuple(version)
    base = _root(root)
    versions = _local_dir(base, "versions")
    folder = versions / version
    if _is_link(folder) or not folder.is_dir():
        raise UpdateError("Подготовленная версия отсутствует.")
    _owned_files(folder, version)
    for name in REQUIRED_FILES:
        file = folder / name
        if _is_link(file) or not file.is_file() or not file.resolve().is_relative_to(folder.resolve()):
            raise UpdateError("Подготовленная версия неполна или содержит ссылки.")
    previous = current_version(base)
    temporary = base / "current.txt.new"
    if _is_link(temporary):
        raise UpdateError("Небезопасный временный указатель версии.")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as output:
            output.write(version + "\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, base / "current.txt")
    finally:
        temporary.unlink(missing_ok=True)
    return previous


def _delete_owned_folder(folder: Path, version: str) -> None:
    if _is_link(folder) or not folder.is_dir():
        raise UpdateError("папка является ссылкой или файлом")
    owned = _owned_files(folder, version)
    actual: set[str] = set()
    for parent, directories, files in os.walk(folder, followlinks=False):
        for name in directories + files:
            candidate = Path(parent) / name
            if _is_link(candidate) or not candidate.resolve().is_relative_to(folder.resolve()):
                raise UpdateError("папка содержит ссылку")
        actual.update((Path(parent) / name).relative_to(folder).as_posix() for name in files)
    if actual - owned:
        raise UpdateError("папка содержит пользовательские файлы")
    # Delete ownership records last. A lock retains enough metadata for retry.
    for name in sorted(actual - {OWNERSHIP_MANIFEST, PACKAGE_MANIFEST, OWNED_LIST}):
        (folder / name).unlink()
    for parent, _, _ in os.walk(folder, topdown=False):
        if Path(parent) != folder:
            Path(parent).rmdir()
    (folder / PACKAGE_MANIFEST).unlink(missing_ok=True)
    (folder / OWNERSHIP_MANIFEST).unlink(missing_ok=True)
    (folder / OWNED_LIST).unlink(missing_ok=True)
    folder.rmdir()


def cleanup_versions(root: str | Path, keep: Iterable[str] = ()) -> list[str]:
    """Prune only owned, inactive version directories; return deferred warnings.

    Call only AFTER the new GUI is ready. Unknown files (including user media),
    links, and locked files are preserved. A subsequent startup can retry locks.
    The current version is protected even if omitted from ``keep``.
    """
    base = _root(root)
    versions = _local_dir(base, "versions")
    protected = set(keep)
    active = current_version(base)
    if active:
        protected.add(active)
    else:
        return ["Очистка отложена: активная версия не определена."]
    warnings: list[str] = []
    for folder in versions.iterdir():
        if folder.name in protected or not VERSION_PATTERN.fullmatch(folder.name):
            continue
        try:
            _delete_owned_folder(folder, folder.name)
        except (OSError, UpdateError) as exc:
            warnings.append(f"Версия {folder.name}: очистка отложена ({exc}).")
    return warnings


def cleanup_staging(root: str | Path) -> list[str]:
    """Recover interrupted extraction at startup, before starting update work."""
    base = _root(root)
    versions = _local_dir(base, "versions")
    warnings: list[str] = []
    for folder in versions.iterdir():
        match = re.fullmatch(r"\.stage-([0-9]+\.[0-9]+\.[0-9]+)-[a-z0-9_]+", folder.name)
        if not match:
            continue
        try:
            version_tuple(match[1])
            _delete_owned_folder(folder, match[1])
        except (OSError, UpdateError) as exc:
            warnings.append(f"Временная версия {folder.name}: очистка отложена ({exc}).")
    return warnings


def cleanup_download_cache(root: str | Path, keep: Iterable[str] = ()) -> list[str]:
    """Remove owned ZIP/partial names at startup; never call during download.

    ``keep`` contains versions whose completed ZIP should remain for reuse.
    Arbitrary files and links are preserved. This also removes old packages
    after a crash between download and extraction without accumulating them.
    """
    base = _root(root)
    cache = _local_dir(base, "cache", "updates")
    protected = set(keep)
    warnings: list[str] = []
    for candidate in cache.iterdir():
        match = re.fullmatch(r"ScriptoLib-([0-9]+\.[0-9]+\.[0-9]+)-windows-x64\.zip(?:\.part)?", candidate.name)
        if not match or match[1] in protected:
            continue
        try:
            version_tuple(match[1])
            if _is_link(candidate) or not candidate.is_file():
                raise UpdateError("необычный тип файла")
            candidate.unlink()
        except (OSError, UpdateError) as exc:
            warnings.append(f"Пакет {candidate.name}: очистка отложена ({exc}).")
    return warnings
