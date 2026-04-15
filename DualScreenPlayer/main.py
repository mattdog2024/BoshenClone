"""
双屏播放器 - DualScreen Player
主入口文件
"""
import sys
import os

# 确保当前目录在 Python 路径中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import Qt, QCoreApplication
from PyQt5.QtGui import QIcon
from ui.main_window import MainWindow


def main():
    # 高 DPI 支持（Windows 10）
    QCoreApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QCoreApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    app = QApplication(sys.argv)
    app.setApplicationName("双屏播放器")
    app.setApplicationVersion("1.0")
    app.setOrganizationName("DualScreenPlayer")

    # 设置应用图标（如果存在）
    icon_path = os.path.join(os.path.dirname(__file__), "resources", "icon.ico")
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))

    window = MainWindow()
    window.show()

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
