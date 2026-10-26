import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import QtQuick.Dialogs

ApplicationWindow {
    id: window
    objectName: "mainWindow"
    visible: true
    width: 1440
    height: 1000
    minimumWidth: 900
    minimumHeight: 640
    title: "ScriptoLib"
    color: Theme.background
    property bool settingsOpen: false
    property var currentItem: ({})
    onClosing: function(close) {
        if (library.working) {
            close.accepted = false
            library.requestClose()
        }
    }
    palette.window: Theme.background
    palette.base: Theme.surface
    palette.text: Theme.text
    palette.windowText: Theme.text
    palette.buttonText: Theme.text
    palette.button: Theme.active
    palette.highlight: Theme.accent
    font.family: Theme.font
    font.pixelSize: 13
    font.variableAxes: ({"wght": 400})

    function selectItem(itemId) {
        currentItem = library.inspectItem(itemId)
        itemDialog.open()
    }

    Shortcut { sequence: "Ctrl+K"; onActivated: { settingsOpen = false; search.forceActiveFocus() } }
    Shortcut { sequence: "Ctrl+O"; onActivated: if (!library.busy) fileDialog.open() }
    Shortcut { sequence: "Escape"; onActivated: { settingsOpen = false; search.text = "" } }

    FileDialog {
        id: fileDialog
        title: "Добавить файлы в библиотеку"
        fileMode: FileDialog.OpenFiles
        options: FileDialog.DontUseNativeDialog
        nameFilters: ["Медиафайлы (*.mp4 *.mkv *.avi *.mov *.webm *.m4v *.mpg *.mpeg *.ts *.m2ts *.mts *.wmv *.ogv *.flv *.3gp *.vob *.mp3 *.flac *.wav *.ogg *.opus *.m4a *.aac *.wma *.aif *.aiff *.alac)", "Все файлы (*)"]
        onAccepted: library.importFiles(selectedFiles)
    }

    Rectangle {
        id: sidebar
        width: 76
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        color: Theme.sidebar
        Image {
            x: 24; y: 30
            width: 28; height: 28
            source: "../assets/icons/logo.svg"
        }
        Column {
            x: 14; y: 120
            spacing: 12
            QuietButton {
                width: 48; height: 48
                iconName: "library"
                hint: "Библиотека"
                selected: !settingsOpen
                onClicked: settingsOpen = false
            }
            QuietButton {
                width: 48; height: 48
                iconName: "search"
                hint: "Найти в библиотеке · Ctrl+K"
                onClicked: { settingsOpen = false; search.forceActiveFocus() }
            }
        }
        QuietButton {
            objectName: "settingsButton"
            x: 14; anchors.bottom: parent.bottom; anchors.bottomMargin: 24
            width: 48; height: 48
            iconName: "settings"
            hint: "Настройки"
            selected: settingsOpen
            onClicked: settingsOpen = !settingsOpen
        }
    }

    ColumnLayout {
        id: mainLayout
        visible: !settingsOpen
        anchors.left: sidebar.right
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        anchors.leftMargin: window.width < 1100 ? 28 : 48
        anchors.rightMargin: window.width < 1100 ? 28 : 48
        anchors.topMargin: 40
        anchors.bottomMargin: 24
        spacing: 0

        RowLayout {
            Layout.fillWidth: true
            spacing: 24
            ColumnLayout {
                spacing: 4
                Text { text: "Библиотека"; color: Theme.text; font.pixelSize: 32; font.variableAxes: ({"wght": 600}) }
                Text { text: "Твоя локальная медиатека"; color: Theme.muted; font.pixelSize: 13 }
            }
            Item { Layout.fillWidth: true }
            Rectangle {
                Layout.preferredWidth: window.width < 1100 ? 200 : 260
                Layout.preferredHeight: 40
                color: Theme.surface
                radius: Theme.radius
                border.color: search.activeFocus ? Theme.accent : Theme.line
                Image { x: 12; anchors.verticalCenter: parent.verticalCenter; width: 24; height: 24; source: "../assets/icons/search.svg" }
                TextField {
                    id: search
                    objectName: "librarySearch"
                    anchors.fill: parent
                    leftPadding: 42; rightPadding: 12
                    placeholderText: "Найти в библиотеке"
                    placeholderTextColor: Theme.muted
                    color: Theme.text
                    font.pixelSize: 12
                    background: Item {}
                    selectByMouse: true
                    onTextChanged: library.setQuery(text)
                    Accessible.name: "Поиск по названиям файлов"
                }
            }
            QuietButton {
                objectName: "importButton"
                text: library.busy ? "Добавление…" : "Добавить файлы"
                iconName: "plus"
                outlined: true
                enabled: !library.busy
                onClicked: fileDialog.open()
            }
        }
        Item { Layout.preferredHeight: 32 }
        RowLayout {
            Layout.fillWidth: true
            spacing: 8
            Repeater {
                model: [{key: "all", label: "Всё"}, {key: "unassigned", label: "Не распределено"},
                        {key: "film", label: "Фильмы"}, {key: "series", label: "Сериалы"},
                        {key: "music", label: "Музыка"}, {key: "video", label: "Видео"}]
                delegate: QuietButton {
                    required property var modelData
                    text: modelData.label + (modelData.key === "all" && library.totalCount > 0 ? "  " + library.totalCount : "")
                    selected: library.category === modelData.key
                    onClicked: library.setCategory(modelData.key)
                }
            }
            Item { Layout.fillWidth: true }
        }
        Item { Layout.preferredHeight: 24 }
        Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: Theme.line }
        Item { Layout.preferredHeight: 22 }
        RowLayout {
            Layout.fillWidth: true
            spacing: 8
            Text { text: search.text.length ? "Найдено" : "Файлы"; font.pixelSize: 15; font.variableAxes: ({"wght": 600}); color: Theme.text }
            Text { text: library.items.length; font.pixelSize: 11; color: Theme.subtle }
            Item { Layout.fillWidth: true }
            Text { text: "Недавно добавленные"; color: Theme.muted; font.pixelSize: 11; Layout.rightMargin: 12 }
            QuietButton { width: 36; implicitWidth: 36; implicitHeight: 36; iconName: "grid"; hint: "Карточки"; selected: !library.listView; onClicked: library.setListView(false) }
            QuietButton { width: 36; implicitWidth: 36; implicitHeight: 36; iconName: "list"; hint: "Список"; selected: library.listView; onClicked: library.setListView(true) }
        }
        Item { Layout.preferredHeight: 16 }
        Item {
            Layout.fillWidth: true
            Layout.fillHeight: true
            GridView {
                id: cards
                objectName: "libraryGrid"
                anchors.fill: parent
                visible: !library.listView
                clip: true
                model: library.items
                cellWidth: width / Math.max(1, Math.floor((width + 24) / (Theme.cardWidth + 24)))
                cellHeight: 314
                ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
                delegate: Item {
                    required property var modelData
                    width: cards.cellWidth
                    height: cards.cellHeight
                    Column {
                        spacing: 0
                        width: Theme.cardWidth
                        Rectangle {
                            width: Theme.cardWidth; height: Theme.posterHeight
                            color: mediaMouse.containsMouse ? Theme.active : Theme.surface
                            radius: Theme.radius
                            border.color: mediaMouse.containsMouse ? Theme.accent : Theme.line
                            border.width: mediaMouse.containsMouse ? 1 : 0
                            Image {
                                anchors.centerIn: parent
                                width: 28; height: 28
                                source: "../assets/icons/logo.svg"
                                opacity: 0.3
                            }
                            Rectangle {
                                x: 8; y: 8; width: extensionLabel.width + 12; height: 20
                                radius: 4; color: "#070b12"
                                Text { id: extensionLabel; x: 6; anchors.verticalCenter: parent.verticalCenter; text: modelData.extension.substring(1).toUpperCase(); color: Theme.text; font.pixelSize: 9 }
                            }
                            MouseArea {
                                id: mediaMouse
                                anchors.fill: parent; hoverEnabled: true
                                cursorShape: Qt.PointingHandCursor
                                onClicked: window.selectItem(modelData.id)
                            }
                            ToolTip.visible: mediaMouse.containsMouse
                            ToolTip.delay: 800
                            ToolTip.text: modelData.title
                            Accessible.role: Accessible.Button
                            Accessible.name: modelData.title
                            Accessible.onPressAction: window.selectItem(modelData.id)
                            activeFocusOnTab: true
                            Keys.onReturnPressed: window.selectItem(modelData.id)
                        }
                        Item { height: 10; width: 1 }
                        Text { width: parent.width; text: modelData.title; elide: Text.ElideRight; color: Theme.text; font.pixelSize: 13; height: 22 }
                        Text { width: parent.width; text: modelData.categoryLabel + " · " + modelData.sizeLabel; elide: Text.ElideRight; color: Theme.muted; font.pixelSize: 11 }
                    }
                }
            }
            ListView {
                id: rows
                objectName: "libraryList"
                anchors.fill: parent
                visible: library.listView
                clip: true
                model: library.items
                spacing: 4
                ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
                delegate: ItemDelegate {
                    required property var modelData
                    width: rows.width - 12; height: 60
                    background: Rectangle { color: parent.hovered ? Theme.surface : "transparent"; radius: Theme.radius }
                    contentItem: RowLayout {
                        spacing: 16
                        Rectangle { Layout.preferredWidth: 42; Layout.preferredHeight: 42; radius: 6; color: Theme.active
                            Text { anchors.centerIn: parent; text: modelData.extension.substring(1).toUpperCase(); font.pixelSize: 10; color: Theme.muted }
                        }
                        Text { text: modelData.title; color: Theme.text; font.pixelSize: 13; elide: Text.ElideRight; Layout.fillWidth: true }
                        Text { text: modelData.categoryLabel; color: Theme.muted; font.pixelSize: 11; Layout.preferredWidth: 120 }
                        Text { text: modelData.sizeLabel; color: Theme.muted; font.pixelSize: 11; Layout.preferredWidth: 80; horizontalAlignment: Text.AlignRight }
                    }
                    onClicked: window.selectItem(modelData.id)
                    Accessible.name: modelData.title
                }
            }
            Column {
                visible: library.items.length === 0
                anchors.centerIn: parent
                spacing: 14
                width: Math.min(parent.width, 440)
                Image { anchors.horizontalCenter: parent.horizontalCenter; width: 28; height: 28; source: "../assets/icons/logo.svg"; opacity: 0.45 }
                Text { width: parent.width; horizontalAlignment: Text.AlignHCenter; text: library.totalCount === 0 ? "Здесь будет твоя библиотека" : "Пока ничего не найдено"; color: Theme.text; font.pixelSize: 20; font.variableAxes: ({"wght": 600}) }
                Text { width: parent.width; horizontalAlignment: Text.AlignHCenter; text: library.totalCount === 0 ? "Добавь видео или музыку с компьютера.\nФайлы останутся на своих местах." : "Попробуй другой раздел или измени запрос."; color: Theme.muted; font.pixelSize: 13; lineHeight: 1.5; wrapMode: Text.Wrap }
                QuietButton { visible: library.totalCount === 0; anchors.horizontalCenter: parent.horizontalCenter; text: "Добавить файлы"; iconName: "plus"; outlined: true; enabled: !library.busy; onClicked: fileDialog.open() }
            }
            DropArea {
                anchors.fill: parent
                onDropped: function(drop) { if (drop.hasUrls && !library.busy) { library.importFiles(drop.urls); drop.acceptProposedAction() } }
            }
        }
        RowLayout {
            visible: library.status.length > 0
            Layout.fillWidth: true
            Layout.preferredHeight: 38
            Text { text: library.status; color: Theme.muted; font.pixelSize: 12; elide: Text.ElideRight; Layout.fillWidth: true }
            QuietButton { visible: library.details.length > 0; text: "Подробности"; onClicked: errorDialog.open() }
        }
        RowLayout {
            Layout.fillWidth: true
            Layout.preferredHeight: 22
            Text { text: library.totalCount + " файлов · " + library.totalSize; font.pixelSize: 11; color: Theme.subtle }
            Item { Layout.fillWidth: true }
            Text { text: "Локальная библиотека"; font.pixelSize: 11; color: Theme.subtle }
        }
    }

    ColumnLayout {
        visible: settingsOpen
        anchors.left: sidebar.right
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.margins: 48
        spacing: 24
        Text { text: "Настройки"; color: Theme.text; font.pixelSize: 32; font.variableAxes: ({"wght": 600}) }
        Text { text: "ScriptoLib · " + library.version; color: Theme.muted; font.pixelSize: 13 }
        Rectangle { Layout.fillWidth: true; height: 1; color: Theme.line }
        Text { text: "Обновления"; color: Theme.text; font.pixelSize: 18; font.variableAxes: ({"wght": 600}) }
        Text { text: library.updateStatus; color: Theme.muted; font.pixelSize: 13; wrapMode: Text.Wrap; Layout.fillWidth: true }
        RowLayout {
            spacing: 12
            QuietButton { objectName: "checkUpdatesButton"; text: library.updateBusy ? "Подождите…" : "Проверить обновления"; outlined: true; enabled: !library.updateBusy; onClicked: library.checkUpdates() }
            QuietButton { visible: library.updateAvailable && library.installed; text: "Обновить и перезапустить"; outlined: true; enabled: !library.updateBusy && !library.busy; onClicked: library.installUpdate() }
        }
        Text { visible: library.updateAvailable && !library.installed; text: "Обновление из исходников: используй установщик новой версии."; color: Theme.muted; font.pixelSize: 12 }
        Item { Layout.preferredHeight: 8 }
        Rectangle { Layout.fillWidth: true; height: 1; color: Theme.line }
        Text { text: "Хранение"; color: Theme.text; font.pixelSize: 18; font.variableAxes: ({"wght": 600}) }
        Text { text: library.storageRoot; color: Theme.text; font.pixelSize: 13; wrapMode: Text.WrapAnywhere; Layout.fillWidth: true }
        Text { text: "Настройки и каталог — data. Временные файлы — cache.\nПапка для скачанного — media; она сохраняется при удалении программы."; color: Theme.muted; font.pixelSize: 13; lineHeight: 1.6; wrapMode: Text.Wrap; Layout.fillWidth: true }
    }

    Dialog {
        id: itemDialog
        anchors.centerIn: parent
        width: Math.min(540, window.width - 80)
        modal: true
        title: "Файл в библиотеке"
        padding: 24
        background: Rectangle { color: Theme.sidebar; border.color: Theme.line; radius: 12 }
        contentItem: ColumnLayout {
            spacing: 18
            Text { text: currentItem.title || ""; color: Theme.text; font.pixelSize: 20; font.variableAxes: ({"wght": 600}); wrapMode: Text.Wrap; Layout.fillWidth: true }
            Text { text: currentItem.path || ""; color: Theme.muted; font.pixelSize: 12; wrapMode: Text.WrapAnywhere; Layout.fillWidth: true }
            Text { text: currentItem.available ? "Файл найден · " + (currentItem.sizeLabel || "") : "Файл не найден по сохранённому пути"; color: currentItem.available ? Theme.muted : "#e4b9a3"; font.pixelSize: 12 }
            RowLayout {
                visible: currentItem.kind === "video"
                Text { text: "Раздел"; color: Theme.muted; Layout.preferredWidth: 70 }
                ComboBox {
                    id: categorySelect
                    Layout.fillWidth: true
                    model: [{label: "Не распределено", key: "unassigned"}, {label: "Фильмы", key: "film"}, {label: "Сериалы", key: "series"}, {label: "Видео", key: "video"}]
                    textRole: "label"
                    valueRole: "key"
                    currentIndex: { let names = ["unassigned", "film", "series", "video"]; return Math.max(0, names.indexOf(currentItem.category)) }
                    onActivated: library.assignCategory(currentItem.id, currentValue)
                }
            }
            Text { text: "Воспроизведение появится на следующем этапе."; color: Theme.subtle; font.pixelSize: 12; wrapMode: Text.Wrap; Layout.fillWidth: true }
            RowLayout {
                QuietButton { text: "Убрать из библиотеки"; onClicked: { library.removeItem(currentItem.id); itemDialog.close() } }
                Item { Layout.fillWidth: true }
                QuietButton { text: "Готово"; outlined: true; onClicked: itemDialog.close() }
            }
        }
    }
    Dialog {
        id: errorDialog
        anchors.centerIn: parent
        width: Math.min(650, window.width - 100)
        height: Math.min(420, window.height - 100)
        title: "Не удалось добавить"
        modal: true
        standardButtons: Dialog.Close
        background: Rectangle { color: Theme.sidebar; border.color: Theme.line; radius: 12 }
        contentItem: ScrollView { TextArea { text: library.details; readOnly: true; wrapMode: Text.Wrap; color: Theme.muted; selectByMouse: true } }
    }
}
