"""
双屏播放器 主界面
使用 PyQt5 QMediaPlayer + QVideoWidget，不依赖 VLC
"""
import os
import sys
import json
import subprocess
from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QSplitter,
    QListWidget, QListWidgetItem, QLabel, QPushButton, QSlider,
    QToolBar, QAction, QFileDialog, QMessageBox, QScrollArea,
    QFrame, QSizePolicy, QMenu, QInputDialog, QStatusBar,
    QApplication, QDialog, QDialogButtonBox, QFormLayout,
    QSpinBox, QCheckBox, QComboBox, QGroupBox
)
from PyQt5.QtCore import Qt, QTimer, QUrl, QSize, QThread, pyqtSignal
from PyQt5.QtGui import QIcon, QFont, QColor, QPalette, QPixmap, QKeySequence
from PyQt5.QtMultimedia import QMediaPlayer, QMediaContent, QMediaPlaylist
from PyQt5.QtMultimediaWidgets import QVideoWidget

# 支持的媒体格式
VIDEO_EXT = {'.mp4', '.avi', '.mkv', '.mov', '.wmv', '.flv', '.ts', '.m4v',
             '.rmvb', '.rm', '.3gp', '.webm', '.mpg', '.mpeg', '.m2ts'}
IMAGE_EXT = {'.jpg', '.jpeg', '.png', '.bmp', '.gif', '.tiff', '.webp'}
AUDIO_EXT = {'.mp3', '.wav', '.aac', '.flac', '.ogg', '.wma', '.m4a'}


def format_ms(ms):
    if ms < 0:
        return "00:00:00"
    total = ms // 1000
    h = total // 3600
    m = (total % 3600) // 60
    s = total % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


def get_media_type(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in VIDEO_EXT:
        return 'video'
    elif ext in IMAGE_EXT:
        return 'image'
    elif ext in AUDIO_EXT:
        return 'audio'
    return 'unknown'


class OutputWindow(QWidget):
    """大屏输出窗口 - 全屏显示在第二个屏幕"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("双屏播放器 - 输出屏幕")
        self.setStyleSheet("background-color: black;")
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 视频输出区域
        self.video_widget = QVideoWidget(self)
        self.video_widget.setStyleSheet("background-color: black;")
        # 关键：强制创建原生 Win32 窗口句柄，否则 Windows 上视频只显示黑色
        self.video_widget.setAttribute(Qt.WA_NativeWindow, True)
        layout.addWidget(self.video_widget)

        # 图片显示标签
        self.image_label = QLabel(self)
        self.image_label.setAlignment(Qt.AlignCenter)
        self.image_label.setStyleSheet("background-color: black; color: white;")
        self.image_label.hide()
        layout.addWidget(self.image_label)

        # 黑屏遮罩
        self._black_overlay = QWidget(self)
        self._black_overlay.setStyleSheet("background-color: black;")
        self._black_overlay.hide()

        self._is_black = False
        self._player_ref = None  # 持有播放器引用，用于重新绑定

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._black_overlay.resize(self.size())

    def show_image(self, path):
        """显示图片"""
        self.video_widget.hide()
        self.image_label.show()
        pixmap = QPixmap(path)
        if not pixmap.isNull():
            scaled = pixmap.scaled(self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.image_label.setPixmap(scaled)

    def show_video(self):
        """切换到视频模式"""
        self.image_label.hide()
        self.video_widget.show()
        # 重新绑定播放器，确保窗口显示后视频输出正常
        if self._player_ref is not None:
            self._player_ref.setVideoOutput(self.video_widget)

    def set_black(self, black: bool):
        self._is_black = black
        if black:
            self._black_overlay.resize(self.size())
            self._black_overlay.show()
            self._black_overlay.raise_()
        else:
            self._black_overlay.hide()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            pass  # 不允许 ESC 退出全屏


class MediaItem:
    """媒体素材"""
    def __init__(self, path):
        self.path = path
        self.name = os.path.basename(path)
        self.media_type = get_media_type(path)
        self.duration_ms = 0       # 图片显示时长（毫秒）
        self.loop = False
        self.volume = 100


class PlaylistWidget(QListWidget):
    """播放列表控件，支持拖拽排序"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragDropMode(QListWidget.InternalMove)
        self.setSelectionMode(QListWidget.SingleSelection)
        self.setStyleSheet("""
            QListWidget {
                background: #1e1e2e;
                color: #cdd6f4;
                border: 1px solid #45475a;
                font-size: 13px;
            }
            QListWidget::item {
                padding: 6px 8px;
                border-bottom: 1px solid #313244;
            }
            QListWidget::item:selected {
                background: #313244;
                color: #cba6f7;
            }
            QListWidget::item:hover {
                background: #2a2a3e;
            }
        """)


class MainWindow(QMainWindow):
    """主控制台窗口"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("双屏播放器 v2.0")
        self.resize(1280, 800)
        self.setMinimumSize(900, 600)

        # 深色主题
        self._apply_dark_theme()

        # 数据
        self._playlists = {"默认列表": []}   # name -> [MediaItem]
        self._current_playlist = "默认列表"
        self._current_index = -1
        self._is_black = False
        self._loop_mode = 0   # 0=不循环 1=单曲循环 2=列表循环
        self._image_timer = QTimer()
        self._image_timer.setSingleShot(True)
        self._image_timer.timeout.connect(self._on_image_timer)

        # 输出窗口
        self._output = OutputWindow()

        # 主播放器
        self._player = QMediaPlayer()
        self._player.positionChanged.connect(self._on_position_changed)
        self._player.durationChanged.connect(self._on_duration_changed)
        self._player.stateChanged.connect(self._on_state_changed)
        self._player.error.connect(self._on_player_error)

        # 预览播放器
        self._preview_player = QMediaPlayer()

        # 构建界面
        self._build_ui()
        self._build_menu()

        # 先移动输出窗口到第二屏并显示，再绑定视频输出
        # 顺序非常重要：必须先 show()，再 setVideoOutput()
        self._move_output_to_screen2()

        # 绑定视频输出（窗口已显示后再绑定）
        self._output._player_ref = self._player
        self._player.setVideoOutput(self._output.video_widget)
        # 强制触发原生句柄初始化
        _ = self._output.video_widget.winId()

    def _apply_dark_theme(self):
        self.setStyleSheet("""
            QMainWindow, QWidget {
                background-color: #1e1e2e;
                color: #cdd6f4;
            }
            QPushButton {
                background-color: #313244;
                color: #cdd6f4;
                border: 1px solid #45475a;
                border-radius: 4px;
                padding: 5px 12px;
                font-size: 13px;
            }
            QPushButton:hover { background-color: #45475a; }
            QPushButton:pressed { background-color: #585b70; }
            QPushButton:checked { background-color: #cba6f7; color: #1e1e2e; }
            QSlider::groove:horizontal {
                height: 4px;
                background: #45475a;
                border-radius: 2px;
            }
            QSlider::handle:horizontal {
                width: 14px; height: 14px;
                background: #cba6f7;
                border-radius: 7px;
                margin: -5px 0;
            }
            QSlider::sub-page:horizontal { background: #cba6f7; border-radius: 2px; }
            QLabel { color: #cdd6f4; }
            QToolBar { background: #181825; border-bottom: 1px solid #313244; spacing: 4px; }
            QStatusBar { background: #181825; color: #6c7086; font-size: 12px; }
            QSplitter::handle { background: #313244; }
        """)

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # 工具栏
        self._build_toolbar()

        # 主体区域（水平分割）
        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(3)

        # 左侧：播放列表管理
        left_panel = self._build_left_panel()
        splitter.addWidget(left_panel)

        # 中间：媒体素材区
        center_panel = self._build_center_panel()
        splitter.addWidget(center_panel)

        # 右侧：预览 + 信息
        right_panel = self._build_right_panel()
        splitter.addWidget(right_panel)

        splitter.setSizes([200, 700, 280])
        main_layout.addWidget(splitter)

        # 底部播放控制栏
        control_bar = self._build_control_bar()
        main_layout.addWidget(control_bar)

        # 状态栏
        self.statusBar().showMessage("就绪 | 请添加媒体文件开始使用")

    def _build_toolbar(self):
        tb = QToolBar("主工具栏")
        tb.setIconSize(QSize(20, 20))
        tb.setMovable(False)
        self.addToolBar(tb)

        # 添加媒体
        act_add = QAction("➕ 添加媒体", self)
        act_add.setShortcut(QKeySequence("Ctrl+O"))
        act_add.triggered.connect(self._add_media)
        tb.addAction(act_add)

        # 清空列表
        act_clear = QAction("🗑 清空列表", self)
        act_clear.triggered.connect(self._clear_playlist)
        tb.addAction(act_clear)

        tb.addSeparator()

        # 保存节目单
        act_save = QAction("💾 保存节目单", self)
        act_save.setShortcut(QKeySequence("Ctrl+S"))
        act_save.triggered.connect(self._save_playlist)
        tb.addAction(act_save)

        # 加载节目单
        act_load = QAction("📂 加载节目单", self)
        act_load.setShortcut(QKeySequence("Ctrl+L"))
        act_load.triggered.connect(self._load_playlist)
        tb.addAction(act_load)

        tb.addSeparator()

        # 黑屏
        self._act_black = QAction("⬛ 黑屏", self)
        self._act_black.setShortcut(QKeySequence("Ctrl+B"))
        self._act_black.setCheckable(True)
        self._act_black.triggered.connect(self._toggle_black)
        tb.addAction(self._act_black)

        tb.addSeparator()

        # 输出屏幕管理
        act_screen = QAction("🖥 输出屏幕", self)
        act_screen.triggered.connect(self._show_screen_settings)
        tb.addAction(act_screen)

        # 循环模式
        self._loop_btn = QPushButton("🔁 不循环")
        self._loop_btn.setCheckable(False)
        self._loop_btn.clicked.connect(self._toggle_loop)
        self._loop_btn.setFixedWidth(100)
        tb.addWidget(self._loop_btn)

        tb.addSeparator()

        # 音量
        tb.addWidget(QLabel(" 🔊"))
        self._vol_slider = QSlider(Qt.Horizontal)
        self._vol_slider.setRange(0, 100)
        self._vol_slider.setValue(100)
        self._vol_slider.setFixedWidth(100)
        self._vol_slider.valueChanged.connect(self._on_volume_changed)
        tb.addWidget(self._vol_slider)
        self._vol_label = QLabel("100%")
        self._vol_label.setFixedWidth(40)
        tb.addWidget(self._vol_label)

    def _build_left_panel(self):
        panel = QFrame()
        panel.setFrameShape(QFrame.StyledPanel)
        panel.setMaximumWidth(220)
        panel.setMinimumWidth(150)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        title = QLabel("📋 播放列表")
        title.setStyleSheet("font-weight: bold; font-size: 14px; color: #cba6f7; padding: 4px;")
        layout.addWidget(title)

        # 列表管理按钮
        btn_row = QHBoxLayout()
        btn_new = QPushButton("新建")
        btn_new.clicked.connect(self._new_playlist)
        btn_del = QPushButton("删除")
        btn_del.clicked.connect(self._delete_playlist)
        btn_row.addWidget(btn_new)
        btn_row.addWidget(btn_del)
        layout.addLayout(btn_row)

        # 播放列表选择
        self._playlist_list = QListWidget()
        self._playlist_list.setStyleSheet("""
            QListWidget {
                background: #181825;
                color: #cdd6f4;
                border: 1px solid #45475a;
                font-size: 13px;
            }
            QListWidget::item { padding: 6px; border-bottom: 1px solid #313244; }
            QListWidget::item:selected { background: #45475a; color: #cba6f7; }
        """)
        self._playlist_list.addItem("默认列表")
        self._playlist_list.setCurrentRow(0)
        self._playlist_list.currentRowChanged.connect(self._switch_playlist)
        layout.addWidget(self._playlist_list)

        return panel

    def _build_center_panel(self):
        panel = QFrame()
        panel.setFrameShape(QFrame.StyledPanel)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # 标题行
        title_row = QHBoxLayout()
        title = QLabel("🎬 媒体素材")
        title.setStyleSheet("font-weight: bold; font-size: 14px; color: #cba6f7; padding: 4px;")
        title_row.addWidget(title)
        title_row.addStretch()

        # 添加按钮
        btn_add = QPushButton("➕ 添加")
        btn_add.clicked.connect(self._add_media)
        title_row.addWidget(btn_add)
        layout.addLayout(title_row)

        # 媒体列表（支持拖拽排序）
        self._media_list = PlaylistWidget()
        self._media_list.itemDoubleClicked.connect(self._on_item_double_clicked)
        self._media_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self._media_list.customContextMenuRequested.connect(self._show_context_menu)
        self._media_list.setAcceptDrops(True)
        layout.addWidget(self._media_list)

        return panel

    def _build_right_panel(self):
        panel = QFrame()
        panel.setFrameShape(QFrame.StyledPanel)
        panel.setMaximumWidth(300)
        panel.setMinimumWidth(200)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(8)

        # 预览区
        preview_title = QLabel("👁 预览")
        preview_title.setStyleSheet("font-weight: bold; font-size: 14px; color: #cba6f7; padding: 4px;")
        layout.addWidget(preview_title)

        self._preview_video = QVideoWidget()
        self._preview_video.setFixedHeight(160)
        self._preview_video.setStyleSheet("background: black;")
        self._preview_player.setVideoOutput(self._preview_video)
        layout.addWidget(self._preview_video)

        self._preview_label = QLabel("（选中素材后预览）")
        self._preview_label.setAlignment(Qt.AlignCenter)
        self._preview_label.setStyleSheet("color: #6c7086; font-size: 12px;")
        layout.addWidget(self._preview_label)

        # 当前素材信息
        info_title = QLabel("ℹ 当前素材")
        info_title.setStyleSheet("font-weight: bold; font-size: 14px; color: #cba6f7; padding: 4px;")
        layout.addWidget(info_title)

        self._info_name = QLabel("—")
        self._info_name.setWordWrap(True)
        self._info_name.setStyleSheet("font-size: 12px; color: #a6e3a1;")
        layout.addWidget(self._info_name)

        self._info_type = QLabel("类型：—")
        self._info_type.setStyleSheet("font-size: 12px; color: #89b4fa;")
        layout.addWidget(self._info_type)

        self._info_duration = QLabel("时长：—")
        self._info_duration.setStyleSheet("font-size: 12px; color: #89dceb;")
        layout.addWidget(self._info_duration)

        layout.addStretch()

        # 下一个素材提示
        next_title = QLabel("⏭ 下一个")
        next_title.setStyleSheet("font-weight: bold; font-size: 13px; color: #cba6f7; padding: 4px;")
        layout.addWidget(next_title)

        self._next_label = QLabel("—")
        self._next_label.setWordWrap(True)
        self._next_label.setStyleSheet("font-size: 12px; color: #f38ba8;")
        layout.addWidget(self._next_label)

        return panel

    def _build_control_bar(self):
        bar = QFrame()
        bar.setStyleSheet("background: #181825; border-top: 1px solid #313244;")
        bar.setFixedHeight(80)
        layout = QVBoxLayout(bar)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(4)

        # 进度条
        progress_row = QHBoxLayout()
        self._time_label = QLabel("00:00:00")
        self._time_label.setStyleSheet("color: #a6e3a1; font-size: 12px; min-width: 60px;")
        self._progress = QSlider(Qt.Horizontal)
        self._progress.setRange(0, 1000)
        self._progress.sliderMoved.connect(self._on_seek)
        self._total_label = QLabel("00:00:00")
        self._total_label.setStyleSheet("color: #6c7086; font-size: 12px; min-width: 60px;")
        progress_row.addWidget(self._time_label)
        progress_row.addWidget(self._progress)
        progress_row.addWidget(self._total_label)
        layout.addLayout(progress_row)

        # 控制按钮行
        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)

        btn_style = "font-size: 18px; min-width: 40px; min-height: 36px; border-radius: 6px;"

        self._btn_prev = QPushButton("⏮")
        self._btn_prev.setStyleSheet(btn_style)
        self._btn_prev.setToolTip("上一个 (Ctrl+Left)")
        self._btn_prev.clicked.connect(self._play_prev)

        self._btn_play = QPushButton("▶")
        self._btn_play.setStyleSheet(btn_style + "min-width: 60px; background: #313244;")
        self._btn_play.setToolTip("播放/暂停 (Space)")
        self._btn_play.clicked.connect(self._toggle_play)

        self._btn_stop = QPushButton("⏹")
        self._btn_stop.setStyleSheet(btn_style)
        self._btn_stop.setToolTip("停止")
        self._btn_stop.clicked.connect(self._stop)

        self._btn_next = QPushButton("⏭")
        self._btn_next.setStyleSheet(btn_style)
        self._btn_next.setToolTip("下一个 (Ctrl+Right)")
        self._btn_next.clicked.connect(self._play_next)

        btn_row.addStretch()
        btn_row.addWidget(self._btn_prev)
        btn_row.addWidget(self._btn_play)
        btn_row.addWidget(self._btn_stop)
        btn_row.addWidget(self._btn_next)
        btn_row.addStretch()

        layout.addLayout(btn_row)

        return bar

    def _build_menu(self):
        menubar = self.menuBar()
        menubar.setStyleSheet("QMenuBar { background: #181825; color: #cdd6f4; }"
                              "QMenuBar::item:selected { background: #313244; }"
                              "QMenu { background: #1e1e2e; color: #cdd6f4; border: 1px solid #45475a; }"
                              "QMenu::item:selected { background: #313244; }")

        # 文件菜单
        file_menu = menubar.addMenu("文件(&F)")
        file_menu.addAction("添加媒体文件 (Ctrl+O)", self._add_media)
        file_menu.addAction("添加文件夹", self._add_folder)
        file_menu.addSeparator()
        file_menu.addAction("保存节目单 (Ctrl+S)", self._save_playlist)
        file_menu.addAction("加载节目单 (Ctrl+L)", self._load_playlist)
        file_menu.addSeparator()
        file_menu.addAction("退出", self.close)

        # 播放菜单
        play_menu = menubar.addMenu("播放(&P)")
        play_menu.addAction("播放/暂停 (Space)", self._toggle_play)
        play_menu.addAction("停止", self._stop)
        play_menu.addAction("上一个 (Ctrl+←)", self._play_prev)
        play_menu.addAction("下一个 (Ctrl+→)", self._play_next)
        play_menu.addSeparator()
        play_menu.addAction("黑屏 (Ctrl+B)", self._toggle_black)

        # 屏幕菜单
        screen_menu = menubar.addMenu("屏幕(&S)")
        screen_menu.addAction("输出屏幕设置", self._show_screen_settings)
        screen_menu.addAction("显示/隐藏输出窗口", self._toggle_output)

        # 帮助菜单
        help_menu = menubar.addMenu("帮助(&H)")
        help_menu.addAction("关于", self._show_about)

    # ==================== 快捷键 ====================

    def keyPressEvent(self, event):
        key = event.key()
        mod = event.modifiers()
        if key == Qt.Key_Space:
            self._toggle_play()
        elif key == Qt.Key_B and mod == Qt.ControlModifier:
            self._toggle_black()
        elif key == Qt.Key_Left and mod == Qt.ControlModifier:
            self._play_prev()
        elif key == Qt.Key_Right and mod == Qt.ControlModifier:
            self._play_next()
        else:
            super().keyPressEvent(event)

    # ==================== 播放列表管理 ====================

    def _new_playlist(self):
        name, ok = QInputDialog.getText(self, "新建列表", "列表名称：")
        if ok and name.strip():
            name = name.strip()
            if name not in self._playlists:
                self._playlists[name] = []
                self._playlist_list.addItem(name)
                self._playlist_list.setCurrentRow(self._playlist_list.count() - 1)

    def _delete_playlist(self):
        if self._playlist_list.count() <= 1:
            QMessageBox.warning(self, "提示", "至少保留一个播放列表！")
            return
        row = self._playlist_list.currentRow()
        if row < 0:
            return
        name = self._playlist_list.item(row).text()
        del self._playlists[name]
        self._playlist_list.takeItem(row)
        self._playlist_list.setCurrentRow(0)

    def _switch_playlist(self, row):
        if row < 0:
            return
        name = self._playlist_list.item(row).text()
        self._current_playlist = name
        self._refresh_media_list()

    def _refresh_media_list(self):
        self._media_list.clear()
        items = self._playlists.get(self._current_playlist, [])
        for i, item in enumerate(items):
            icon = {"video": "🎬", "image": "🖼", "audio": "🎵"}.get(item.media_type, "📄")
            text = f"{icon} {item.name}"
            list_item = QListWidgetItem(text)
            list_item.setData(Qt.UserRole, i)
            if i == self._current_index:
                list_item.setForeground(QColor("#a6e3a1"))
                list_item.setText(f"▶ {text}")
            self._media_list.addItem(list_item)

    # ==================== 媒体操作 ====================

    def _add_media(self):
        exts = " ".join([f"*{e}" for e in sorted(VIDEO_EXT | IMAGE_EXT | AUDIO_EXT)])
        paths, _ = QFileDialog.getOpenFileNames(
            self, "添加媒体文件", "",
            f"媒体文件 ({exts});;视频文件 (*.mp4 *.avi *.mkv *.mov *.wmv *.flv *.ts);;图片文件 (*.jpg *.jpeg *.png *.bmp *.gif);;音频文件 (*.mp3 *.wav *.aac *.flac);;所有文件 (*.*)"
        )
        for path in paths:
            self._add_item(path)

    def _add_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "选择文件夹")
        if not folder:
            return
        all_ext = VIDEO_EXT | IMAGE_EXT | AUDIO_EXT
        count = 0
        for fname in sorted(os.listdir(folder)):
            ext = os.path.splitext(fname)[1].lower()
            if ext in all_ext:
                self._add_item(os.path.join(folder, fname))
                count += 1
        self.statusBar().showMessage(f"已添加 {count} 个文件")

    def _add_item(self, path):
        if not os.path.exists(path):
            return
        item = MediaItem(path)
        self._playlists[self._current_playlist].append(item)
        self._refresh_media_list()
        self.statusBar().showMessage(f"已添加：{item.name}")

    def _clear_playlist(self):
        if QMessageBox.question(self, "确认", "清空当前播放列表？") == QMessageBox.Yes:
            self._playlists[self._current_playlist].clear()
            self._current_index = -1
            self._refresh_media_list()

    def _show_context_menu(self, pos):
        row = self._media_list.currentRow()
        if row < 0:
            return
        menu = QMenu(self)
        menu.setStyleSheet("QMenu { background: #1e1e2e; color: #cdd6f4; border: 1px solid #45475a; }"
                           "QMenu::item:selected { background: #313244; }")
        menu.addAction("▶ 播放此素材", lambda: self._play_at(row))
        menu.addAction("👁 预览", lambda: self._preview_item(row))
        menu.addSeparator()
        menu.addAction("⬆ 上移", lambda: self._move_item(row, -1))
        menu.addAction("⬇ 下移", lambda: self._move_item(row, 1))
        menu.addSeparator()
        menu.addAction("⚙ 属性", lambda: self._show_properties(row))
        menu.addSeparator()
        menu.addAction("🗑 删除", lambda: self._remove_item(row))
        menu.exec_(self._media_list.mapToGlobal(pos))

    def _on_item_double_clicked(self, item):
        row = self._media_list.currentRow()
        self._play_at(row)

    def _move_item(self, row, direction):
        items = self._playlists[self._current_playlist]
        new_row = row + direction
        if 0 <= new_row < len(items):
            items[row], items[new_row] = items[new_row], items[row]
            self._refresh_media_list()
            self._media_list.setCurrentRow(new_row)

    def _remove_item(self, row):
        items = self._playlists[self._current_playlist]
        if 0 <= row < len(items):
            items.pop(row)
            self._refresh_media_list()

    def _show_properties(self, row):
        items = self._playlists[self._current_playlist]
        if row < 0 or row >= len(items):
            return
        item = items[row]
        dlg = PropertiesDialog(item, self)
        dlg.exec_()

    def _preview_item(self, row):
        items = self._playlists[self._current_playlist]
        if row < 0 or row >= len(items):
            return
        item = items[row]
        if item.media_type in ('video', 'audio'):
            self._preview_player.setMedia(QMediaContent(QUrl.fromLocalFile(item.path)))
            self._preview_player.play()
            self._preview_label.setText(f"预览：{item.name}")
        elif item.media_type == 'image':
            pixmap = QPixmap(item.path)
            if not pixmap.isNull():
                scaled = pixmap.scaled(280, 160, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                self._preview_label.setPixmap(scaled)

    # ==================== 播放控制 ====================

    def _play_at(self, index):
        items = self._playlists[self._current_playlist]
        if index < 0 or index >= len(items):
            return

        self._current_index = index
        item = items[index]

        # 更新信息面板
        self._info_name.setText(item.name)
        type_names = {'video': '视频', 'image': '图片', 'audio': '音频'}
        self._info_type.setText(f"类型：{type_names.get(item.media_type, '未知')}")

        # 更新下一个素材提示
        next_idx = index + 1
        if next_idx < len(items):
            self._next_label.setText(items[next_idx].name)
        else:
            self._next_label.setText("（列表末尾）")

        # 刷新列表高亮
        self._refresh_media_list()
        self._media_list.setCurrentRow(index)

        # 播放
        if item.media_type == 'image':
            self._play_image(item)
        elif item.media_type in ('video', 'audio'):
            self._play_video_audio(item)

        self.statusBar().showMessage(f"正在播放：{item.name}")

    def _play_image(self, item):
        """播放图片（显示指定秒数后自动切下一个）"""
        self._player.stop()
        self._image_timer.stop()
        self._output.show_image(item.path)
        duration_ms = item.duration_ms if item.duration_ms > 0 else 5000
        self._info_duration.setText(f"时长：{duration_ms // 1000} 秒")
        self._total_label.setText(format_ms(duration_ms))
        self._time_label.setText("00:00:00")
        self._btn_play.setText("⏸")

        # 启动图片计时器
        if not item.loop:
            self._image_timer.start(duration_ms)

    def _play_video_audio(self, item):
        """播放视频或音频"""
        self._image_timer.stop()
        self._output.show_video()
        url = QUrl.fromLocalFile(item.path)
        self._player.setMedia(QMediaContent(url))
        self._player.setVolume(item.volume)
        self._player.play()
        self._btn_play.setText("⏸")

    def _toggle_play(self):
        items = self._playlists[self._current_playlist]
        if not items:
            return
        if self._current_index < 0:
            self._play_at(0)
            return

        item = items[self._current_index]
        if item.media_type == 'image':
            # 图片：暂停/继续计时
            if self._image_timer.isActive():
                self._image_timer.stop()
                self._btn_play.setText("▶")
            else:
                remaining = self._image_timer.remainingTime()
                if remaining > 0:
                    self._image_timer.start(remaining)
                self._btn_play.setText("⏸")
        else:
            state = self._player.state()
            if state == QMediaPlayer.PlayingState:
                self._player.pause()
                self._btn_play.setText("▶")
            elif state in (QMediaPlayer.PausedState, QMediaPlayer.StoppedState):
                self._player.play()
                self._btn_play.setText("⏸")

    def _stop(self):
        self._player.stop()
        self._image_timer.stop()
        self._btn_play.setText("▶")
        self._progress.setValue(0)
        self._time_label.setText("00:00:00")
        self.statusBar().showMessage("已停止")

    def _play_prev(self):
        if self._current_index > 0:
            self._play_at(self._current_index - 1)

    def _play_next(self):
        items = self._playlists[self._current_playlist]
        if not items:
            return
        next_idx = self._current_index + 1
        if self._loop_mode == 1:  # 单曲循环
            self._play_at(self._current_index)
        elif next_idx < len(items):
            self._play_at(next_idx)
        elif self._loop_mode == 2:  # 列表循环
            self._play_at(0)
        else:
            self._stop()
            self.statusBar().showMessage("播放完毕")

    def _on_image_timer(self):
        """图片计时结束，播放下一个"""
        self._play_next()

    def _toggle_loop(self):
        self._loop_mode = (self._loop_mode + 1) % 3
        labels = ["🔁 不循环", "🔂 单曲循环", "🔁 列表循环"]
        self._loop_btn.setText(labels[self._loop_mode])

    def _toggle_black(self):
        self._is_black = not self._is_black
        self._output.set_black(self._is_black)
        self._act_black.setChecked(self._is_black)
        self.statusBar().showMessage("黑屏已开启" if self._is_black else "黑屏已关闭")

    # ==================== 播放器事件 ====================

    def _on_position_changed(self, pos_ms):
        dur = self._player.duration()
        if dur > 0:
            self._progress.setValue(int(pos_ms / dur * 1000))
        self._time_label.setText(format_ms(pos_ms))

    def _on_duration_changed(self, dur_ms):
        self._total_label.setText(format_ms(dur_ms))
        items = self._playlists[self._current_playlist]
        if 0 <= self._current_index < len(items):
            items[self._current_index].duration_ms = dur_ms
            self._info_duration.setText(f"时长：{format_ms(dur_ms)}")

    def _on_state_changed(self, state):
        if state == QMediaPlayer.StoppedState:
            # 视频播放结束，自动播放下一个
            if self._player.position() >= self._player.duration() - 100 and self._player.duration() > 0:
                self._play_next()

    def _on_player_error(self, error):
        if error != QMediaPlayer.NoError:
            err_msg = self._player.errorString()
            self.statusBar().showMessage(f"播放错误：{err_msg}")

    def _on_seek(self, value):
        dur = self._player.duration()
        if dur > 0:
            self._player.setPosition(int(value / 1000 * dur))

    def _on_volume_changed(self, value):
        self._player.setVolume(value)
        self._vol_label.setText(f"{value}%")

    # ==================== 屏幕管理 ====================

    def _move_output_to_screen2(self):
        """将输出窗口移到第二个屏幕"""
        screens = QApplication.screens()
        if len(screens) >= 2:
            screen2 = screens[1]
            geo = screen2.geometry()
            # 先设置位置，再显示，再全屏
            self._output.setGeometry(geo)
            self._output.show()
            QApplication.processEvents()  # 确保窗口真正显示
            self._output.showFullScreen()
            QApplication.processEvents()  # 确保全屏生效
            msg = f"检测到 {len(screens)} 个屏幕，输出到屏幕2（{geo.width()}x{geo.height()}）"
        else:
            # 单屏模式：输出窗口作为独立窗口
            self._output.resize(960, 540)
            self._output.show()
            QApplication.processEvents()
            msg = "单屏模式：输出窗口已显示（建议连接第二个屏幕）"
        self.statusBar().showMessage(msg)

    def _show_screen_settings(self):
        screens = QApplication.screens()
        msg = f"检测到 {len(screens)} 个屏幕：\n\n"
        for i, s in enumerate(screens):
            geo = s.geometry()
            msg += f"屏幕 {i+1}：{geo.width()}x{geo.height()} @ ({geo.x()},{geo.y()})\n"
        msg += "\n当前输出窗口在屏幕 " + ("2（全屏）" if len(screens) >= 2 else "1（窗口模式）")

        dlg = QMessageBox(self)
        dlg.setWindowTitle("屏幕信息")
        dlg.setText(msg)
        dlg.addButton("重新分配屏幕", QMessageBox.ActionRole)
        dlg.addButton("关闭", QMessageBox.RejectRole)
        result = dlg.exec_()
        if result == 0:
            self._move_output_to_screen2()

    def _toggle_output(self):
        if self._output.isVisible():
            self._output.hide()
        else:
            self._output.show()

    # ==================== 节目单保存/加载 ====================

    def _save_playlist(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "保存节目单", "", "节目单文件 (*.json)"
        )
        if not path:
            return
        data = {}
        for name, items in self._playlists.items():
            data[name] = [
                {"path": it.path, "duration_ms": it.duration_ms,
                 "loop": it.loop, "volume": it.volume}
                for it in items
            ]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        self.statusBar().showMessage(f"节目单已保存：{path}")

    def _load_playlist(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "加载节目单", "", "节目单文件 (*.json)"
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._playlists.clear()
            self._playlist_list.clear()
            for name, items in data.items():
                self._playlists[name] = []
                self._playlist_list.addItem(name)
                for it_data in items:
                    if os.path.exists(it_data["path"]):
                        item = MediaItem(it_data["path"])
                        item.duration_ms = it_data.get("duration_ms", 0)
                        item.loop = it_data.get("loop", False)
                        item.volume = it_data.get("volume", 100)
                        self._playlists[name].append(item)
            self._playlist_list.setCurrentRow(0)
            self._current_index = -1
            self.statusBar().showMessage(f"节目单已加载：{path}")
        except Exception as e:
            QMessageBox.critical(self, "加载失败", f"无法加载节目单：{str(e)}")

    # ==================== 其他 ====================

    def _show_about(self):
        QMessageBox.about(self, "关于双屏播放器",
                          "双屏播放器 v2.0\n\n"
                          "功能：\n"
                          "• 双屏输出（控制台 + 大屏）\n"
                          "• 支持视频/图片/音频\n"
                          "• 播放列表管理\n"
                          "• 节目单保存/加载\n"
                          "• 黑屏控制\n"
                          "• 循环播放\n\n"
                          "快捷键：\n"
                          "Space - 播放/暂停\n"
                          "Ctrl+B - 黑屏\n"
                          "Ctrl+← - 上一个\n"
                          "Ctrl+→ - 下一个")

    def closeEvent(self, event):
        self._player.stop()
        self._preview_player.stop()
        self._output.close()
        event.accept()


class PropertiesDialog(QDialog):
    """素材属性设置对话框"""
    def __init__(self, item: MediaItem, parent=None):
        super().__init__(parent)
        self.item = item
        self.setWindowTitle(f"属性 - {item.name}")
        self.setModal(True)
        self.resize(360, 220)

        layout = QFormLayout(self)

        # 文件路径
        path_label = QLabel(item.path)
        path_label.setWordWrap(True)
        path_label.setStyleSheet("color: #89b4fa; font-size: 11px;")
        layout.addRow("文件路径：", path_label)

        # 图片显示时长（仅图片有效）
        self._duration_spin = QSpinBox()
        self._duration_spin.setRange(1, 3600)
        self._duration_spin.setValue(max(1, item.duration_ms // 1000))
        self._duration_spin.setSuffix(" 秒")
        self._duration_spin.setEnabled(item.media_type == 'image')
        layout.addRow("图片显示时长：", self._duration_spin)

        # 音量
        self._vol_spin = QSpinBox()
        self._vol_spin.setRange(0, 100)
        self._vol_spin.setValue(item.volume)
        self._vol_spin.setSuffix(" %")
        layout.addRow("音量：", self._vol_spin)

        # 循环
        self._loop_check = QCheckBox("循环播放此素材")
        self._loop_check.setChecked(item.loop)
        layout.addRow("", self._loop_check)

        # 按钮
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self._save)
        btns.rejected.connect(self.reject)
        layout.addRow(btns)

    def _save(self):
        self.item.duration_ms = self._duration_spin.value() * 1000
        self.item.volume = self._vol_spin.value()
        self.item.loop = self._loop_check.isChecked()
        self.accept()
