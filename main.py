# -*- coding: utf-8 -*-
"""
波神凯线系统 v3.4.2
修复记录（v3.4.0 → v3.4.1）：
  - 修复按 Ctrl+Alt+S 后程序偶发崩溃/自动退出的问题。
    v3.4.0 用了 `keyboard` 库监听全局热键，该库依赖 Windows 低级键盘钩子
    （WH_KEYBOARD_LL），与 PySide6 的事件循环不在同一线程，竞态时会让
    Python 进程直接 crash，且在某些场景下表现不稳定。
  - 改用 Windows 原生 RegisterHotKey API（通过 ctypes 调用 user32.dll），
    配合 QAbstractNativeEventFilter 在 Qt 主线程消化 WM_HOTKEY 消息。
    完全在 Qt 主线程事件循环中处理，不开额外线程，不引入第三方钩子库。
  - 额外修复：
      · overlay.set_tool() 不再 raise_()/activateWindow()，避免在热键路径上
        强行抢 Windows 焦点导致看盘软件错乱。
      · overlay.auto_calibrate_axis() 用 try/finally 兜底，确保 setVisible
        状态恢复，避免 overlay 被永久隐藏。
      · 启动 / 退出热键资源都妥善释放（UnregisterHotKey）。
"""

import sys
import os
import ctypes
import ctypes.wintypes
from ctypes import wintypes

from PySide6.QtWidgets import QApplication, QSystemTrayIcon, QMenu
from PySide6.QtCore import Qt, QTimer, Signal, QObject, QAbstractNativeEventFilter
from PySide6.QtGui import QIcon, QAction

from ui_toolbar import BoshenToolbar
from overlay import Overlay
from ui_analysis import AnalysisDialog


# ── 辅助：获取资源文件路径（兼容 PyInstaller 打包） ──────────────────────
def resource_path(relative_path: str) -> str:
    """兼容 PyInstaller 打包后的资源路径"""
    base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, relative_path)


# ── Windows 全局热键（纯 ctypes，无第三方依赖） ───────────────────────────
# Win32 RegisterHotKey API
#   BOOL RegisterHotKey(HWND hWnd, int id, UINT fsModifiers, UINT vk);
#   BOOL UnregisterHotKey(HWND hWnd, int id);
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000  # Vista+ 才支持，避免按键重复触发

VK_S = 0x53
VK_D = 0x44

WM_HOTKEY = 0x0312

user32 = ctypes.windll.user32
user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_uint, ctypes.c_uint]
user32.RegisterHotKey.restype = wintypes.BOOL
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
user32.UnregisterHotKey.restype = wintypes.BOOL


class WinHotkeyFilter(QAbstractNativeEventFilter):
    """
    在 Qt 主线程消化 WM_HOTKEY 消息。
    通过 ctypes 调 user32.RegisterHotKey 注册系统级热键，
    Qt 主线程的 nativeEvent 会自动把 WM_HOTKEY 路由到本过滤器。
    """

    def __init__(self):
        super().__init__()
        # id -> 回调
        self._handlers = {}
        # 已注册的 id 列表（用于退出时清理）
        self._registered_ids = []

    def register(self, hotkey_id: int, vk: int, modifiers: int, callback):
        """
        注册全局热键。失败返回 False。
        注意：必须在 QApplication 安装完成之后调用。
        """
        # 同一 id 已经注册过则先释放，避免泄漏
        if hotkey_id in self._registered_ids:
            self.unregister(hotkey_id)

        # MOD_NOREPEAT 防止按住时反复触发
        ok = user32.RegisterHotKey(None, hotkey_id, modifiers | MOD_NOREPEAT, vk)
        if not ok:
            err = ctypes.GetLastError()
            print(f"[警告] RegisterHotKey(id={hotkey_id}) 失败，错误码 {err}。"
                  f"该组合键可能已被其他程序占用。")
            return False

        self._handlers[hotkey_id] = callback
        self._registered_ids.append(hotkey_id)
        print(f"[信息] 已注册全局热键 id={hotkey_id} (mod=0x{modifiers:04x}, vk=0x{vk:02x})")
        return True

    def unregister(self, hotkey_id: int):
        if hotkey_id in self._registered_ids:
            user32.UnregisterHotKey(None, hotkey_id)
            self._registered_ids.remove(hotkey_id)
            self._handlers.pop(hotkey_id, None)

    def unregister_all(self):
        for hid in list(self._registered_ids):
            user32.UnregisterHotKey(None, hid)
        self._registered_ids.clear()
        self._handlers.clear()

    def nativeEventFilter(self, event_type, message):  # noqa: N802 (Qt API)
        # PySide6 在 Windows 上 event_type == "windows_generic_MSG" 或 b"windows_generic_MSG"
        # 实际 message 是 ctypes 指针，指向 MSG
        if event_type != "windows_generic_MSG" and event_type != b"windows_generic_MSG":
            return False, 0

        try:
            msg = wintypes.MSG.from_address(int(message))
        except (TypeError, ValueError):
            return False, 0

        if msg.message == WM_HOTKEY:
            hotkey_id = msg.wParam
            handler = self._handlers.get(hotkey_id)
            if handler is not None:
                try:
                    handler()
                except Exception as e:
                    # 任何回调异常都吞掉，绝不让它冒泡到 Qt 主循环，
                    # 否则 Qt 会把未捕获的 Python 异常当作 fatal 让进程退出。
                    print(f"[警告] 热键回调异常: {e}")
                return True, 0
        return False, 0


# ── 全局热键 ID ──────────────────────────────────────────────────────────
HK_SINGLE = 1  # Ctrl+Alt+S → 激活"单"工具
HK_CLEAR  = 2  # Ctrl+Alt+D → 清除所有画线


# ── 全局热键信号桥(主要给 Qt 连接的回调用) ───────────────────────────────
class HotkeySignals(QObject):
    """热键触发后通过 Qt Signal 在主线程串行执行 UI 动作"""
    activate_single = Signal()
    clear_all       = Signal()


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
    tray.setToolTip("波神凯线系统 v3.4.2\n双击显示/隐藏面板\nCtrl+Alt+S: 单线工具\nCtrl+Alt+D: 清除画线")

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

    # ── 全局热键（Win32 RegisterHotKey + QAbstractNativeEventFilter） ─────
    hotkey_signals = HotkeySignals()
    hotkey_filter = WinHotkeyFilter()
    app.installNativeEventFilter(hotkey_filter)

    def _activate_single():
        """
        主线程槽：激活「单」画线工具。
        v3.4.1 改动：
          - 不再依赖 overlay.set_tool 内部的 raise_/activateWindow。
            overlay.set_tool() 已经去掉这两步，热键触发时不会强抢焦点，
            也不会触发自动截图校准。
          - 一切 Qt 操作都在当前函数（Qt 主线程）内同步完成，
            不再走 QTimer.singleShot 后路。
        """
        overlay.set_tool("单")
        tray.showMessage(
            "波神凯线",
            "已激活「单」画线工具",
            QSystemTrayIcon.MessageIcon.Information,
            1500,
        )

    def _do_clear_all():
        """主线程槽：清除所有画线"""
        overlay.clear_all()
        tray.showMessage(
            "波神凯线",
            "已清除所有画线",
            QSystemTrayIcon.MessageIcon.Information,
            1500,
        )

    hotkey_signals.activate_single.connect(_activate_single)
    hotkey_signals.clear_all.connect(_do_clear_all)

    # 注册两个全局热键。WinHotkeyFilter 的回调就在 Qt 主线程消化，
    # 通过 Signal 发出，让 Qt 自动以 QueuedConnection 派发（这里本来就是同线程，
    # 写 Qt.AutoConnection 也不影响，但显式 QueuedConnection 更稳妥）。
    ok_s = hotkey_filter.register(
        HK_SINGLE, VK_S, MOD_CONTROL | MOD_ALT,
        lambda: hotkey_signals.activate_single.emit()
    )
    ok_d = hotkey_filter.register(
        HK_CLEAR, VK_D, MOD_CONTROL | MOD_ALT,
        lambda: hotkey_signals.clear_all.emit()
    )

    if not (ok_s and ok_d):
        tray.showMessage(
            "波神凯线",
            "全局热键注册失败：可能被其他程序占用。\n"
            "可继续在面板上使用工具按钮。",
            QSystemTrayIcon.MessageIcon.Warning,
            3000,
        )

    # ── 退出时清理：避免热键 ID 被主进程遗留在系统里 ─────────────────────
    app.aboutToQuit.connect(hotkey_filter.unregister_all)

    # ── 启动提示 ──────────────────────────────────────────────────────────
    QTimer.singleShot(800, lambda: tray.showMessage(
        "波神凯线 v3.4.2",
        "程序已在托盘运行\n双击图标显示面板\nCtrl+Alt+S: 单线工具\nCtrl+Alt+D: 清除画线",
        QSystemTrayIcon.MessageIcon.Information,
        3000
    ))

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
