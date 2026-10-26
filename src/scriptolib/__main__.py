"""Configure local storage before loading Qt or application services."""
from __future__ import annotations

import argparse
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import sys


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="ScriptoLib")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--smoke-test", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--screenshot", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--update-token", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    from .paths import AppPaths, AppPathError, prepare_environment
    try:
        if (args.root / ".scriptolib-maintenance.lock").exists():
            raise AppPathError("ScriptoLib сейчас удаляется. Дождитесь завершения операции.")
        paths = prepare_environment(AppPaths(args.root))
        if args.screenshot and not args.screenshot.resolve().is_relative_to(paths.root.resolve()):
            raise AppPathError("Снимок окна разрешён только внутри папки ScriptoLib.")
        if args.update_token:
            from .update_worker import register_update_child
            register_update_child(paths.root, args.update_token)
    except (AppPathError, OSError) as exc:
        if sys.platform == "win32":
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, str(exc), "ScriptoLib", 0x10)
        else:
            print(str(exc), file=sys.stderr)
        return 1
    handler = RotatingFileHandler(paths.data / "scriptolib.log", maxBytes=512_000,
                                  backupCount=1, encoding="utf-8")
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    try:
        from .app import run
        return run(paths, args)
    except Exception as exc:
        logging.exception("Application startup failed")
        if sys.platform == "win32":
            import ctypes
            ctypes.windll.user32.MessageBoxW(None,
                str(exc) + "\n\nПодробности: data\\scriptolib.log в папке программы.",
                "ScriptoLib", 0x10)
        else:
            logging.exception("Startup error")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

