"""
屏幕管理模块
检测多显示器，管理输出窗口在不同屏幕间的位置
"""
from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import QRect
from typing import List


class ScreenManager:
    """管理多显示器配置"""

    def __init__(self):
        self._app = QApplication.instance()

    def get_screens(self) -> List[dict]:
        """获取所有显示器信息"""
        screens = []
        for i, screen in enumerate(self._app.screens()):
            geo = screen.geometry()
            screens.append({
                'index': i,
                'name': screen.name(),
                'width': geo.width(),
                'height': geo.height(),
                'x': geo.x(),
                'y': geo.y(),
                'is_primary': screen == self._app.primaryScreen(),
            })
        return screens

    def get_screen_count(self) -> int:
        return len(self._app.screens())

    def get_screen_geometry(self, index: int) -> QRect:
        screens = self._app.screens()
        if 0 <= index < len(screens):
            return screens[index].geometry()
        return self._app.primaryScreen().geometry()

    def get_primary_screen_index(self) -> int:
        primary = self._app.primaryScreen()
        for i, screen in enumerate(self._app.screens()):
            if screen == primary:
                return i
        return 0

    def get_secondary_screen_index(self) -> int:
        """获取第一个非主屏幕的索引"""
        primary_index = self.get_primary_screen_index()
        for i in range(len(self._app.screens())):
            if i != primary_index:
                return i
        return primary_index  # 只有一个屏幕时返回主屏
