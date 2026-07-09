# -*- coding: utf-8 -*-
"""
波神凯线系统 v3.4.0
新增功能：
  - 启动后自动最小化到右下角系统托盘
  - 双击托盘图标 → 显示/隐藏工具面板
  - 全局热键 Ctrl+Alt+S → 激活"单"画线工具
  - 全局热键 Ctrl+Alt+D → 快速清除所有画线
"""

import sys
import os
import threading

from PySide6.QtWidgets import QApplication, QSystemTrayIcon, QMenu
from PySide6.QtCore import Qt, QTimer, Signal, QObject
from PySide6.QtGui import QIcon, QAction

from ui_toolbar import BoshenToolbar
from overlay import Overlay
from ui_analysis import AnalysisDialog

# ── 辅助：获取资源文件路径（兼容 PyInstaller 打包） ──────────────────────
def resource_path(relative_path: str) -> str:
    """兼容 PyInstaller 打包后的资源路径"""
    base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, relative_path)


# ── 全局热键信号桥（子线程 → 主线程） ────────────────────────────────────
class HotkeySignals(QObject):
    """用于从 keyboard 监听线程安全地向 Qt 主线程发送信号"""
    activate_single = Signal()   # Ctrl+Alt+S → 激活"单"工具
    clear_all       = Signal()   # Ctrl+Alt+D → 清除所有画线


# ── 主程序 ────────────────────────────────────────────────────────────────
def main():
    # 必须在 QApplication 创建前设置，否则 Windows 高 DPI 下托盘图标模糊
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)   # 关闭面板不退出程序，只退到托盘

    # ── 全局样式 ──────────────────────────────────────────────────────────
    app.setStyleSheet("""
        QWidget {
            background-color: #F0F0F0;
            color: black;
            font-family: "SimSun";
        }
        QMenu {
            background-color: #F0F0F0;
            border: 1px solid #A0A0A0;
        }
        QMenu::item:selected {
            background-color: #90C8F6;
            color: black;
        }
        QLineEdit {
            background-color: white;
            color: black;
            border: 1px solid #A0A0A0;
        }
        QPushButton {
            background-color: #E0E0E0;
            border: 1px solid #A0A0A0;
            padding: 4px;
        }
        QDialog, QMessageBox, QInputDialog {
            background-color: #FFFFFF;
            color: black;
            border: 1px solid #888;
        }
        QMessageBox QLabel, QInputDialog QLabel {
            color: black;
            background-color: transparent;
        }
    """)

    # ── 托盘图标 ──────────────────────────────────────────────────────────
    icon_path = resource_path("boshen_tray.ico")
    tray_icon_obj = QIcon(icon_path) if os.path.exists(icon_path) else app.style().standardIcon(
        __import__('PySide6.QtWidgets', fromlist=['QStyle']).QStyle.SP_ComputerIcon
    )

    tray = QSystemTrayIcon(tray_icon_obj, parent=app)
    tray.setToolTip("波神凯线系统 v3.4.0\n双击显示/隐藏面板\nCtrl+Alt+S: 单线工具\nCtrl+Alt+D: 清除画线")

    tray_menu = QMenu()
    action_show = QAction("显示面板", tray_menu)
    action_quit = QAction("退出程序", tray_menu)
    tray_menu.addAction(action_show)
    tray_menu.addSeparator()
    tray_menu.addAction(action_quit)
    tray.setContextMenu(tray_menu)
    tray.show()

    # ── 创建 Overlay（全屏透明画布） ──────────────────────────────────────
    overlay = Overlay()
    screen_geo = app.primaryScreen().geometry()
    overlay.setGeometry(screen_geo)
    overlay.showFullScreen()

    # ── 创建工具栏 ────────────────────────────────────────────────────────
    toolbar = BoshenToolbar()

    # 启动时隐藏面板（只在托盘显示）
    toolbar.hide()

    # ── 面板显示/隐藏切换 ─────────────────────────────────────────────────
    def toggle_panel():
        if toolbar.isVisible():
            toolbar.hide()
            action_show.setText("显示面板")
        else:
            toolbar.show()
            toolbar.raise_()
            toolbar.activateWindow()
            action_show.setText("隐藏面板")

    # ── 托盘双击 → 切换面板 ───────────────────────────────────────────────
    def on_tray_activated(reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            toggle_panel()

    tray.activated.connect(on_tray_activated)
    action_show.triggered.connect(toggle_panel)
    action_quit.triggered.connect(app.quit)

    # ── 工具栏信号连接 ────────────────────────────────────────────────────
    def on_tool_selected(tool_name):
        if tool_name == "量":
            dialog = AnalysisDialog(overlay.analysis_data, parent=toolbar)
            dialog.exec()
        else:
            overlay.set_tool(tool_name)

    toolbar.tool_selected.connect(on_tool_selected)
    toolbar.clear_requested.connect(overlay.clear_all)
    toolbar.close_requested.connect(app.quit)
    toolbar.hide_to_tray.connect(toggle_panel)   # 隐藏到托盘
    toolbar.color_changed.connect(overlay.set_line_color)
    toolbar.fast_mode_toggled.connect(overlay.toggle_fast_mode)
    toolbar.timeframe_changed.connect(overlay.set_timeframe)
    toolbar.save_requested.connect(overlay.save_snapshot)

    # ── 全局热键（keyboard 库） ───────────────────────────────────────────
    hotkey_signals = HotkeySignals()

    # 热键触发后通过信号在主线程执行 Qt 操作
    def _hotkey_single():
        hotkey_signals.activate_single.emit()

    def _hotkey_clear():
        hotkey_signals.clear_all.emit()

    def _activate_single():
        """主线程：静默激活"单"工具，面板保持隐藏状态"""
        # 记录调用前 toolbar 的可见状态
        was_visible = toolbar.isVisible()
        overlay.set_tool("单")
        # overlay.set_tool 里的 raise_()/activateWindow() 会将同一应用的 Tool 窗口一起带出来
        # 用极短延迟（等 Qt 事件循环处理完毕）后强制恢复 toolbar 原来的状态
        def _restore_toolbar():
            if not was_visible:
                toolbar.hide()
        QTimer.singleShot(0, _restore_toolbar)
        tray.showMessage("波神凯线", "已激活「单」画线工具", QSystemTrayIcon.MessageIcon.Information, 1500)

    def _do_clear_all():
        """主线程：清除所有画线"""
        overlay.clear_all()
        tray.showMessage("波神凯线", "已清除所有画线", QSystemTrayIcon.MessageIcon.Information, 1500)

    hotkey_signals.activate_single.connect(_activate_single)
    hotkey_signals.clear_all.connect(_do_clear_all)

    # 在后台线程注册全局热键
    def _register_hotkeys():
        try:
            import keyboard
            keyboard.add_hotkey("ctrl+alt+s", _hotkey_single, suppress=False)
            keyboard.add_hotkey("ctrl+alt+d", _hotkey_clear,  suppress=False)
            keyboard.wait()   # 阻塞线程，保持监听
        except ImportError:
            print("[警告] keyboard 库未安装，全局热键不可用。请运行: pip install keyboard")
        except Exception as e:
            print(f"[警告] 全局热键注册失败: {e}")

    hotkey_thread = threading.Thread(target=_register_hotkeys, daemon=True)
    hotkey_thread.start()

    # ── 启动提示 ──────────────────────────────────────────────────────────
    QTimer.singleShot(800, lambda: tray.showMessage(
        "波神凯线 v3.4.0",
        "程序已在托盘运行\n双击图标显示面板\nCtrl+Alt+S: 单线工具\nCtrl+Alt+D: 清除画线",
        QSystemTrayIcon.MessageIcon.Information,
        3000
    ))

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
