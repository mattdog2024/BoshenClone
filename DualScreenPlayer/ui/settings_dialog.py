"""
系统设置对话框
"""
from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
                              QComboBox, QPushButton, QCheckBox, QGroupBox,
                              QFormLayout, QDoubleSpinBox, QTabWidget, QWidget,
                              QSpinBox)
from PyQt5.QtCore import Qt
from utils.config import Config
from core.screen_manager import ScreenManager


class SettingsDialog(QDialog):
    """系统设置对话框"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.config = Config()
        self.screen_manager = ScreenManager()
        self.setWindowTitle("设置选项")
        self.setFixedSize(480, 420)
        self.setStyleSheet("""
            QDialog { background: #2a2a2a; color: #dddddd; }
            QLabel { color: #dddddd; }
            QComboBox, QSpinBox, QDoubleSpinBox {
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
            QCheckBox { color: #dddddd; }
            QTabWidget::pane { border: 1px solid #555; }
            QTabBar::tab {
                background: #333; color: #aaa; padding: 5px 15px;
                border: 1px solid #555;
            }
            QTabBar::tab:selected { background: #4a4a6a; color: #fff; }
        """)
        self._setup_ui()
        self._load_settings()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        tabs = QTabWidget()

        # 通用设置
        general_tab = QWidget()
        general_layout = QVBoxLayout(general_tab)
        general_group = QGroupBox("通用")
        general_form = QFormLayout(general_group)

        self._double_click_check = QCheckBox("双击播放媒体")
        general_form.addRow("", self._double_click_check)

        self._preload_check = QCheckBox("启用预加载")
        general_form.addRow("", self._preload_check)

        self._clear_ppt_check = QCheckBox("关闭时清除PPT缓存")
        general_form.addRow("", self._clear_ppt_check)

        self._fade_spin = QDoubleSpinBox()
        self._fade_spin.setRange(0.0, 5.0)
        self._fade_spin.setSingleStep(0.1)
        self._fade_spin.setSuffix(" 秒")
        general_form.addRow("淡入淡出时长:", self._fade_spin)

        self._blackout_spin = QDoubleSpinBox()
        self._blackout_spin.setRange(0.1, 2.0)
        self._blackout_spin.setSingleStep(0.1)
        self._blackout_spin.setSuffix(" 秒")
        general_form.addRow("黑屏切换时长(Ctrl+B):", self._blackout_spin)

        general_layout.addWidget(general_group)
        general_layout.addStretch()
        tabs.addTab(general_tab, "通用设置")

        # 输出设置
        output_tab = QWidget()
        output_layout = QVBoxLayout(output_tab)
        output_group = QGroupBox("输出配置")
        output_form = QFormLayout(output_group)

        self._screen_combo = QComboBox()
        screens = self.screen_manager.get_screens()
        for s in screens:
            label = f"屏幕 {s['index'] + 1}: {s['width']}x{s['height']}"
            if s['is_primary']:
                label += " (主屏)"
            self._screen_combo.addItem(label, s['index'])
        output_form.addRow("输出屏幕:", self._screen_combo)

        self._res_combo = QComboBox()
        resolutions = ["1920x1080", "1280x720", "3840x2160", "1024x768", "自定义"]
        for r in resolutions:
            self._res_combo.addItem(r)
        output_form.addRow("输出分辨率:", self._res_combo)

        output_layout.addWidget(output_group)
        output_layout.addStretch()
        tabs.addTab(output_tab, "输出设置")

        layout.addWidget(tabs)

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

    def _load_settings(self):
        self._double_click_check.setChecked(self.config['double_click_play'])
        self._preload_check.setChecked(self.config['preload_next'])
        self._clear_ppt_check.setChecked(self.config['clear_ppt_cache_on_close'])
        self._fade_spin.setValue(self.config['fade_duration'])
        self._blackout_spin.setValue(self.config['blackout_duration'])

        # 设置当前输出屏幕
        output_screen = self.config['output_screen']
        for i in range(self._screen_combo.count()):
            if self._screen_combo.itemData(i) == output_screen:
                self._screen_combo.setCurrentIndex(i)
                break

    def _save_and_close(self):
        self.config.set('double_click_play', self._double_click_check.isChecked())
        self.config.set('preload_next', self._preload_check.isChecked())
        self.config.set('clear_ppt_cache_on_close', self._clear_ppt_check.isChecked())
        self.config.set('fade_duration', self._fade_spin.value())
        self.config.set('blackout_duration', self._blackout_spin.value())
        screen_index = self._screen_combo.currentData()
        if screen_index is not None:
            self.config.set('output_screen', screen_index)
        self.accept()
