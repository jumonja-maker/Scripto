"""Offscreen integration tests for the real QML window and asynchronous actions."""

import os
from pathlib import Path
import sys
import tempfile
import threading
import time

import pytest

from scriptolib.paths import AppPaths, prepare_environment


@pytest.mark.parametrize("value", ["[]", "null", '"text"'])
def test_non_object_settings_use_defaults(qt_app, tmp_path, value):
    from scriptolib.controller import LibraryController
    paths = AppPaths(tmp_path / "ScriptoLib").initialize()
    paths.settings.write_text(value, encoding="utf-8")
    controller = LibraryController(paths)
    assert controller.listView is False
    controller.setListView(True)
    assert controller.listView is True


@pytest.fixture(scope="session")
def qt_app(tmp_path_factory):
    # Configure storage before importing PySide6, as the real bootstrap does.
    patch = pytest.MonkeyPatch()
    for name in (
        "TEMP", "TMP", "TMPDIR", "QML_DISK_CACHE_PATH", "XDG_CACHE_HOME",
        "XDG_DATA_HOME", "XDG_CONFIG_HOME", "PYTHONPYCACHEPREFIX",
        "PYTHONDONTWRITEBYTECODE",
    ):
        patch.setenv(name, os.environ.get(name, ""))
    patch.setenv("QT_QPA_PLATFORM", "offscreen")
    patch.setenv("QT_QUICK_BACKEND", "software")
    patch.setattr(sys, "dont_write_bytecode", sys.dont_write_bytecode)
    patch.setattr(sys, "pycache_prefix", sys.pycache_prefix)
    patch.setattr(tempfile, "tempdir", tempfile.tempdir)
    prepare_environment(AppPaths(tmp_path_factory.mktemp("qt-environment")))

    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQuickControls2 import QQuickStyle

    QQuickStyle.setStyle("Basic")
    app = QGuiApplication.instance() or QGuiApplication(["ScriptoLib GUI tests"])
    app.setQuitOnLastWindowClosed(False)
    yield app
    app.processEvents()
    patch.undo()


def wait_until(predicate, timeout=4000):
    from PySide6.QtTest import QTest

    deadline = time.monotonic() + timeout / 1000
    while not predicate():
        if time.monotonic() >= deadline:
            pytest.fail("Timed out while processing Qt events")
        QTest.qWait(10)


@pytest.fixture
def controller(qt_app, tmp_path):
    from PySide6.QtCore import QCoreApplication, QEvent
    from scriptolib.controller import LibraryController

    value = LibraryController(AppPaths(tmp_path / "ScriptoLib").initialize())
    yield value
    value.shutdown()
    wait_until(lambda: not value._tasks)
    value.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qt_app.processEvents()


@pytest.fixture
def ui(qt_app, controller):
    from PySide6.QtCore import QCoreApplication, QEvent, QUrl
    from PySide6.QtQml import QQmlApplicationEngine
    # Register the QQuickWindow wrapper before rootObjects() is converted.
    from PySide6.QtQuick import QQuickWindow
    import scriptolib

    messages = []
    engine = QQmlApplicationEngine()
    engine.warnings.connect(lambda warnings: messages.extend(error.toString() for error in warnings))
    engine.rootContext().setContextProperty("library", controller)
    path = Path(scriptolib.__file__).parent / "qml" / "Main.qml"
    engine.load(QUrl.fromLocalFile(str(path)))
    roots = engine.rootObjects()
    assert roots, "Main.qml failed to load: " + "\n".join(messages)
    window = roots[0]
    assert isinstance(window, QQuickWindow)
    wait_until(window.isExposed)
    try:
        yield engine, window, messages
    finally:
        window.close()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        qt_app.processEvents()


def click(window, item):
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtTest import QTest

    assert item is not None
    assert item.isVisible()
    assert item.isEnabled()
    point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint()
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point)


def test_async_import_signals_filters_persistence_and_safe_removal(controller, tmp_path, monkeypatch):
    from PySide6.QtCore import QUrl
    from PySide6.QtTest import QSignalSpy
    from scriptolib.controller import LibraryController

    video = tmp_path / "Небо над нами.MKV"
    audio = tmp_path / "Моя песня.FLAC"
    unsupported = tmp_path / "notes.txt"
    for path in (video, audio, unsupported):
        path.write_bytes(b"catalogue fixture; decoding is outside this milestone")
    missing = tmp_path / "missing.mp4"
    busy_changes = QSignalSpy(controller.busyChanged)
    status_changes = QSignalSpy(controller.statusChanged)
    item_changes = QSignalSpy(controller.changed)
    refresh_threads = []
    original_refresh = controller.refresh

    def record_refresh():
        refresh_threads.append(threading.get_ident())
        return original_refresh()

    monkeypatch.setattr(controller, "refresh", record_refresh)
    main_thread = threading.get_ident()
    controller.importFiles([QUrl.fromLocalFile(str(path)) for path in (
        missing, video, unsupported, audio, video,
    )])
    assert controller.busy
    wait_until(lambda: not controller.busy)
    wait_until(lambda: not controller._tasks)
    assert busy_changes.count() == 2
    assert status_changes.count() >= 2
    assert item_changes.count() >= 1
    assert refresh_threads == [main_thread], "Import completion changed UI state outside the GUI thread"
    assert controller.totalCount == 2
    assert "Добавлено: 2" in controller.status
    assert "уже в библиотеке: 1" in controller.status
    assert "не добавлено: 2" in controller.status
    assert "missing.mp4" in controller.details
    assert "notes.txt" in controller.details

    controller.setQuery("  МОЯ ПЕСНЯ  ")
    assert [item["title"] for item in controller.items] == ["Моя песня"]
    controller.setCategory("unassigned")
    assert controller.items == []
    controller.setQuery("")
    [movie] = controller.items
    controller.assignCategory(movie["id"], "film")
    assert controller.items == []
    controller.setCategory("film")
    assert [item["id"] for item in controller.items] == [movie["id"]]
    controller.setListView(True)

    reopened = LibraryController(controller.paths)
    try:
        assert reopened.totalCount == 2
        assert reopened.listView is True
        reopened.setCategory("film")
        assert reopened.items[0]["id"] == movie["id"]
        assert reopened.inspectItem(movie["id"])["available"] is True
        reopened.removeItem(movie["id"])
        assert reopened.totalCount == 1
        assert video.is_file()
        assert video.read_bytes().startswith(b"catalogue fixture")
    finally:
        reopened.shutdown()
        reopened.deleteLater()


@pytest.mark.parametrize("size", [(900, 640), (1440, 1000)])
def test_real_qml_renders_and_settings_are_reachable(ui, controller, size):
    from PySide6.QtQuick import QQuickItem
    from PySide6.QtTest import QTest

    _, window, messages = ui
    window.resize(*size)
    QTest.qWait(80)
    assert (window.width(), window.height()) == size
    screenshot = window.grabWindow()
    assert not screenshot.isNull(), "QML window did not render"
    assert screenshot.width() >= size[0]
    assert screenshot.height() >= size[1]
    search = window.findChild(QQuickItem, "librarySearch")
    import_button = window.findChild(QQuickItem, "importButton")
    assert search.isVisible() and import_button.isVisible()
    for control in (search, import_button):
        position = control.mapToScene(control.boundingRect().topLeft())
        assert position.x() >= 76
        assert position.x() + control.width() <= window.width()

    click(window, window.findChild(QQuickItem, "settingsButton"))
    wait_until(lambda: bool(window.property("settingsOpen")))
    check = window.findChild(QQuickItem, "checkUpdatesButton")
    assert check.isVisible() and check.isEnabled()
    assert check.property("text") == "Проверить обновления"
    assert controller.updateBusy is False
    assert messages == [], "QML warnings/errors: " + "\n".join(messages)


def test_imported_items_render_search_and_open_from_actual_qml(ui, controller, tmp_path):
    from PySide6.QtCore import QPointF, Qt, QUrl
    from PySide6.QtQuick import QQuickItem
    from PySide6.QtTest import QTest

    video = tmp_path / "Мой фильм.mkv"
    audio = tmp_path / "Моя песня.mp3"
    video.write_bytes(b"catalogue video fixture")
    audio.write_bytes(b"catalogue audio fixture")
    _, window, messages = ui
    controller.importFiles([QUrl.fromLocalFile(str(path)) for path in (video, audio)])
    wait_until(lambda: not controller.busy)
    grid = window.findChild(QQuickItem, "libraryGrid")
    rows = window.findChild(QQuickItem, "libraryList")
    search = window.findChild(QQuickItem, "librarySearch")
    wait_until(lambda: grid.property("count") == 2)
    QTest.qWait(40)
    assert grid.isVisible()
    assert not window.grabWindow().isNull()

    search.setProperty("text", "ПЕСНЯ")
    wait_until(lambda: grid.property("count") == 1)
    assert controller.items[0]["title"] == "Моя песня"
    search.setProperty("text", "нет такого файла")
    wait_until(lambda: grid.property("count") == 0)
    search.setProperty("text", "")
    controller.setListView(True)
    wait_until(lambda: rows.isVisible() and rows.property("count") == 2)
    QTest.qWait(40)
    point = rows.mapToScene(QPointF(rows.width() / 2, 30)).toPoint()
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point)

    def selection():
        value = window.property("currentItem")
        return value.toVariant() if hasattr(value, "toVariant") else value

    wait_until(lambda: bool(selection().get("title")))
    assert selection()["title"] == "Моя песня"
    assert selection()["available"] is True
    assert messages == [], "QML warnings/errors: " + "\n".join(messages)


@pytest.mark.parametrize("outcome", ["none", "offline", "new"])
def test_update_button_uses_worker_and_keeps_gui_responsive(ui, controller, monkeypatch, outcome):
    from PySide6.QtCore import QTimer
    from PySide6.QtQuick import QQuickItem
    from PySide6.QtTest import QSignalSpy
    from scriptolib import updates

    started = threading.Event()
    finish = threading.Event()
    worker_threads = []

    def mocked_check(current_version):
        assert current_version == controller.version
        worker_threads.append(threading.get_ident())
        started.set()
        if not finish.wait(timeout=3):
            raise RuntimeError("Test worker was not released")
        if outcome == "offline":
            raise updates.UpdateError("Network unavailable in test")
        if outcome == "none":
            return None
        return updates.ReleaseInfo(
            "0.2.0", "https://github.com/jumonja-maker/Scripto/releases/download/v0.2.0/ScriptoLib-0.2.0-windows-x64.zip",
            "a" * 64, 1, "https://github.com/jumonja-maker/Scripto/releases/tag/v0.2.0",
        )

    monkeypatch.setattr(updates, "check_release", mocked_check)
    _, window, messages = ui
    click(window, window.findChild(QQuickItem, "settingsButton"))
    wait_until(lambda: bool(window.property("settingsOpen")))
    button = window.findChild(QQuickItem, "checkUpdatesButton")
    changes = QSignalSpy(controller.updateChanged)
    ticks = []
    timer = QTimer()
    timer.setInterval(10)
    timer.timeout.connect(lambda: ticks.append(True))
    try:
        click(window, button)
        assert controller.updateBusy
        assert not button.isEnabled()
        wait_until(started.is_set)
        timer.start()
        wait_until(lambda: len(ticks) >= 3)
        assert controller.updateBusy, "Blocked network mock should still be in progress"
        assert len(worker_threads) == 1
        assert worker_threads[0] != threading.get_ident()
    finally:
        finish.set()
        timer.stop()
    wait_until(lambda: not controller.updateBusy)
    wait_until(lambda: not controller._tasks)
    assert button.isEnabled()
    assert changes.count() >= 2
    assert controller.updateAvailable is (outcome == "new")
    expected = {
        "none": "Новых опубликованных версий пока нет.",
        "offline": "Не удалось проверить обновления. Библиотека работает офлайн.",
        "new": "Доступна версия 0.2.0.",
    }
    assert controller.updateStatus == expected[outcome]
    assert messages == [], "QML warnings/errors: " + "\n".join(messages)
