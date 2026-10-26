"""Out-of-process activation with acknowledgement and rollback.

No Qt imports. Run using the OLD bundled Python after the GUI has quit. All
transaction records and logs are installation-local. Startup recovery is
explicit so an interrupted update never silently chooses an untested version.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import time
from typing import Callable

from .updates import (
    UpdateError, _is_link, _local_dir, _read_json, _root, activate_version,
    cleanup_download_cache, cleanup_staging, cleanup_versions,
    current_version, version_tuple,
)


PENDING_FILE = "update-pending.json"
LOCK_FILE = "update-helper.lock"
TOKEN_PATTERN = re.compile(r"[0-9a-f]{32}\Z")


@dataclass(frozen=True)
class UpdateResult:
    success: bool
    rolled_back: bool
    message: str


def _cache(root: str | Path) -> Path:
    return _local_dir(_root(root), "cache")


def _token(token: str) -> str:
    if not isinstance(token, str) or not TOKEN_PATTERN.fullmatch(token):
        raise UpdateError("Некорректный идентификатор обновления.")
    return token


def _write_json(path: Path, payload: dict) -> None:
    temporary = path.with_name(path.name + ".new")
    if _is_link(path) or _is_link(temporary):
        raise UpdateError("Служебный файл обновления не должен быть ссылкой.")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as target:
            json.dump(payload, target, ensure_ascii=False)
            target.write("\n")
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _pending(root: str | Path) -> dict | None:
    path = _cache(root) / PENDING_FILE
    if not path.exists() and not _is_link(path):
        return None
    payload = _read_json(path, 4096)
    if payload.get("format") != 1 or payload.get("application") != "ScriptoLib":
        raise UpdateError("Неизвестная операция обновления.")
    _token(payload.get("token"))
    version_tuple(payload.get("old_version"))
    version_tuple(payload.get("new_version"))
    for field in ("parent_pid", "helper_pid", "child_pid"):
        value = payload.get(field)
        if value is not None and (type(value) is not int or value <= 0):
            raise UpdateError("Некорректный процесс обновления.")
    return payload


def prepare_restart(root: str | Path, version: str, *, parent_pid: int | None = None) -> str:
    """Call under the GUI app.lock BEFORE closing it. Returns helper CLI token."""
    version_tuple(version)
    base = _root(root)
    old = current_version(base)
    if old is None or old == version:
        raise UpdateError("Для перезапуска нужна другая установленная версия.")
    target = base / "versions" / version
    if _is_link(target) or not target.is_dir():
        raise UpdateError("Новая версия ещё не подготовлена.")
    if _pending(base) is not None:
        raise UpdateError("Обновление уже выполняется.")
    token = secrets.token_hex(16)
    _write_json(_cache(base) / PENDING_FILE, {
        "format": 1, "application": "ScriptoLib", "token": token,
        "old_version": old, "new_version": version,
        "parent_pid": parent_pid if parent_pid is not None else os.getpid(),
        "helper_pid": None, "status": "scheduled",
    })
    return token


def launch_allowed(root: str | Path, token: str | None = None) -> bool:
    """Call before GUI/app.lock startup; only the update's own launch may enter."""
    pending = _pending(root)
    return pending is None or (token is not None and token == pending["token"])


def register_update_child(root: str | Path, token: str, *, pid: int | None = None) -> None:
    """Call from bootstrap before heavy Qt imports to record the child's PID.

    Both helper and child write the same PID. This shrinks the spawn/record crash
    window; any remaining ambiguous window is handled conservatively below.
    """
    pending = _pending(root)
    if pending is None or pending["token"] != _token(token):
        raise UpdateError("Запуск не соответствует операции обновления.")
    child_pid = pid if pid is not None else os.getpid()
    if type(child_pid) is not int or child_pid <= 0:
        raise UpdateError("Некорректный процесс новой версии.")
    pending["child_pid"] = child_pid
    _write_json(_cache(root) / PENDING_FILE, pending)


def _ready_path(root: str | Path, token: str) -> Path:
    return _cache(root) / f"update-ready-{_token(token)}.json"


def mark_update_ready(root: str | Path, token: str, version: str) -> None:
    """Call after successful DB open + QML window load + first event-loop turn."""
    version_tuple(version)
    pending = _pending(root)
    if pending is None or pending["token"] != _token(token) or version not in {pending["old_version"], pending["new_version"]}:
        raise UpdateError("Запуск не соответствует текущему обновлению.")
    if current_version(root) != version:
        raise UpdateError("Запущенная версия не соответствует указателю программы.")
    _write_json(_ready_path(root, token), {"token": token, "version": version, "pid": os.getpid()})


def _has_ready(root: str | Path, token: str, version: str, pid: int | None = None) -> bool:
    path = _ready_path(root, token)
    if not path.exists() or _is_link(path):
        return False
    try:
        ready = _read_json(path, 4096)
    except UpdateError:
        return False
    return ready.get("token") == token and ready.get("version") == version and (pid is None or ready.get("pid") == pid)


def process_alive(pid: int | None) -> bool:
    if pid is None or pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
        if not handle:
            # Access denied is treated as alive; never race an unknown process.
            return ctypes.get_last_error() == 5
        try:
            return kernel32.WaitForSingleObject(handle, 0) == 0x00000102
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def wait_parent(pid: int, timeout: float = 60.0) -> bool:
    deadline = time.monotonic() + timeout
    while process_alive(pid):
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.1)
    return True


def spawn_version(root: str | Path, version: str, token: str):
    base = _root(root)
    version_tuple(version)
    _token(token)
    folder = base / "versions" / version
    executable = folder / "runtime" / "pythonw.exe"
    bootstrap = folder / "app" / "bootstrap.py"
    for path in (executable, bootstrap):
        if _is_link(path) or not path.is_file() or not path.resolve().is_relative_to(folder.resolve()):
            raise UpdateError("Подготовленная версия не содержит безопасный запуск.")
    temporary = _local_dir(base, "cache", "temp")
    environment = os.environ.copy()
    environment.update(TEMP=str(temporary), TMP=str(temporary), PYTHONDONTWRITEBYTECODE="1")
    return subprocess.Popen(
        [str(executable), "-B", str(bootstrap), "--root", str(base), "--update-token", token],
        cwd=base, env=environment,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )


def await_ready(root: str | Path, token: str, version: str, child, timeout: float = 45.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if child.poll() is not None:
            return False
        if _has_ready(root, token, version, child.pid):
            return True
        time.sleep(0.1)
    return False


def _stop_child(child) -> None:
    if child is None or child.poll() is not None:
        return
    child.terminate()
    try:
        child.wait(timeout=5)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait(timeout=5)


def _clear_transaction(root: str | Path, token: str) -> None:
    pending = _pending(root)
    if pending is not None and pending["token"] != token:
        raise UpdateError("Нельзя удалить чужую операцию обновления.")
    cache = _cache(root)
    for path in (_ready_path(root, token), cache / PENDING_FILE, cache / LOCK_FILE):
        if _is_link(path):
            raise UpdateError("Служебный файл обновления является ссылкой.")
        path.unlink(missing_ok=True)


def run_update(
    root: str | Path, version: str, parent_pid: int, token: str, *,
    wait_for_parent: Callable = wait_parent, spawn: Callable = spawn_version,
    ready: Callable = await_ready,
) -> UpdateResult:
    """Activation transaction. Injectable process operations support real tests."""
    base = _root(root)
    version_tuple(version)
    _token(token)
    pending = _pending(base)
    if pending is None or pending["token"] != token or pending["new_version"] != version or pending["parent_pid"] != parent_pid:
        raise UpdateError("Операция обновления не соответствует запросу.")
    lock = _cache(base) / LOCK_FILE
    if _is_link(lock):
        raise UpdateError("Небезопасный файл блокировки обновления.")
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise UpdateError("Другой процесс уже устанавливает обновление.") from exc
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump({"pid": os.getpid(), "token": token}, stream)
    old = pending["old_version"]
    child = None
    changed = False
    parent_stopped = False
    try:
        pending.update(helper_pid=os.getpid(), status="waiting")
        _write_json(_cache(base) / PENDING_FILE, pending)
        if not wait_for_parent(parent_pid, 60.0):
            _clear_transaction(base, token)
            return UpdateResult(False, False, "Обновление отменено: программа ещё открыта.")
        parent_stopped = True
        if current_version(base) != old:
            raise UpdateError("Активная версия изменилась во время обновления.")
        activate_version(base, version)
        changed = True
        pending["status"] = "starting"
        _write_json(_cache(base) / PENDING_FILE, pending)
        child = spawn(base, version, token)
        pending["child_pid"] = child.pid
        _write_json(_cache(base) / PENDING_FILE, pending)
        if not ready(base, token, version, child, 45.0):
            raise UpdateError("Новая версия не подтвердила успешный запуск.")
        pending["status"] = "ready"
        _write_json(_cache(base) / PENDING_FILE, pending)
        return UpdateResult(True, False, "Обновление установлено.")
    except (OSError, UpdateError) as exc:
        # Do not change pointer while a failed new process could still write.
        try:
            _stop_child(child)
        except (OSError, subprocess.TimeoutExpired) as stop_error:
            pending["status"] = "recovery_required"
            _write_json(_cache(base) / PENDING_FILE, pending)
            return UpdateResult(False, False, f"Не удалось остановить новую версию: {stop_error}. Перезапуск отложен.")
        if changed or (parent_stopped and current_version(base) == old):
            try:
                activate_version(base, old)
                pending["status"] = "rolling_back"
                _write_json(_cache(base) / PENDING_FILE, pending)
                _ready_path(base, token).unlink(missing_ok=True)
                child = spawn(base, old, token)
                pending["child_pid"] = child.pid
                _write_json(_cache(base) / PENDING_FILE, pending)
                if not ready(base, token, old, child, 45.0):
                    _stop_child(child)
                    pending["status"] = "rolled_back"
                    _write_json(_cache(base) / PENDING_FILE, pending)
                    return UpdateResult(False, True, f"Предыдущая версия восстановлена, но её запуск не подтверждён: {exc}")
                pending["status"] = "rolled_back"
                _write_json(_cache(base) / PENDING_FILE, pending)
                return UpdateResult(False, True, f"Обновление не запустилось. Предыдущая версия восстановлена: {exc}")
            except (OSError, UpdateError, subprocess.TimeoutExpired) as rollback_error:
                pending["status"] = "recovery_required"
                _write_json(_cache(base) / PENDING_FILE, pending)
                return UpdateResult(False, False, f"Нужен повторный запуск для восстановления: {rollback_error}")
        _clear_transaction(base, token)
        return UpdateResult(False, False, f"Обновление не установлено: {exc}")
    finally:
        lock.unlink(missing_ok=True)


def finish_or_recover(root: str | Path, *, alive: Callable = process_alive, confirm_no_app_processes: bool = False) -> list[str]:
    """Call at startup / periodically AFTER helper exit, before ordinary launch.

    If helper/old parent still runs, this leaves the transaction untouched.
    Otherwise finalize a ready version, or restore the previous pointer after a
    crashed helper. It does NOT start processes. The caller can restart itself
    if recovery changed current.txt away from its executing version.
    """
    base = _root(root)
    pending = _pending(base)
    if pending is None:
        return []
    if alive(pending.get("helper_pid")) or (pending.get("status") == "scheduled" and alive(pending.get("parent_pid"))):
        return []
    token = pending["token"]
    active = current_version(base)
    messages: list[str] = []
    if pending.get("status") in {"starting", "rolling_back", "recovery_required"} and pending.get("child_pid") is None and not _has_ready(base, token, active or "") and not confirm_no_app_processes:
        return ["Восстановление приостановлено: процесс запуска не успел зарегистрироваться. Версии и медиа сохранены. Для восстановления нужно подтвердить завершение всех процессов ScriptoLib."]
    if alive(pending.get("child_pid")) and pending.get("status") not in {"ready", "rolled_back"} and not _has_ready(base, token, active or ""):
        return ["Очистка обновления отложена: запущенная версия ещё не подтвердила готовность."]
    if active == pending["new_version"]:
        if pending.get("status") == "ready" or _has_ready(base, token, pending["new_version"]):
            messages.append("Обновление завершено.")
        else:
            activate_version(base, pending["old_version"])
            messages.append("Незавершённое обновление отменено; восстановлена предыдущая версия.")
    elif active != pending["old_version"]:
        raise UpdateError("Невозможно автоматически определить версию для восстановления.")
    _clear_transaction(base, token)
    messages.extend(cleanup_versions(base))
    messages.extend(cleanup_staging(base))
    messages.extend(cleanup_download_cache(base))
    return messages


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ScriptoLib update helper")
    parser.add_argument("--root", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--parent-pid", required=True, type=int)
    parser.add_argument("--token", required=True)
    args = parser.parse_args(argv)
    try:
        result = run_update(args.root, args.version, args.parent_pid, args.token)
        # One small overwritten status file, not an ever-growing log.
        _write_json(_cache(args.root) / "update-result.json", {
            "success": result.success, "rolled_back": result.rolled_back, "message": result.message,
        })
        return 0 if result.success else 1
    except (OSError, UpdateError) as exc:
        try:
            _write_json(_cache(args.root) / "update-result.json", {"success": False, "rolled_back": False, "message": str(exc)})
        except (OSError, UpdateError):
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
