import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

Button {
    id: control
    property string iconName: ""
    property string hint: ""
    property bool selected: false
    property bool outlined: false
    implicitWidth: text.length ? contentItem.implicitWidth + 28 : 48
    implicitHeight: 40
    padding: text.length ? 10 : Math.max(0, (Math.min(width, height) - 24) / 2)
    leftPadding: padding
    rightPadding: padding
    topPadding: padding
    bottomPadding: padding
    hoverEnabled: true
    focusPolicy: Qt.StrongFocus
    Accessible.name: hint.length ? hint : text
    background: Rectangle {
        radius: Theme.radius
        color: control.selected || control.down ? Theme.active : control.hovered ? Theme.surface : "transparent"
        border.width: control.outlined || control.visualFocus ? 1 : 0
        border.color: control.visualFocus ? Theme.accent : Theme.line
        Behavior on color { ColorAnimation { duration: 100 } }
    }
    contentItem: RowLayout {
        spacing: 6
        Image {
            visible: control.iconName.length > 0
            source: control.iconName.length ? "../assets/icons/" + control.iconName + ".svg" : ""
            sourceSize.width: 24
            sourceSize.height: 24
            Layout.preferredWidth: 24
            Layout.preferredHeight: 24
            Layout.alignment: Qt.AlignCenter
            opacity: control.enabled ? 1 : 0.4
        }
        Text {
            visible: control.text.length > 0
            text: control.text
            font.family: Theme.font
            font.pixelSize: 13
            color: control.enabled ? Theme.text : Theme.subtle
            horizontalAlignment: Text.AlignHCenter
            Layout.fillWidth: true
        }
    }
    ToolTip.visible: hovered && hint.length > 0
    ToolTip.text: hint
    ToolTip.delay: 650
}

