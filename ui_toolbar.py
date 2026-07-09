# -*- coding: utf-8 -*-
"""
波神凯线工具栏 v3.4.0
改动：
  - 关闭按钮(X) 改为「隐藏到托盘」而非退出程序
  - 新增 hide_to_tray 信号供 main.py 处理
  - 其余功能完全保留
"""

from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
                               QLabel, QFrame, QComboBox, QToolButton, QColorDialog)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QIcon, QFont, QColor


class DraggableWidget(QWidget):
    """可拖拽的无边框窗口基类"""

    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self._drag_pos = None

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.LeftButton and self._drag_pos:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_pos = None


class BoshenToolbar(DraggableWidget):
    """
    波神凯线主工具栏 v3.4.0
    """
    tool_selected     = Signal(str)   # 工具被选中
    clear_requested   = Signal()      # 清除画线
    close_requested   = Signal()      # 退出程序（保留兼容）
    hide_to_tray      = Signal()      # 隐藏到托盘（新增）
    color_changed     = Signal(QColor)
    fast_mode_toggled = Signal(bool)
    timeframe_changed = Signal(str)
    save_requested    = Signal()

    def __init__(self):
        super().__init__()
        self.init_ui()

    def init_ui(self):
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(5, 5, 5, 5)
        main_layout.setSpacing(2)

        self.setStyleSheet("""
            QWidget {
                background-color: #f0f0f0;
                border: 1px solid #a0a0a0;
                font-family: "SimSun";
                font-size: 12px;
            }
            QLabel#DragHandle {
                color: #888888;
                font-weight: bold;
            }
            QPushButton, QToolButton {
                border: 1px solid transparent;
                background-color: transparent;
                padding: 2px;
            }
            QPushButton:hover, QToolButton:hover {
                border: 1px solid #8f8f8f;
                background-color: #e0e0e0;
            }
            QPushButton:pressed, QToolButton:pressed {
                border: 1px solid #4f4f4f;
                background-color: #c0c0c0;
            }
            QToolButton:checked {
                background-color: #a0a0a0;
                border: 1px inset #555555;
            }
        """)

        # ── 第一行 ────────────────────────────────────────────────────────
        row1 = QHBoxLayout()
        row1.setSpacing(2)

        drag_handle = QLabel("::")
        drag_handle.setObjectName("DragHandle")
        row1.addWidget(drag_handle)

        btns_r1 = ["k线", "量", "单", "箱", "影", "画", "测2", "数", "米", "Dx", "自动"]
        for b_text in btns_r1:
            btn = QToolButton()
            btn.setText(b_text)
            if b_text == "Dx":
                btn.clicked.connect(self.clear_requested.emit)
                btn.setToolTip("清除所有画线 (快捷键: Ctrl+Alt+D)")
            elif b_text == "单":
                btn.clicked.connect(lambda checked=False, t=b_text: self.on_tool_click(t))
                btn.setToolTip("单线工具 (快捷键: Ctrl+Alt+S)")
            elif b_text == "自动":
                btn.clicked.connect(lambda: self.on_tool_click("ocr_selection"))
            elif b_text == "画":
                btn.clicked.connect(lambda: self.on_tool_click("free_draw"))
            else:
                btn.clicked.connect(lambda checked=False, t=b_text: self.on_tool_click(t))
            row1.addWidget(btn)

        line = QFrame()
        line.setFrameShape(QFrame.VLine)
        line.setFrameShadow(QFrame.Sunken)
        row1.addWidget(line)

        # 颜色选择按钮
        self.color_btn = QPushButton()
        self.color_btn.setStyleSheet("background-color: red; border: 1px solid gray; width: 40px;")
        self.color_btn.clicked.connect(self.choose_color)
        row1.addWidget(self.color_btn)

        # 时区下拉
        self.combo = QComboBox()
        self.combo.addItems(["日线", "4小时", "1小时"])
        self.combo.setFixedWidth(60)
        self.combo.currentTextChanged.connect(self.timeframe_changed.emit)
        row1.addWidget(self.combo)

        # 保存按钮
        save_btn = QToolButton()
        save_btn.setText("存")
        save_btn.setToolTip("保存当前屏幕线段到选定时区")
        save_btn.clicked.connect(self.save_requested.emit)
        row1.addWidget(save_btn)

        # 帮助按钮
        help_btn = QToolButton()
        help_btn.setText("?")
        help_btn.setStyleSheet("color: blue; font-weight: bold;")
        row1.addWidget(help_btn)

        # ── 隐藏到托盘按钮（原来的 X 改为 _） ───────────────────────────
        hide_btn = QToolButton()
        hide_btn.setText("_")
        hide_btn.setToolTip("隐藏到系统托盘（双击托盘图标可重新显示）")
        hide_btn.setStyleSheet("color: #555; font-weight: bold;")
        hide_btn.clicked.connect(self.hide_to_tray.emit)
        row1.addWidget(hide_btn)

        # 退出按钮（保留，改为红色 X）
        close_btn = QToolButton()
        close_btn.setText("X")
        close_btn.setToolTip("退出程序")
        close_btn.setStyleSheet("color: red; font-weight: bold;")
        close_btn.clicked.connect(self.close_requested.emit)
        row1.addWidget(close_btn)

        row1.addStretch()
        main_layout.addLayout(row1)

        # ── 分隔线 ────────────────────────────────────────────────────────
        h_line = QFrame()
        h_line.setFrameShape(QFrame.HLine)
        h_line.setFrameShadow(QFrame.Sunken)
        main_layout.addWidget(h_line)

        # ── 第二行 ────────────────────────────────────────────────────────
        row2 = QHBoxLayout()
        row2.setSpacing(2)

        btns_r2_text = ["解", "表", "快"]
        for b_text in btns_r2_text:
            btn = QToolButton()
            btn.setText(b_text)
            if b_text == "快":
                btn.setCheckable(True)
                btn.toggled.connect(self.fast_mode_toggled.emit)
            row2.addWidget(btn)

        line2 = QFrame()
        line2.setFrameShape(QFrame.VLine)
        line2.setFrameShadow(QFrame.Sunken)
        row2.addWidget(line2)

        draw_tools = ["\\", "\\\\", "[]", "G", "%", "T", "|||", "||||", "/", "V", "↑", "↓"]
        for dt in draw_tools:
            btn = QToolButton()
            btn.setText(dt)
            if dt == "V":
                btn.clicked.connect(lambda: self.on_tool_click("fan"))
            else:
                btn.clicked.connect(lambda checked=False, t=dt: self.on_tool_click(t))
            row2.addWidget(btn)

        row2.addStretch()
        main_layout.addLayout(row2)

        self.setLayout(main_layout)

    def on_tool_click(self, tool_name):
        print(f"Tool selected: {tool_name}")
        self.tool_selected.emit(tool_name)

    def choose_color(self):
        color = QColorDialog.getColor(Qt.red, self, "Select Color")
        if color.isValid():
            self.color_btn.setStyleSheet(
                f"background-color: {color.name()}; border: 1px solid gray; width: 40px;"
            )
            self.color_changed.emit(color)


if __name__ == "__main__":
    from PySide6.QtWidgets import QApplication
    import sys
    app = QApplication(sys.argv)
    win = BoshenToolbar()
    win.show()
    sys.exit(app.exec())
