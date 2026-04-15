"""
媒体素材卡片组件
显示缩略图、文件名、分辨率、时长等信息
"""
from PyQt5.QtWidgets import (QWidget, QLabel, QVBoxLayout, QHBoxLayout,
                              QSizePolicy, QMenu, QAction, QFrame)
from PyQt5.QtCore import Qt, pyqtSignal, QSize
from PyQt5.QtGui import QPixmap, QColor, QPalette, QFont, QIcon
from models.media_item import MediaItem, MediaType


class MediaCard(QWidget):
    """媒体素材卡片"""

    clicked = pyqtSignal(object)        # MediaItem
    double_clicked = pyqtSignal(object) # MediaItem
    play_requested = pyqtSignal(object)
    loop_toggled = pyqtSignal(object)
    remove_requested = pyqtSignal(object)
    set_kv_requested = pyqtSignal(object)
    properties_requested = pyqtSignal(object)

    CARD_WIDTH = 160
    CARD_HEIGHT = 130
    THUMB_HEIGHT = 90

    def __init__(self, item: MediaItem, parent=None):
        super().__init__(parent)
        self.item = item
        self._selected = False
        self._playing = False
        self.setFixedSize(self.CARD_WIDTH, self.CARD_HEIGHT)
        self.setCursor(Qt.PointingHandCursor)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)
        self._setup_ui()
        self._update_display()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)

        # 缩略图区域
        self._thumb_label = QLabel()
        self._thumb_label.setFixedSize(self.CARD_WIDTH - 4, self.THUMB_HEIGHT)
        self._thumb_label.setAlignment(Qt.AlignCenter)
        self._thumb_label.setStyleSheet("background: #1a1a1a; border: 1px solid #333;")

        # 信息区域
        self._info_label = QLabel()
        self._info_label.setFixedHeight(18)
        self._info_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        font = QFont()
        font.setPointSize(7)
        self._info_label.setFont(font)
        self._info_label.setStyleSheet("color: #aaaaaa; background: transparent;")

        # 文件名标签
        self._name_label = QLabel()
        self._name_label.setFixedHeight(16)
        self._name_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        font2 = QFont()
        font2.setPointSize(7)
        self._name_label.setFont(font2)
        self._name_label.setStyleSheet("color: #dddddd; background: transparent;")

        layout.addWidget(self._thumb_label)
        layout.addWidget(self._name_label)
        layout.addWidget(self._info_label)

        # 整体样式
        self.setStyleSheet("""
            MediaCard {
                background: #2a2a2a;
                border: 1px solid #444;
                border-radius: 3px;
            }
        """)

    def _update_display(self):
        """更新显示内容"""
        # 缩略图
        if self.item.thumbnail_path:
            pixmap = QPixmap(self.item.thumbnail_path)
            if not pixmap.isNull():
                pixmap = pixmap.scaled(
                    self.CARD_WIDTH - 4, self.THUMB_HEIGHT,
                    Qt.KeepAspectRatio, Qt.SmoothTransformation
                )
                self._thumb_label.setPixmap(pixmap)
            else:
                self._set_default_thumb()
        else:
            self._set_default_thumb()

        # 文件名（截断）
        name = self.item.label
        if len(name) > 18:
            name = name[:15] + "..."
        self._name_label.setText(name)
        self._name_label.setToolTip(self.item.label)

        # 信息（分辨率 + 时长）
        info_parts = []
        if self.item.format_resolution():
            info_parts.append(self.item.format_resolution())
        if self.item.duration > 0:
            info_parts.append(self.item.format_duration())
        self._info_label.setText("  ".join(info_parts))

    def _set_default_thumb(self):
        """设置默认缩略图（根据类型显示不同图标文字）"""
        type_icons = {
            MediaType.VIDEO: "▶ 视频",
            MediaType.IMAGE: "🖼 图片",
            MediaType.AUDIO: "♪ 音频",
            MediaType.UNKNOWN: "? 未知",
        }
        text = type_icons.get(self.item.media_type, "?")
        self._thumb_label.setText(text)
        self._thumb_label.setStyleSheet(
            "background: #1a1a2a; border: 1px solid #333; color: #666; font-size: 11px;"
        )

    def set_selected(self, selected: bool):
        self._selected = selected
        if selected:
            self.setStyleSheet("""
                MediaCard {
                    background: #2a3a5a;
                    border: 1px solid #4a7aff;
                    border-radius: 3px;
                }
            """)
        else:
            self.setStyleSheet("""
                MediaCard {
                    background: #2a2a2a;
                    border: 1px solid #444;
                    border-radius: 3px;
                }
            """)

    def set_playing(self, playing: bool):
        self._playing = playing
        if playing:
            self.setStyleSheet("""
                MediaCard {
                    background: #1a3a1a;
                    border: 2px solid #44ff44;
                    border-radius: 3px;
                }
            """)
        elif not self._selected:
            self.setStyleSheet("""
                MediaCard {
                    background: #2a2a2a;
                    border: 1px solid #444;
                    border-radius: 3px;
                }
            """)

    def refresh(self):
        """刷新显示（缩略图生成后调用）"""
        self._update_display()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.item)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.double_clicked.emit(self.item)
            self.play_requested.emit(self.item)
        super().mouseDoubleClickEvent(event)

    def _show_context_menu(self, pos):
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background: #2a2a2a;
                color: #dddddd;
                border: 1px solid #555;
            }
            QMenu::item:selected {
                background: #4a4a6a;
            }
        """)

        play_action = QAction("播放", self)
        play_action.triggered.connect(lambda: self.play_requested.emit(self.item))
        menu.addAction(play_action)

        loop_action = QAction("循环播放", self)
        loop_action.setCheckable(True)
        loop_action.setChecked(self.item.loop)
        loop_action.triggered.connect(self._toggle_loop)
        menu.addAction(loop_action)

        menu.addSeparator()

        kv_action = QAction("设置为KV", self)
        kv_action.triggered.connect(lambda: self.set_kv_requested.emit(self.item))
        menu.addAction(kv_action)

        menu.addSeparator()

        props_action = QAction("属性设置", self)
        props_action.triggered.connect(lambda: self.properties_requested.emit(self.item))
        menu.addAction(props_action)

        remove_action = QAction("删除媒体", self)
        remove_action.triggered.connect(lambda: self.remove_requested.emit(self.item))
        menu.addAction(remove_action)

        menu.exec_(self.mapToGlobal(pos))

    def _toggle_loop(self):
        self.item.loop = not self.item.loop
        self.loop_toggled.emit(self.item)
