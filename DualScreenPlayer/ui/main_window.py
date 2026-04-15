"""
主控制台界面
布局：左侧播放列表 | 中间媒体素材区 | 右侧预览/解说员控制台
顶部：工具栏（打开/保存/黑屏/设置/音量/亮度）
底部：播放控制栏（播放/暂停/进度条/时间）
"""
import os
import sys
import threading
from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QSplitter,
    QListWidget, QListWidgetItem, QLabel, QPushButton, QSlider,
    QToolBar, QAction, QFileDialog, QMessageBox, QScrollArea,
    QFrame, QGridLayout, QSizePolicy, QMenu, QInputDialog,
    QStatusBar, QProgressBar
)
from PyQt5.QtCore import Qt, QTimer, QThread, pyqtSignal, QSize
from PyQt5.QtGui import QIcon, QFont, QColor, QPalette, QKeySequence

from core.player_manager import PlayerManager
from core.playlist_manager import PlaylistManager
from core.screen_manager import ScreenManager
from models.media_item import MediaItem, MediaType, VIDEO_EXTENSIONS, IMAGE_EXTENSIONS, AUDIO_EXTENSIONS
from utils.config import Config
from utils.thumbnail import generate_video_thumbnail, generate_image_thumbnail, get_video_info
from ui.output_window import OutputWindow
from ui.media_card import MediaCard
from ui.properties_dialog import PropertiesDialog
from ui.settings_dialog import SettingsDialog


def format_ms(ms: int) -> str:
    """毫秒格式化为 HH:MM:SS"""
    if ms < 0:
        return "00:00:00"
    total = ms // 1000
    h = total // 3600
    m = (total % 3600) // 60
    s = total % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


class ThumbnailWorker(QThread):
    """后台线程：生成缩略图"""
    thumbnail_ready = pyqtSignal(str, str)  # file_path, thumb_path

    def __init__(self, item: MediaItem):
        super().__init__()
        self.item = item

    def run(self):
        if self.item.media_type == MediaType.VIDEO:
            # 先获取视频信息
            info = get_video_info(self.item.file_path)
            self.item.duration = info['duration']
            self.item.width = info['width']
            self.item.height = info['height']
            thumb = generate_video_thumbnail(self.item.file_path)
        elif self.item.media_type == MediaType.IMAGE:
            thumb = generate_image_thumbnail(self.item.file_path)
        else:
            thumb = ""
        self.item.thumbnail_path = thumb
        self.thumbnail_ready.emit(self.item.file_path, thumb)


class MainWindow(QMainWindow):
    """主控制台窗口"""

    def __init__(self):
        super().__init__()
        self.config = Config()
        self.playlist_manager = PlaylistManager()
        self.screen_manager = ScreenManager()
        self.player_manager = PlayerManager()

        self._output_window = OutputWindow()
        self._media_cards = {}  # file_path -> MediaCard
        self._current_playing_item = None
        self._is_black_screen = False
        self._seeking = False
        self._thumbnail_workers = []

        self.setWindowTitle("双屏播放器 - DualScreen Player")
        self.setMinimumSize(1200, 700)
        self._apply_dark_theme()
        self._setup_ui()
        self._setup_shortcuts()
        self._connect_signals()
        self._init_output_window()

    def _apply_dark_theme(self):
        """应用深色主题"""
        self.setStyleSheet("""
            QMainWindow { background: #1e1e1e; }
            QWidget { background: #1e1e1e; color: #dddddd; }
            QSplitter::handle { background: #333; width: 2px; }
            QListWidget {
                background: #252525; color: #dddddd;
                border: 1px solid #444; outline: none;
            }
            QListWidget::item { padding: 4px 8px; }
            QListWidget::item:selected { background: #3a3a6a; color: #ffffff; }
            QListWidget::item:hover { background: #333355; }
            QPushButton {
                background: #3a3a3a; color: #dddddd;
                border: 1px solid #555; padding: 4px 10px;
                border-radius: 3px;
            }
            QPushButton:hover { background: #4a4a5a; }
            QPushButton:pressed { background: #2a2a4a; }
            QPushButton:checked { background: #4a4a8a; border: 1px solid #7a7aff; }
            QSlider::groove:horizontal {
                background: #444; height: 4px; border-radius: 2px;
            }
            QSlider::handle:horizontal {
                background: #4a7aff; width: 12px; height: 12px;
                margin: -4px 0; border-radius: 6px;
            }
            QSlider::sub-page:horizontal { background: #4a7aff; border-radius: 2px; }
            QLabel { color: #dddddd; background: transparent; }
            QScrollArea { border: none; }
            QScrollBar:vertical {
                background: #2a2a2a; width: 8px;
            }
            QScrollBar::handle:vertical {
                background: #555; border-radius: 4px; min-height: 20px;
            }
            QScrollBar:horizontal {
                background: #2a2a2a; height: 8px;
            }
            QScrollBar::handle:horizontal {
                background: #555; border-radius: 4px; min-width: 20px;
            }
            QToolBar {
                background: #252525; border-bottom: 1px solid #444;
                spacing: 5px; padding: 3px;
            }
            QStatusBar { background: #252525; color: #888; }
            QMenuBar { background: #252525; color: #dddddd; }
            QMenuBar::item:selected { background: #3a3a5a; }
            QMenu { background: #2a2a2a; color: #dddddd; border: 1px solid #555; }
            QMenu::item:selected { background: #4a4a6a; }
        """)

    def _setup_ui(self):
        """搭建主界面布局"""
        # 菜单栏
        self._setup_menubar()

        # 工具栏
        self._setup_toolbar()

        # 中心区域
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # 主分割器（左中右）
        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(3)

        # ===== 左侧：播放列表 =====
        left_panel = self._create_left_panel()
        splitter.addWidget(left_panel)

        # ===== 中间：媒体素材区 =====
        center_panel = self._create_center_panel()
        splitter.addWidget(center_panel)

        # ===== 右侧：预览/控制台 =====
        right_panel = self._create_right_panel()
        splitter.addWidget(right_panel)

        splitter.setSizes([200, 700, 280])
        main_layout.addWidget(splitter)

        # ===== 底部：播放控制栏 =====
        control_bar = self._create_control_bar()
        main_layout.addWidget(control_bar)

        # 状态栏
        self.statusBar().showMessage("就绪")

    def _setup_menubar(self):
        menubar = self.menuBar()

        # 文件菜单
        file_menu = menubar.addMenu("文件")
        open_act = QAction("打开节目单", self)
        open_act.setShortcut("Ctrl+O")
        open_act.triggered.connect(self._open_playlist_file)
        file_menu.addAction(open_act)

        save_act = QAction("保存节目单", self)
        save_act.setShortcut("Ctrl+S")
        save_act.triggered.connect(self._save_playlist_file)
        file_menu.addAction(save_act)

        saveas_act = QAction("另存为", self)
        saveas_act.triggered.connect(self._save_playlist_as)
        file_menu.addAction(saveas_act)

        file_menu.addSeparator()

        new_act = QAction("新建节目单", self)
        new_act.setShortcut("Ctrl+N")
        new_act.triggered.connect(self._new_playlist)
        file_menu.addAction(new_act)

        file_menu.addSeparator()
        quit_act = QAction("退出", self)
        quit_act.triggered.connect(self.close)
        file_menu.addAction(quit_act)

        # 播放菜单
        play_menu = menubar.addMenu("播放")
        play_act = QAction("播放/暂停", self)
        play_act.setShortcut("Space")
        play_act.triggered.connect(self._toggle_play)
        play_menu.addAction(play_act)

        stop_act = QAction("停止", self)
        stop_act.triggered.connect(self._stop_playback)
        play_menu.addAction(stop_act)

        # 帮助菜单
        help_menu = menubar.addMenu("帮助")
        about_act = QAction("关于", self)
        about_act.triggered.connect(self._show_about)
        help_menu.addAction(about_act)

    def _setup_toolbar(self):
        toolbar = QToolBar("主工具栏")
        toolbar.setMovable(False)
        toolbar.setIconSize(QSize(32, 32))
        self.addToolBar(toolbar)

        # 打开/保存/另存
        open_btn = QPushButton("打开")
        open_btn.setFixedSize(50, 36)
        open_btn.clicked.connect(self._open_playlist_file)
        toolbar.addWidget(open_btn)

        save_btn = QPushButton("保存")
        save_btn.setFixedSize(50, 36)
        save_btn.clicked.connect(self._save_playlist_file)
        toolbar.addWidget(save_btn)

        toolbar.addSeparator()

        # 黑屏按钮
        self._black_btn = QPushButton("黑屏")
        self._black_btn.setFixedSize(55, 36)
        self._black_btn.setCheckable(True)
        self._black_btn.setStyleSheet("""
            QPushButton { background: #3a2a2a; border: 1px solid #664444; }
            QPushButton:checked { background: #1a1a1a; border: 1px solid #ff4444; color: #ff4444; }
            QPushButton:hover { background: #4a3a3a; }
        """)
        self._black_btn.clicked.connect(self._toggle_black_screen)
        toolbar.addWidget(self._black_btn)

        toolbar.addSeparator()

        # 输出屏幕按钮
        output_btn = QPushButton("输出屏幕")
        output_btn.setFixedSize(70, 36)
        output_btn.clicked.connect(self._show_output_window)
        toolbar.addWidget(output_btn)

        toolbar.addSeparator()

        # 分辨率显示
        self._res_label = QLabel("1920x1080")
        self._res_label.setStyleSheet("color: #888; font-size: 11px;")
        toolbar.addWidget(self._res_label)

        toolbar.addSeparator()

        # 主音量
        vol_label = QLabel("音量:")
        vol_label.setStyleSheet("color: #888; font-size: 11px;")
        toolbar.addWidget(vol_label)

        self._volume_slider = QSlider(Qt.Horizontal)
        self._volume_slider.setRange(0, 100)
        self._volume_slider.setValue(100)
        self._volume_slider.setFixedWidth(80)
        self._volume_slider.valueChanged.connect(self._on_volume_changed)
        toolbar.addWidget(self._volume_slider)

        self._volume_label = QLabel("100")
        self._volume_label.setFixedWidth(30)
        self._volume_label.setStyleSheet("color: #888; font-size: 11px;")
        toolbar.addWidget(self._volume_label)

        toolbar.addSeparator()

        # 设置按钮
        settings_btn = QPushButton("设置")
        settings_btn.setFixedSize(50, 36)
        settings_btn.clicked.connect(self._open_settings)
        toolbar.addWidget(settings_btn)

    def _create_left_panel(self) -> QWidget:
        """创建左侧播放列表面板"""
        panel = QWidget()
        panel.setFixedWidth(200)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(5)

        # 标题
        title = QLabel("播放列表")
        title.setStyleSheet("color: #aaaaaa; font-size: 12px; font-weight: bold;")
        layout.addWidget(title)

        # 播放列表
        self._playlist_list = QListWidget()
        self._playlist_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self._playlist_list.customContextMenuRequested.connect(self._playlist_context_menu)
        self._playlist_list.currentRowChanged.connect(self._on_playlist_selected)
        layout.addWidget(self._playlist_list)

        # 添加列表按钮
        add_list_btn = QPushButton("+ 新建列表")
        add_list_btn.clicked.connect(self._add_playlist)
        layout.addWidget(add_list_btn)

        self._refresh_playlist_list()
        return panel

    def _create_center_panel(self) -> QWidget:
        """创建中间媒体素材区"""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(5)

        # 标题栏
        title_bar = QHBoxLayout()
        self._media_title = QLabel("视频/图像/PPT")
        self._media_title.setStyleSheet("color: #aaaaaa; font-size: 12px; font-weight: bold;")
        title_bar.addWidget(self._media_title)
        title_bar.addStretch()

        # 添加媒体按钮
        add_media_btn = QPushButton("+ 添加媒体")
        add_media_btn.clicked.connect(self._add_media_files)
        title_bar.addWidget(add_media_btn)
        layout.addLayout(title_bar)

        # 媒体素材滚动区域
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setAcceptDrops(True)

        self._media_container = QWidget()
        self._media_container.setAcceptDrops(True)
        self._media_grid = QGridLayout(self._media_container)
        self._media_grid.setContentsMargins(5, 5, 5, 5)
        self._media_grid.setSpacing(8)
        self._media_grid.setAlignment(Qt.AlignTop | Qt.AlignLeft)

        scroll.setWidget(self._media_container)
        layout.addWidget(scroll)

        # 启用拖拽
        self._media_container.installEventFilter(self)
        self.setAcceptDrops(True)

        return panel

    def _create_right_panel(self) -> QWidget:
        """创建右侧预览/解说员控制台"""
        panel = QWidget()
        panel.setFixedWidth(280)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(5)

        # 预览窗口标题
        preview_title = QLabel("预览窗口")
        preview_title.setStyleSheet("color: #aaaaaa; font-size: 12px; font-weight: bold;")
        layout.addWidget(preview_title)

        # 预览视频区域
        self._preview_frame = QFrame()
        self._preview_frame.setFixedHeight(160)
        self._preview_frame.setStyleSheet("background: black; border: 1px solid #444;")
        layout.addWidget(self._preview_frame)

        # 预览控制
        preview_ctrl = QHBoxLayout()
        self._preview_play_btn = QPushButton("▶")
        self._preview_play_btn.setFixedSize(30, 24)
        self._preview_play_btn.clicked.connect(self._preview_play)
        preview_ctrl.addWidget(self._preview_play_btn)

        self._preview_time_label = QLabel("00:00:00")
        self._preview_time_label.setStyleSheet("color: #888; font-size: 10px;")
        preview_ctrl.addWidget(self._preview_time_label)
        preview_ctrl.addStretch()
        layout.addLayout(preview_ctrl)

        # 分隔线
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("color: #444;")
        layout.addWidget(line)

        # 解说员列表标题
        console_title = QLabel("解说员列表")
        console_title.setStyleSheet("color: #aaaaaa; font-size: 12px; font-weight: bold;")
        layout.addWidget(console_title)

        # 下一个节目信息
        self._next_item_label = QLabel("下一个：（无）")
        self._next_item_label.setWordWrap(True)
        self._next_item_label.setStyleSheet("color: #888; font-size: 10px;")
        layout.addWidget(self._next_item_label)

        # 节目列表
        self._console_list = QListWidget()
        self._console_list.setStyleSheet("""
            QListWidget { background: #1a1a1a; font-size: 10px; }
            QListWidget::item { padding: 3px; }
            QListWidget::item:selected { background: #3a3a5a; }
        """)
        layout.addWidget(self._console_list)

        layout.addStretch()
        return panel

    def _create_control_bar(self) -> QWidget:
        """创建底部播放控制栏"""
        bar = QWidget()
        bar.setFixedHeight(70)
        bar.setStyleSheet("background: #252525; border-top: 1px solid #444;")
        layout = QVBoxLayout(bar)
        layout.setContentsMargins(10, 5, 10, 5)
        layout.setSpacing(3)

        # 进度条
        progress_layout = QHBoxLayout()
        self._time_label = QLabel("00:00:00")
        self._time_label.setFixedWidth(65)
        self._time_label.setStyleSheet("color: #aaaaaa; font-size: 11px;")
        progress_layout.addWidget(self._time_label)

        self._progress_slider = QSlider(Qt.Horizontal)
        self._progress_slider.setRange(0, 1000)
        self._progress_slider.setValue(0)
        self._progress_slider.sliderPressed.connect(self._on_seek_start)
        self._progress_slider.sliderReleased.connect(self._on_seek_end)
        progress_layout.addWidget(self._progress_slider)

        self._duration_label = QLabel("00:00:00")
        self._duration_label.setFixedWidth(65)
        self._duration_label.setStyleSheet("color: #aaaaaa; font-size: 11px;")
        progress_layout.addWidget(self._duration_label)
        layout.addLayout(progress_layout)

        # 播放控制按钮
        ctrl_layout = QHBoxLayout()
        ctrl_layout.setSpacing(5)

        # 上一个
        self._prev_btn = QPushButton("⏮")
        self._prev_btn.setFixedSize(36, 30)
        self._prev_btn.clicked.connect(self._play_prev)
        ctrl_layout.addWidget(self._prev_btn)

        # 播放/暂停
        self._play_btn = QPushButton("▶")
        self._play_btn.setFixedSize(40, 30)
        self._play_btn.setStyleSheet("""
            QPushButton {
                background: #4a4a8a; font-size: 14px;
                border: 1px solid #7a7aff;
            }
            QPushButton:hover { background: #5a5aaa; }
        """)
        self._play_btn.clicked.connect(self._toggle_play)
        ctrl_layout.addWidget(self._play_btn)

        # 停止
        self._stop_btn = QPushButton("⏹")
        self._stop_btn.setFixedSize(36, 30)
        self._stop_btn.clicked.connect(self._stop_playback)
        ctrl_layout.addWidget(self._stop_btn)

        # 下一个
        self._next_btn = QPushButton("⏭")
        self._next_btn.setFixedSize(36, 30)
        self._next_btn.clicked.connect(self._play_next)
        ctrl_layout.addWidget(self._next_btn)

        ctrl_layout.addStretch()

        # 当前播放文件名
        self._now_playing_label = QLabel("未播放")
        self._now_playing_label.setStyleSheet("color: #888; font-size: 10px;")
        ctrl_layout.addWidget(self._now_playing_label)

        ctrl_layout.addStretch()

        # 循环按钮
        self._loop_btn = QPushButton("循环")
        self._loop_btn.setFixedSize(45, 30)
        self._loop_btn.setCheckable(True)
        ctrl_layout.addWidget(self._loop_btn)

        layout.addLayout(ctrl_layout)
        return bar

    def _setup_shortcuts(self):
        """设置快捷键"""
        from PyQt5.QtWidgets import QShortcut
        # 空格键：播放/暂停
        QShortcut(QKeySequence("Space"), self, self._toggle_play)
        # Ctrl+B：黑屏
        QShortcut(QKeySequence("Ctrl+B"), self, self._toggle_black_screen)
        # Ctrl+O：打开
        QShortcut(QKeySequence("Ctrl+O"), self, self._open_playlist_file)
        # Ctrl+S：保存
        QShortcut(QKeySequence("Ctrl+S"), self, self._save_playlist_file)

    def _connect_signals(self):
        """连接信号与槽"""
        self.player_manager.position_changed.connect(self._on_position_changed)
        self.player_manager.time_changed.connect(self._on_time_changed)
        self.player_manager.duration_changed.connect(self._on_duration_changed)
        self.player_manager.state_changed.connect(self._on_state_changed)
        self.player_manager.media_ended.connect(self._on_media_ended)

    def _init_output_window(self):
        """初始化输出窗口"""
        screens = self.screen_manager.get_screens()
        if len(screens) > 1:
            output_idx = self.config['output_screen']
            if output_idx >= len(screens):
                output_idx = 1
            geo = self.screen_manager.get_screen_geometry(output_idx)
            self._output_window.show_on_screen(geo)
        else:
            # 单屏模式：以小窗口显示
            self._output_window.setGeometry(100, 100, 800, 450)
            self._output_window.show()

        # 绑定 VLC 到输出窗口
        QTimer.singleShot(500, self._bind_vlc_to_windows)

    def _bind_vlc_to_windows(self):
        """将 VLC 绑定到输出窗口"""
        win_id = self._output_window.get_video_widget_id()
        self.player_manager.set_main_output(win_id)
        preview_id = int(self._preview_frame.winId())
        self.player_manager.set_preview_output(preview_id)

    # ===== 播放列表管理 =====

    def _refresh_playlist_list(self):
        """刷新左侧播放列表显示"""
        self._playlist_list.clear()
        for i in range(self.playlist_manager.get_playlist_count()):
            pl = self.playlist_manager.get_playlist(i)
            item = QListWidgetItem(f"▶ {pl.name}  [{pl.count()}]")
            self._playlist_list.addItem(item)
        current = self.playlist_manager._current_playlist_index
        self._playlist_list.setCurrentRow(current)

    def _on_playlist_selected(self, row: int):
        if row >= 0:
            self.playlist_manager.set_current_playlist(row)
            self._refresh_media_grid()
            self._update_console_list()

    def _add_playlist(self):
        name, ok = QInputDialog.getText(self, "新建列表", "列表名称:")
        if ok and name:
            self.playlist_manager.add_playlist(name)
            self._refresh_playlist_list()

    def _playlist_context_menu(self, pos):
        menu = QMenu(self)
        rename_act = QAction("重命名", self)
        rename_act.triggered.connect(self._rename_playlist)
        menu.addAction(rename_act)
        delete_act = QAction("删除列表", self)
        delete_act.triggered.connect(self._delete_playlist)
        menu.addAction(delete_act)
        menu.exec_(self._playlist_list.mapToGlobal(pos))

    def _rename_playlist(self):
        row = self._playlist_list.currentRow()
        pl = self.playlist_manager.get_playlist(row)
        if pl:
            name, ok = QInputDialog.getText(self, "重命名", "新名称:", text=pl.name)
            if ok and name:
                pl.name = name
                self._refresh_playlist_list()

    def _delete_playlist(self):
        row = self._playlist_list.currentRow()
        self.playlist_manager.remove_playlist(row)
        self._refresh_playlist_list()
        self._refresh_media_grid()

    # ===== 媒体管理 =====

    def _add_media_files(self):
        """添加媒体文件"""
        all_exts = (VIDEO_EXTENSIONS | IMAGE_EXTENSIONS | AUDIO_EXTENSIONS)
        ext_filter = "媒体文件 (" + " ".join(f"*{e}" for e in sorted(all_exts)) + ")"
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择媒体文件", "", ext_filter
        )
        for f in files:
            self._add_single_media(f)

    def _add_single_media(self, file_path: str):
        """添加单个媒体文件到当前播放列表"""
        if not os.path.exists(file_path):
            return
        item = MediaItem(file_path)
        pl_idx = self.playlist_manager._current_playlist_index
        self.playlist_manager.add_media_to_playlist(pl_idx, item)

        # 创建卡片
        card = self._create_media_card(item)
        self._add_card_to_grid(card)
        self._refresh_playlist_list()
        self._update_console_list()

        # 后台生成缩略图
        worker = ThumbnailWorker(item)
        worker.thumbnail_ready.connect(lambda fp, tp: self._on_thumbnail_ready(fp))
        worker.start()
        self._thumbnail_workers.append(worker)

    def _create_media_card(self, item: MediaItem) -> MediaCard:
        card = MediaCard(item)
        card.play_requested.connect(self._play_item)
        card.remove_requested.connect(self._remove_item)
        card.set_kv_requested.connect(self._set_kv)
        card.properties_requested.connect(self._show_properties)
        self._media_cards[item.file_path] = card
        return card

    def _add_card_to_grid(self, card: MediaCard):
        """将卡片添加到网格布局"""
        count = self._media_grid.count()
        cols = max(1, (self._media_container.width() - 10) // (MediaCard.CARD_WIDTH + 8))
        row = count // cols
        col = count % cols
        self._media_grid.addWidget(card, row, col)

    def _refresh_media_grid(self):
        """刷新媒体素材网格"""
        # 清除现有卡片
        while self._media_grid.count():
            item = self._media_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._media_cards.clear()

        pl = self.playlist_manager.get_current_playlist()
        if not pl:
            return

        for i, media_item in enumerate(pl.items):
            card = self._create_media_card(media_item)
            cols = max(1, (self._media_container.width() - 10) // (MediaCard.CARD_WIDTH + 8))
            row = i // cols
            col = i % cols
            self._media_grid.addWidget(card, row, col)

    def _on_thumbnail_ready(self, file_path: str):
        """缩略图生成完成，刷新对应卡片"""
        card = self._media_cards.get(file_path)
        if card:
            card.refresh()

    def _remove_item(self, item: MediaItem):
        pl_idx = self.playlist_manager._current_playlist_index
        pl = self.playlist_manager.get_current_playlist()
        if pl:
            for i, m in enumerate(pl.items):
                if m.file_path == item.file_path:
                    self.playlist_manager.remove_media_from_playlist(pl_idx, i)
                    break
        self._refresh_media_grid()
        self._refresh_playlist_list()
        self._update_console_list()

    def _show_properties(self, item: MediaItem):
        dlg = PropertiesDialog(item, self)
        if dlg.exec_():
            card = self._media_cards.get(item.file_path)
            if card:
                card.refresh()

    def _set_kv(self, item: MediaItem):
        # 清除旧 KV 标记
        old_kv = self.playlist_manager.get_kv()
        if old_kv:
            old_kv.is_kv = False
        self.playlist_manager.set_kv(item)
        self.statusBar().showMessage(f"已设置 KV: {item.label}")

    # ===== 播放控制 =====

    def _play_item(self, item: MediaItem):
        """播放指定素材"""
        self._output_window.hide_no_signal()
        self.player_manager.play(item)
        self._current_playing_item = item

        # 更新 UI
        self._play_btn.setText("⏸")
        self._now_playing_label.setText(f"正在播放: {item.label}")
        self.statusBar().showMessage(f"播放: {item.label}")

        # 更新卡片状态
        for fp, card in self._media_cards.items():
            card.set_playing(fp == item.file_path)

        # 更新播放列表当前索引
        pl = self.playlist_manager.get_current_playlist()
        if pl:
            for i, m in enumerate(pl.items):
                if m.file_path == item.file_path:
                    self.playlist_manager.set_current_item(
                        self.playlist_manager._current_playlist_index, i
                    )
                    break

        # 预览下一个
        self._preview_next_item()

    def _toggle_play(self):
        """播放/暂停切换"""
        if self._current_playing_item is None:
            # 没有播放中的素材，播放第一个
            pl = self.playlist_manager.get_current_playlist()
            if pl and pl.count() > 0:
                self._play_item(pl.items[0])
            return

        self.player_manager.pause()

    def _stop_playback(self):
        """停止播放"""
        self.player_manager.stop()
        self._play_btn.setText("▶")
        self._now_playing_label.setText("未播放")
        self._progress_slider.setValue(0)
        self._time_label.setText("00:00:00")
        for card in self._media_cards.values():
            card.set_playing(False)

    def _play_next(self):
        """播放下一个"""
        next_item = self.playlist_manager.advance_to_next()
        if next_item:
            self._play_item(next_item)
        elif self._loop_btn.isChecked():
            pl = self.playlist_manager.get_current_playlist()
            if pl and pl.count() > 0:
                self.playlist_manager._current_item_index = -1
                self._play_item(pl.items[0])

    def _play_prev(self):
        """播放上一个"""
        pl = self.playlist_manager.get_current_playlist()
        if not pl:
            return
        idx = self.playlist_manager._current_item_index - 1
        if idx >= 0:
            self.playlist_manager._current_item_index = idx
            item = pl.get_item(idx)
            if item:
                self._play_item(item)

    def _preview_next_item(self):
        """在预览窗口显示下一个素材"""
        next_item = self.playlist_manager.get_next_item()
        if next_item:
            self._next_item_label.setText(f"下一个：{next_item.label}")
        else:
            self._next_item_label.setText("下一个：（无）")

    def _preview_play(self):
        """在预览窗口播放下一个素材"""
        next_item = self.playlist_manager.get_next_item()
        if next_item:
            self.player_manager.play_preview(next_item)

    # ===== 黑屏控制 =====

    def _toggle_black_screen(self):
        """切换黑屏"""
        self._is_black_screen = not self._is_black_screen
        self._output_window.show_black_screen(self._is_black_screen)
        self._black_btn.setChecked(self._is_black_screen)
        if self._is_black_screen:
            self.statusBar().showMessage("黑屏已开启 (Ctrl+B 关闭)")
        else:
            self.statusBar().showMessage("黑屏已关闭")

    # ===== 输出窗口 =====

    def _show_output_window(self):
        """显示/切换输出窗口"""
        if self._output_window.isVisible():
            self._output_window.hide()
        else:
            self._init_output_window()

    # ===== 进度控制 =====

    def _on_seek_start(self):
        self._seeking = True

    def _on_seek_end(self):
        pos = self._progress_slider.value() / 1000.0
        self.player_manager.seek(pos)
        self._seeking = False

    def _on_position_changed(self, pos: float):
        if not self._seeking:
            self._progress_slider.setValue(int(pos * 1000))

    def _on_time_changed(self, ms: int):
        self._time_label.setText(format_ms(ms))

    def _on_duration_changed(self, ms: int):
        self._duration_label.setText(format_ms(ms))

    def _on_state_changed(self, state: str):
        if state == 'playing':
            self._play_btn.setText("⏸")
        elif state in ('paused', 'stopped'):
            self._play_btn.setText("▶")

    def _on_media_ended(self):
        """媒体播放结束"""
        if self._current_playing_item:
            if self._current_playing_item.loop:
                # 循环播放当前素材
                self.player_manager.play(self._current_playing_item)
                return
            if self._current_playing_item.play_next:
                self._play_next()
                return
        self._play_btn.setText("▶")

    # ===== 音量控制 =====

    def _on_volume_changed(self, value: int):
        self._volume_label.setText(str(value))
        self.player_manager.set_volume(value)

    # ===== 解说员列表 =====

    def _update_console_list(self):
        """更新右侧解说员列表"""
        self._console_list.clear()
        pl = self.playlist_manager.get_current_playlist()
        if not pl:
            return
        for i, item in enumerate(pl.items):
            text = f"{i + 1}. {item.label}"
            list_item = QListWidgetItem(text)
            self._console_list.addItem(list_item)

    # ===== 文件操作 =====

    def _open_playlist_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "打开节目单", "", "节目单文件 (*.json);;所有文件 (*)"
        )
        if path:
            try:
                self.playlist_manager.load_from_file(path)
                self._refresh_playlist_list()
                self._refresh_media_grid()
                self._update_console_list()
                self.statusBar().showMessage(f"已加载: {path}")
            except Exception as e:
                QMessageBox.warning(self, "错误", f"加载失败: {e}")

    def _save_playlist_file(self):
        last = self.config['last_playlist']
        if last:
            try:
                self.playlist_manager.save_to_file(last)
                self.statusBar().showMessage(f"已保存: {last}")
                return
            except Exception:
                pass
        self._save_playlist_as()

    def _save_playlist_as(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "保存节目单", "", "节目单文件 (*.json)"
        )
        if path:
            if not path.endswith('.json'):
                path += '.json'
            try:
                self.playlist_manager.save_to_file(path)
                self.config.set('last_playlist', path)
                self.statusBar().showMessage(f"已保存: {path}")
            except Exception as e:
                QMessageBox.warning(self, "错误", f"保存失败: {e}")

    def _new_playlist(self):
        reply = QMessageBox.question(
            self, "新建节目单", "确定要新建节目单吗？当前内容将被清除。",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            self.playlist_manager._playlists.clear()
            self.playlist_manager._add_default_playlist()
            self.playlist_manager._current_playlist_index = 0
            self._refresh_playlist_list()
            self._refresh_media_grid()
            self._update_console_list()

    # ===== 设置 =====

    def _open_settings(self):
        dlg = SettingsDialog(self)
        if dlg.exec_():
            # 重新初始化输出窗口（屏幕可能改变了）
            self._init_output_window()

    # ===== 关于 =====

    def _show_about(self):
        QMessageBox.about(
            self, "关于双屏播放器",
            "双屏播放器 v1.0\n\n"
            "专业多屏媒体播放软件\n"
            "支持 Windows 7 ~ Windows 10\n\n"
            "功能：视频/图片/音频播放\n"
            "双屏输出、播放列表管理\n"
            "转场效果、快捷键控制"
        )

    # ===== 拖拽支持 =====

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            file_path = url.toLocalFile()
            if os.path.isfile(file_path):
                self._add_single_media(file_path)

    # ===== 窗口关闭 =====

    def closeEvent(self, event):
        self.player_manager.cleanup()
        self._output_window.close()
        event.accept()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # 重新排列媒体卡片
        QTimer.singleShot(100, self._refresh_media_grid)
