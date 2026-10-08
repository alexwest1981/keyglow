import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

// Bar widget + panel for the Corsair keyboard's lighting.
//
// The HID protocol is not ours: OpenLinkHub owns the keyboard over hidraw and
// serves an HTTP API on 127.0.0.1:27003, so every action goes through
// ./corsair_ctl.py. Nothing here writes to /dev/hidraw and nothing needs root.
Panel {
  id: root

  moduleName: "io.github.alexwest1981.omacorsair"
  ipcTarget: "io.github.alexwest1981.omacorsair"

  // Resolved by position, not by the folder name: a renamed copy still works.
  readonly property string scriptPath: Qt.resolvedUrl("./corsair_ctl.py").toString().replace("file://", "")
  readonly property color foreground: bar ? bar.foreground : Color.foreground
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family
  readonly property bool onlyWhenConnected: setting("onlyWhenConnected", false) !== false

  readonly property string glyph: String.fromCodePoint(0xF11C)

  property bool connected: false
  property string product: "Corsair tangentbord"
  property string firmware: ""
  property string effect: ""
  property string effectName: ""
  property var profiles: []
  property string currentColor: ""
  property int brightnessLevel: -1

  readonly property var swatches: ["#ff8800", "#ff3b30", "#ffd60a", "#30d158", "#00cccc", "#0a84ff", "#bf5af2", "#ffffff"]
  // The bar glyph carries the colour this panel last applied; with nothing
  // applied yet there is no colour to claim, so it falls back to the accent.
  readonly property color litColor: root.currentColor !== "" ? root.currentColor : Color.accent
  readonly property string brightnessLabel: root.brightnessLevel >= 0 ? ("nivå " + root.brightnessLevel) : "inte satt härifrån"

  visible: !onlyWhenConnected || connected
  implicitWidth: visible ? button.implicitWidth : 0
  implicitHeight: visible ? button.implicitHeight : 0

  function refresh() { if (!pollProc.running) pollProc.running = true }

  function act(args) {
    if (actionProc.running) return
    actionProc.command = ["python3", root.scriptPath].concat(args)
    actionProc.running = true
  }

  function setColor(hex) { root.currentColor = hex; root.act(["color", hex]) }
  function setBrightness(level) { root.brightnessLevel = level; root.act(["brightness", String(level)]) }
  function setEffect(id) { root.act(["effect", id]) }

  function openControlPanel() {
    Quickshell.execDetached(["xdg-open", "http://127.0.0.1:27003/index.html"])
  }

  Process {
    id: pollProc
    command: ["python3", root.scriptPath, "status"]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        try {
          var d = JSON.parse(text.trim())
          root.connected = d.connected === true
          root.product = d.product || root.product
          root.firmware = d.firmware || ""
          root.effect = d.effect || ""
          root.effectName = d.effect_name || d.effect || ""
          root.profiles = Array.isArray(d.profiles) ? d.profiles : []
          root.currentColor = d.color || ""
          root.brightnessLevel = (typeof d.brightness === "number") ? d.brightness : -1
        } catch (e) {
          console.warn("OmaCorsair: status JSON not readable: " + e)
        }
      }
    }
  }

  Process {
    id: actionProc
    command: []
    onExited: function(code) { root.refresh() }
  }

  Timer {
    interval: panel.open ? 4000 : 20000
    running: true
    repeat: true
    triggeredOnStart: true
    onTriggered: root.refresh()
  }

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    visible: root.visible
    useActiveColor: false
    slotSize: Style.bar.iconSlot
    tooltipText: root.product + (root.connected
      ? (" • " + (root.effectName || "—") + " • " + root.brightnessLabel)
      : " • frånkopplad")
    iconComponent: Component {
      Text {
        text: root.glyph
        anchors.centerIn: parent
        color: root.connected ? root.litColor : Qt.darker(root.bar ? root.bar.foreground : Color.foreground, 1.6)
        font.family: root.fontFamily
        font.pixelSize: Style.bar.iconFont
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
      }
    }
    onPressed: function(b) {
      if (b === Qt.RightButton) root.refresh()
      else root.toggle()
    }
  }

  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(340))
    contentHeight: panel.fittedContentHeight(panelColumn.implicitHeight, Style.space(600))

    onOpenChanged: if (open) root.refresh()

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }

      Flickable {
        id: panelFlick
        anchors.fill: parent
        contentWidth: width
        contentHeight: panelColumn.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        flickableDirection: Flickable.VerticalFlick
        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

        Column {
          id: panelColumn
          width: panelFlick.width
          spacing: Style.space(12)

          PanelHero {
            width: parent.width
            title: root.product
            meta: root.connected ? ("fw " + (root.firmware || "?") + " • " + (root.effectName || "—")) : "hittas inte"
            detail: root.connected ? "OpenLinkHub" : "frånkopplad"
            foreground: root.foreground
            fontFamily: root.fontFamily
            iconOpacity: root.connected ? 1.0 : 0.4

            iconComponent: Component {
              Text {
                text: root.glyph
                color: root.connected ? root.litColor : Qt.darker(root.foreground, 1.6)
                font.family: root.fontFamily
                font.pixelSize: Style.font.display
              }
            }

            trailingControl: Component {
              Item {
                width: Style.space(26)
                height: Style.space(26)
                Text {
                  anchors.centerIn: parent
                  text: String.fromCodePoint(0xF0450)
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.body
                  color: refreshArea.containsMouse ? Color.accent : Qt.darker(root.foreground, 1.4)
                }
                MouseArea {
                  id: refreshArea
                  anchors.fill: parent
                  hoverEnabled: true
                  cursorShape: Qt.PointingHandCursor
                  onClicked: root.refresh()
                }
              }
            }
          }

          PanelSeparator { width: parent.width; foreground: root.foreground }

          Column {
            width: parent.width
            spacing: Style.space(8)

            PanelSectionHeader { text: "FÄRG"; foreground: root.foreground }

            RowLayout {
              width: parent.width
              spacing: Style.space(6)
              enabled: root.connected

              Repeater {
                model: root.swatches
                Rectangle {
                  Layout.fillWidth: true
                  implicitHeight: Style.space(26)
                  radius: Style.space(4)
                  readonly property bool isSelected: root.currentColor.toLowerCase() === String(modelData).toLowerCase()
                  color: modelData
                  border.color: isSelected ? root.foreground : Qt.rgba(0, 0, 0, 0.45)
                  border.width: isSelected ? 2 : 1
                  opacity: root.connected ? 1.0 : 0.4

                  MouseArea {
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: root.setColor(String(modelData))
                  }
                }
              }
            }
          }

          PanelSeparator { width: parent.width; foreground: root.foreground }

          Column {
            width: parent.width
            spacing: Style.space(8)

            RowLayout {
              width: parent.width
              PanelSectionHeader { text: "LJUSSTYRKA"; foreground: root.foreground }
              Item { Layout.fillWidth: true; height: 1 }
              Text {
                text: root.brightnessLabel
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
                color: Color.accent
              }
            }

            ButtonGroup {
              options: [
                { value: "0", label: "0" },
                { value: "1", label: "1" },
                { value: "2", label: "2" },
                { value: "3", label: "3" }
              ]
              value: root.brightnessLevel >= 0 ? String(root.brightnessLevel) : ""
              fontFamily: root.fontFamily
              foreground: root.foreground
              onChanged: function(val) { root.setBrightness(parseInt(val)) }
            }
          }

          PanelSeparator { width: parent.width; foreground: root.foreground }

          Column {
            width: parent.width
            spacing: Style.space(8)

            RowLayout {
              width: parent.width
              PanelSectionHeader { text: "EFFEKT"; foreground: root.foreground }
              Item { Layout.fillWidth: true; height: 1 }
              Text {
                text: root.effectName || "—"
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
                color: Color.accent
              }
            }

            // Rows, not a Dropdown. The panel window is ~360 px tall and Qt
            // clamps a Popup to its window: an 18-item list hung below the
            // trigger ran off the bottom and the clicks never landed.
            Column {
              width: parent.width
              spacing: Style.space(2)

              Repeater {
                model: root.profiles

                Rectangle {
                  required property var modelData
                  width: parent.width
                  implicitHeight: Style.space(24)
                  radius: Style.space(4)
                  readonly property bool isCurrent: modelData.id === root.effect
                  readonly property color faint: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.03)
                  readonly property color faintHover: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.09)

                  color: isCurrent ? Style.selectedFillFor(root.foreground, Color.accent)
                       : (effectRow.containsMouse ? faintHover : faint)
                  border.width: isCurrent ? 1 : 0
                  border.color: Color.accent

                  Text {
                    anchors.left: parent.left
                    anchors.leftMargin: Style.space(8)
                    anchors.verticalCenter: parent.verticalCenter
                    textFormat: Text.PlainText
                    text: modelData.name
                    color: parent.isCurrent ? Color.accent : root.foreground
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.bodySmall
                  }

                  MouseArea {
                    id: effectRow
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: root.setEffect(modelData.id)
                  }
                }
              }
            }
          }

          PanelSeparator { width: parent.width; foreground: root.foreground }

          WidgetButton {
            bar: root.bar
            width: parent.width
            text: "Öppna OpenLinkHub (per tangent, makron)"
            tooltipText: "Hela gränssnittet på 127.0.0.1:27003"
            onPressed: root.openControlPanel()
          }
        }
      }
    }
  }
}
