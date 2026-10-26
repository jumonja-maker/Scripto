"""Qt-facing library actions. Slow imports and network work stay off the GUI thread."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from PySide6.QtCore import QObject, Property, QThread, QUrl, Signal, Slot

from . import __version__
from .library import LibraryStore

CATEGORY_LABELS = {"unassigned": "Не распределено", "film": "Фильм", "series": "Сериал",
                   "music": "Музыка", "video": "Видео"}


def format_size(size):
    for unit in ("Б", "КБ", "МБ", "ГБ", "ТБ"):
        if size < 1024 or unit == "ТБ":
            return (f"{size:.1f}".replace(".", ",") if unit != "Б" else str(int(size))) + " " + unit
        size /= 1024


class Task(QThread):
    result = Signal(object)
    failed = Signal(str)

    def __init__(self, function, parent=None):
        super().__init__(parent)
        self.function = function

    def run(self):
        try:
            self.result.emit(self.function())
        except Exception as exc:
            logging.exception("Background task failed")
            self.failed.emit(str(exc))


class LibraryController(QObject):
    changed = Signal()
    statusChanged = Signal()
    busyChanged = Signal()
    workingChanged = Signal()
    updateChanged = Signal()
    restartRequested = Signal(str)
    closeRequested = Signal()

    def __init__(self, paths, parent=None):
        super().__init__(parent)
        self.paths = paths
        self._items = []
        self._filter = "all"
        self._query = ""
        self._status = ""
        self._details = ""
        self._busy = False
        self._tasks = set()
        self._closing = False
        self._release = None
        self._update_busy = False
        self._update_status = "Проверка выполняется только по нажатию кнопки."
        self._settings = {}
        if paths.settings.exists():
            try:
                loaded = json.loads(paths.settings.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    self._settings = loaded
            except (OSError, ValueError):
                logging.warning("Cannot read settings; using defaults")
        self.refresh()

    @Property("QVariantList", notify=changed)
    def items(self):
        query = self._query.casefold().strip()
        return [item for item in self._items
                if (self._filter == "all" or item["category"] == self._filter)
                and (not query or query in item["title"].casefold())]

    @Property(int, notify=changed)
    def totalCount(self):
        return len(self._items)

    @Property(str, notify=changed)
    def totalSize(self):
        return format_size(sum(item["size_bytes"] for item in self._items))

    @Property(str, notify=changed)
    def category(self):
        return self._filter

    @Property(str, notify=statusChanged)
    def status(self):
        return self._status

    @Property(str, notify=statusChanged)
    def details(self):
        return self._details

    @Property(bool, notify=busyChanged)
    def busy(self):
        return self._busy

    @Property(bool, notify=workingChanged)
    def working(self):
        return bool(self._tasks)

    @Property(str, constant=True)
    def version(self):
        return __version__

    @Property(str, constant=True)
    def storageRoot(self):
        return str(self.paths.root)

    @Property(bool, notify=updateChanged)
    def updateBusy(self):
        return self._update_busy

    @Property(str, notify=updateChanged)
    def updateStatus(self):
        return self._update_status

    @Property(bool, notify=updateChanged)
    def updateAvailable(self):
        return self._release is not None

    @Property(bool, constant=True)
    def installed(self):
        return (self.paths.root / ".scriptolib-install.json").is_file()

    @Property(bool, notify=changed)
    def listView(self):
        return bool(self._settings.get("list_view", False))

    def _message(self, text, details=""):
        self._status, self._details = text, details
        self.statusChanged.emit()

    def _start(self, function, success, failure):
        task = Task(function, self)
        self._tasks.add(task)
        self.workingChanged.emit()
        task.result.connect(success)
        task.failed.connect(failure)
        task.finished.connect(lambda: self._finished(task))
        task.finished.connect(task.deleteLater)
        task.start()

    def _finished(self, task):
        self._tasks.discard(task)
        self.workingChanged.emit()
        if self._closing and not self._tasks:
            self.closeRequested.emit()

    @Slot()
    def requestClose(self):
        self._closing = True
        for task in self._tasks:
            task.requestInterruption()
        self._message("Завершаем текущую операцию…")
        if not self._tasks:
            self.closeRequested.emit()

    @Slot()
    def refresh(self):
        with LibraryStore(self.paths.database) as store:
            self._items = store.list_items()
        for item in self._items:
            item["sizeLabel"] = format_size(item["size_bytes"])
            item["categoryLabel"] = CATEGORY_LABELS[item["category"]]
        self.changed.emit()

    @Slot(str)
    def setCategory(self, category):
        if category in CATEGORY_LABELS or category == "all":
            self._filter = category
            self.changed.emit()

    @Slot(str)
    def setQuery(self, query):
        self._query = query
        self.changed.emit()

    @Slot(bool)
    def setListView(self, value):
        self._settings["list_view"] = value
        temporary = self.paths.settings.with_suffix(".tmp")
        try:
            temporary.write_text(json.dumps(self._settings), encoding="utf-8")
            temporary.replace(self.paths.settings)
        except OSError:
            self._message("Не удалось сохранить вид библиотеки.")
        self.changed.emit()

    @Slot("QVariantList")
    def importFiles(self, urls):
        if self._busy:
            return
        paths = [url.toLocalFile() if isinstance(url, QUrl) else QUrl(str(url)).toLocalFile()
                 for url in urls]
        if not paths:
            return
        self._busy = True
        self.busyChanged.emit()
        self._message("Добавляем файлы…")

        def import_batch():
            with LibraryStore(self.paths.database) as store:
                return store.add_files(paths)

        def done(report):
            self._busy = False
            self.busyChanged.emit()
            self.refresh()
            summary = f"Добавлено: {report.added}"
            if report.skipped:
                summary += f" · уже в библиотеке: {report.skipped}"
            if report.errors:
                summary += f" · не добавлено: {len(report.errors)}"
            self._message(summary, "\n".join(f"{e.path}: {e.reason}" for e in report.errors))

        def failed(message):
            self._busy = False
            self.busyChanged.emit()
            self._message("Не удалось добавить файлы.", message)

        self._start(import_batch, done, failed)

    @Slot(int, result="QVariantMap")
    def inspectItem(self, item_id):
        item = next((dict(i) for i in self._items if i["id"] == item_id), {})
        if item:
            try:
                item["available"] = Path(item["path"]).is_file()
            except OSError:
                item["available"] = False
        return item

    @Slot(int, str)
    def assignCategory(self, item_id, category):
        try:
            with LibraryStore(self.paths.database) as store:
                store.update_category(item_id, category)
            self.refresh()
        except (OSError, ValueError) as exc:
            self._message("Не удалось изменить раздел.", str(exc))

    @Slot(int)
    def removeItem(self, item_id):
        with LibraryStore(self.paths.database) as store:
            store.remove_item(item_id)
        self.refresh()
        self._message("Запись убрана из библиотеки. Файл остался на диске.")

    @Slot()
    def checkUpdates(self):
        if self._update_busy:
            return
        from .updates import check_release
        self._update_busy = True
        self._release = None
        self._update_status = "Проверяем обновления…"
        self.updateChanged.emit()

        def done(release):
            self._release = release
            self._update_busy = False
            self._update_status = (f"Доступна версия {release.version}." if release else
                                   "Новых опубликованных версий пока нет.")
            self.updateChanged.emit()

        def failed(message):
            self._update_busy = False
            self._update_status = "Не удалось проверить обновления. Библиотека работает офлайн."
            self.updateChanged.emit()
        self._start(lambda: check_release(__version__), done, failed)

    @Slot()
    def installUpdate(self):
        if self._update_busy or not self._release or not self.installed:
            return
        from .updates import download_update, stage_update
        release = self._release
        self._update_busy = True
        self._update_status = "Скачиваем и проверяем обновление…"
        self.updateChanged.emit()

        def prepare():
            cancelled = QThread.currentThread().isInterruptionRequested
            archive = download_update(self.paths.root, release, cancelled=cancelled)
            return stage_update(self.paths.root, archive, release, cancelled=cancelled)

        def done(_):
            self._update_busy = False
            self._update_status = "Обновление проверено. Перезапускаем ScriptoLib…"
            self.updateChanged.emit()
            self.restartRequested.emit(release.version)

        def failed(message):
            self._update_busy = False
            self._update_status = "Обновление не установлено. Текущая версия сохранена. " + message
            self.updateChanged.emit()
        self._start(prepare, done, failed)

    def shutdown(self):
        for task in list(self._tasks):
            task.wait()

