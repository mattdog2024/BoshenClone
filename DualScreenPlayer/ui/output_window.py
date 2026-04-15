"""
大屏输出窗口
全屏显示在副屏上，无边框，黑色背景
"""
from PyQt5.QtWidgets import QWidget, QLabel, QVBoxLayout
from PyQt5.QtCore import Qt, QRect
from PyQt5.QtGui import QColor, QPalette, QFont


class OutputWindow(QWidget):
    """大屏输出窗口（全屏，无边框）"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("输出屏幕")
        self.setWindowFlags(
            Qt.Window |
            Qt.FramelessWindowHint |
            Qt.WindowStaysOnTopHint
        )
        # 黑色背景
        palette = self.palette()
        palette.setColor(QPalette.Window, QColor(0, 0, 0))
        self.setPalette(palette)
        self.setAutoFillBackground(True)

        # 用于显示黑屏提示的标签
        self._black_label = QLabel("", self)
        self._black_label.setAlignment(Qt.AlignCenter)
        self._black_label.setStyleSheet("background: black;")
        self._black_label.hide()

        # 用于显示"无连接"提示
        self._no_signal_label = QLabel("等待输出...", self)
        self._no_signal_label.setAlignment(Qt.AlignCenter)
        font = QFont("Arial", 24)
        self._no_signal_label.setFont(font)
        self._no_signal_label.setStyleSheet("color: #444444; background: black;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._no_signal_label)
        self.setLayout(layout)

    def get_video_widget_id(self) -> int:
        """返回用于 VLC 嵌入的窗口句柄"""
        return int(self.winId())

    def show_black_screen(self, show: bool):
        """显示/隐藏黑屏遮罩"""
        if show:
            self._black_label.setGeometry(self.rect())
            self._black_label.setStyleSheet("background: black;")
            self._black_label.show()
            self._black_label.raise_()
        else:
            self._black_label.hide()

    def show_on_screen(self, geometry: QRect):
        """将窗口移动到指定屏幕并全屏"""
        self.setGeometry(geometry)
        self.showFullScreen()
        self._no_signal_label.hide()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._black_label.isVisible():
            self._black_label.setGeometry(self.rect())
        self._no_signal_label.setGeometry(self.rect())

    def hide_no_signal(self):
        self._no_signal_label.hide()
