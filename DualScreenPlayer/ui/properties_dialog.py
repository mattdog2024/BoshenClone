"""
媒体属性设置对话框
"""
from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
                              QLineEdit, QSlider, QComboBox, QPushButton,
                              QCheckBox, QGroupBox, QFormLayout, QSpinBox)
from PyQt5.QtCore import Qt
from models.media_item import MediaItem


class PropertiesDialog(QDialog):
    """媒体属性设置对话框"""

    def __init__(self, item: MediaItem, parent=None):
        super().__init__(parent)
        self.item = item
        self.setWindowTitle("媒体属性")
        self.setFixedSize(400, 380)
        self.setStyleSheet("""
            QDialog { background: #2a2a2a; color: #dddddd; }
            QLabel { color: #dddddd; }
            QLineEdit, QComboBox, QSpinBox {
                background: #1a1a1a; color: #dddddd;
                border: 1px solid #555; padding: 3px;
            }
            QPushButton {
                background: #4a4a6a; color: #dddddd;
                border: 1px solid #666; padding: 5px 15px;
                border-radius: 3px;
            }
            QPushButton:hover { background: #5a5a8a; }
            QGroupBox {
                color: #aaaaaa; border: 1px solid #555;
                margin-top: 8px; padding-top: 8px;
            }
            QGroupBox::title { subcontrol-origin: margin; left: 8px; }
            QSlider::groove:horizontal {
                background: #444; height: 4px; border-radius: 2px;
            }
            QSlider::handle:horizontal {
                background: #4a7aff; width: 12px; height: 12px;
                margin: -4px 0; border-radius: 6px;
            }
            QCheckBox { color: #dddddd; }
        """)
        self._setup_ui()
        self._load_data()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # 文件信息
        info_group = QGroupBox("文件信息")
        info_layout = QFormLayout(info_group)
        self._path_label = QLabel()
        self._path_label.setWordWrap(True)
        info_layout.addRow("路径:", self._path_label)
        self._type_label = QLabel()
        info_layout.addRow("类型:", self._type_label)
        self._size_label = QLabel()
        info_layout.addRow("尺寸:", self._size_label)
        self._duration_label = QLabel()
        info_layout.addRow("时长:", self._duration_label)
        layout.addWidget(info_group)

        # 播放设置
        play_group = QGroupBox("播放设置")
        play_layout = QFormLayout(play_group)

        self._label_edit = QLineEdit()
        play_layout.addRow("显示标签:", self._label_edit)

        self._volume_slider = QSlider(Qt.Horizontal)
        self._volume_slider.setRange(0, 100)
        self._volume_label = QLabel("100")
        self._volume_slider.valueChanged.connect(
            lambda v: self._volume_label.setText(str(v))
        )
        vol_layout = QHBoxLayout()
        vol_layout.addWidget(self._volume_slider)
        vol_layout.addWidget(self._volume_label)
        play_layout.addRow("音量:", vol_layout)

        self._loop_check = QCheckBox("循环播放")
        play_layout.addRow("", self._loop_check)

        self._play_next_check = QCheckBox("播放完成后播放下一个")
        play_layout.addRow("", self._play_next_check)

        layout.addWidget(play_group)

        # 快捷键
        key_group = QGroupBox("快捷键")
        key_layout = QFormLayout(key_group)
        self._shortcut_edit = QLineEdit()
        self._shortcut_edit.setPlaceholderText("0-9 或 a-z")
        self._shortcut_edit.setMaxLength(1)
        key_layout.addRow("播放快捷键:", self._shortcut_edit)
        layout.addWidget(key_group)

        # 按钮
        btn_layout = QHBoxLayout()
        ok_btn = QPushButton("确定")
        ok_btn.clicked.connect(self._save_and_close)
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addStretch()
        btn_layout.addWidget(ok_btn)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)

    def _load_data(self):
        """加载当前素材数据"""
        self._path_label.setText(self.item.file_path)
        self._type_label.setText(self.item.media_type.value)
        self._size_label.setText(self.item.format_resolution() or "N/A")
        self._duration_label.setText(self.item.format_duration())
        self._label_edit.setText(self.item.label)
        self._volume_slider.setValue(self.item.volume)
        self._loop_check.setChecked(self.item.loop)
        self._play_next_check.setChecked(self.item.play_next)
        self._shortcut_edit.setText(self.item.shortcut_key)

    def _save_and_close(self):
        """保存设置并关闭"""
        self.item.label = self._label_edit.text() or self.item.label
        self.item.volume = self._volume_slider.value()
        self.item.loop = self._loop_check.isChecked()
        self.item.play_next = self._play_next_check.isChecked()
        self.item.shortcut_key = self._shortcut_edit.text()
        self.accept()
