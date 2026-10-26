from __future__ import annotations

from pathlib import Path
import logging
import os
import subprocess
import sys

from PySide6.QtCore import QLockFile, QSettings, QTimer, QUrl
from PySide6.QtGui import QFont, QFontDatabase, QGuiApplication, QIcon
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle

from .controller import LibraryController


def run(paths, args):
    from . import __version__
    from .update_worker import (finish_or_recover, launch_allowed, mark_update_ready,
                                prepare_restart)
    from .updates import current_version, cleanup_staging, cleanup_download_cache, cleanup_versions
    installed = (paths.root / ".scriptolib-install.json").is_file()
    update_token = getattr(args, "update_token", None)
    if installed:
        if not update_token:
            finish_or_recover(paths.root)
        if not launch_allowed(paths.root, update_token):
            raise RuntimeError("Обновление ScriptoLib уже выполняется. Дождитесь перезапуска.")
        active = current_version(paths.root)
        if active != __version__:
            try:
                executing_version = Path(__file__).resolve().relative_to(paths.root / "versions").parts[0]
            except ValueError:
                executing_version = None
            if executing_version == active or executing_version is None:
                raise RuntimeError("Номер версии пакета не совпадает с кодом приложения. Пакет повреждён.")
            # Recovery can restore current.txt while the old launcher had already
            # selected a different runtime. Re-enter through the stable launcher.
            subprocess.Popen([str(paths.root / "ScriptoLib.exe")], cwd=paths.root)
            return 0
    app = QGuiApplication(["ScriptoLib"])
    app.setApplicationName("ScriptoLib")
    app.setOrganizationName("ScriptoLib")
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(paths.data / "config"))
    QSettings.setPath(QSettings.IniFormat, QSettings.SystemScope, str(paths.data / "config"))
    QQuickStyle.setStyle("Basic")
    package = Path(__file__).resolve().parent
    QFontDatabase.addApplicationFont(str(package / "assets" / "Manrope.ttf"))
    app.setFont(QFont("Manrope", 10))
    app.setWindowIcon(QIcon(str(package / "assets" / "icons" / "logo.svg")))
    lock = QLockFile(str(paths.data / "app.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(0):
        raise RuntimeError("ScriptoLib уже запущен из этой папки.")
    if (paths.root / ".scriptolib-maintenance.lock").exists():
        lock.unlock()
        raise RuntimeError("ScriptoLib сейчас удаляется.")
    if installed:
        from .paths import cleanup_setup
        cleanup_setup(paths)
    engine = QQmlApplicationEngine()
    controller = LibraryController(paths)
    engine.rootContext().setContextProperty("library", controller)
    engine.load(QUrl.fromLocalFile(str(package / "qml" / "Main.qml")))
    if not engine.rootObjects():
        lock.unlock()
        return 1
    window = engine.rootObjects()[0]
    controller.closeRequested.connect(app.quit)
    pending_restart = []

    def schedule_restart(version):
        try:
            token = prepare_restart(paths.root, version)
        except Exception as exc:
            controller._update_status = "Не удалось подготовить перезапуск. " + str(exc)
            controller.updateChanged.emit()
            return
        pending_restart[:] = [version, token]
        app.quit()

    controller.restartRequested.connect(schedule_restart)
    app.aboutToQuit.connect(controller.shutdown)
    maintenance = QTimer(app)
    maintenance.setInterval(2000)
    def finish_update():
        if controller.working:
            return
        try:
            finish_or_recover(paths.root)
            if launch_allowed(paths.root):
                cleanup_staging(paths.root)
                cleanup_download_cache(paths.root)
                cleanup_versions(paths.root)
                maintenance.stop()
        except Exception:
            logging.exception("Update cleanup deferred")
            maintenance.stop()
    if installed:
        if update_token:
            def acknowledge():
                try:
                    mark_update_ready(paths.root, update_token, __version__)
                except Exception:
                    logging.exception("Cannot acknowledge update; requesting rollback")
                    app.quit()
            QTimer.singleShot(0, acknowledge)
        maintenance.start()
    if args.smoke_test or args.screenshot:
        def capture():
            if args.screenshot:
                window.grabWindow().save(str(args.screenshot))
            app.quit()
        QTimer.singleShot(1200, capture)
    result = app.exec()
    lock.unlock()
    if pending_restart:
        version, token = pending_restart
        try:
            subprocess.Popen([sys.executable, "-B", "-m", "scriptolib.update_worker",
                              "--root", str(paths.root), "--version", version,
                              "--parent-pid", str(os.getpid()), "--token", token],
                             cwd=paths.root,
                             creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        except OSError:
            logging.exception("Cannot start update worker; next start will recover")
    return result

