"""
双屏播放器 v3.0 主界面
使用 VLC 内核播放，支持所有视频格式
"""
import os
import sys
import json
import ctypes
from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QSplitter,
    QListWidget, QListWidgetItem, QLabel, QPushButton, QSlider,
    QToolBar, QAction, QFileDialog, QMessageBox, QFrame,
    QMenu, QInputDialog, QApplication, QDialog,
    QDialogButtonBox, QFormLayout, QSpinBox, QCheckBox, QSizePolicy
)
from PyQt5.QtCore import Qt, QTimer, QSize, QUrl
from PyQt5.QtGui import QColor, QPixmap, QKeySequence

# ============================================================
# VLC 初始化 - 智能查找 VLC 安装路径
# ============================================================

def _find_vlc_path():
    """在 Windows 上自动查找 VLC 安装目录"""
    candidates = [
        r"C:\Program Files\VideoLAN\VLC",
        r"C:\Program Files (x86)\VideoLAN\VLC",
        r"D:\Program Files\VideoLAN\VLC",
        r"D:\Program Files (x86)\VideoLAN\VLC",
    ]
    # 从注册表查找
    try:
        import winreg
        for key_path in [
            r"SOFTWARE\VideoLAN\VLC",
            r"SOFTWARE\WOW6432Node\VideoLAN\VLC",
        ]:
            try:
                key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path)
                install_dir, _ = winreg.QueryValueEx(key, "InstallDir")
                if install_dir and os.path.isdir(install_dir):
                    return install_dir
            except Exception:
                pass
    except ImportError:
        pass
    # 从候选路径查找
    for path in candidates:
        if os.path.isdir(path) and os.path.exists(os.path.join(path, "libvlc.dll")):
            return path
    return None


def _init_vlc():
    """初始化 VLC，返回 (vlc模块, 错误信息)"""
    vlc_dir = _find_vlc_path()
    if vlc_dir:
        # 把 VLC 目录加到 PATH，让 python-vlc 能找到 dll
        os.environ["PATH"] = vlc_dir + os.pathsep + os.environ.get("PATH", "")
        # 也加到 DLL 搜索路径（Python 3.8+）
        try:
            os.add_dll_directory(vlc_dir)
        except (AttributeError, OSError):
            pass
    try:
        import vlc as _vlc
        # 测试能否创建实例
        inst = _vlc.Instance("--no-xlib")
        if inst is None:
            return None, "VLC 实例创建失败"
        inst.release()
        return _vlc, None
    except Exception as e:
        return None, str(e)


# 全局 VLC 模块
_vlc_mod, _vlc_error = _init_vlc()

# ============================================================
# 媒体类型定义
# ============================================================
VIDEO_EXT = {'.mp4', '.avi', '.mkv', '.mov', '.wmv', '.flv', '.ts', '.m4v',
             '.rmvb', '.rm', '.3gp', '.webm', '.mpg', '.mpeg', '.m2ts', '.mts'}
IMAGE_EXT = {'.jpg', '.jpeg', '.png', '.bmp', '.gif', '.tiff', '.webp'}
AUDIO_EXT = {'.mp3', '.wav', '.aac', '.flac', '.ogg', '.wma', '.m4a'}


def format_ms(ms):
    if ms < 0:
        return "00:00:00"
    total = int(ms) // 1000
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


# ============================================================
# 输出窗口（大屏）
# ============================================================
class OutputWindow(QWidget):
    """大屏输出窗口 - 全屏显示在第二个屏幕"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("双屏播放器 - 输出屏幕")
        self.setStyleSheet("background-color: black;")
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint)
        # 强制创建原生 Win32 窗口句柄
        self.setAttribute(Qt.WA_NativeWindow, True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # VLC 视频渲染区域（纯黑 Widget，VLC 直接渲染到它上面）
        self.video_frame = QWidget(self)
        self.video_frame.setStyleSheet("background-color: black;")
        self.video_frame.setAttribute(Qt.WA_NativeWindow, True)
        self.video_frame.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout.addWidget(self.video_frame)

        # 图片显示标签
        self.image_label = QLabel(self)
        self.image_label.setAlignment(Qt.AlignCenter)
        self.image_label.setStyleSheet("background-color: black;")
        self.image_label.hide()
        layout.addWidget(self.image_label)

        # 黑屏遮罩
        self._black_overlay = QWidget(self)
        self._black_overlay.setStyleSheet("background-color: black;")
        self._black_overlay.hide()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._black_overlay.resize(self.size())
        # 图片重新缩放
        if not self.image_label.isHidden() and self.image_label.pixmap():
            pix = self.image_label.pixmap()
            if pix and not pix.isNull():
                scaled = pix.scaled(self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
                self.image_label.setPixmap(scaled)

    def show_image(self, path):
        self.video_frame.hide()
        self.image_label.show()
        pixmap = QPixmap(path)
        if not pixmap.isNull():
            scaled = pixmap.scaled(self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.image_label.setPixmap(scaled)

    def show_video(self):
        self.image_label.hide()
        self.video_frame.show()

    def set_black(self, black: bool):
        if black:
            self._black_overlay.resize(self.size())
            self._black_overlay.show()
            self._black_overlay.raise_()
        else:
            self._black_overlay.hide()

    def get_video_hwnd(self):
        """获取视频渲染区域的 Win32 窗口句柄"""
        return int(self.video_frame.winId())

    def keyPressEvent(self, event):
        pass  # 屏蔽 ESC 等按键


# ============================================================
# 媒体素材数据类
# ============================================================
class MediaItem:
    def __init__(self, path):
        self.path = path
        self.name = os.path.basename(path)
        self.media_type = get_media_type(path)
        self.duration_ms = 0
        self.loop = False
        self.volume = 100


# ============================================================
# 播放列表控件
# ============================================================
class PlaylistWidget(QListWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragDropMode(QListWidget.InternalMove)
        self.setSelectionMode(QListWidget.SingleSelection)
        self.setStyleSheet("""
            QListWidget {
                background: #1e1e2e; color: #cdd6f4;
                border: 1px solid #45475a; font-size: 13px;
            }
            QListWidget::item { padding: 6px 8px; border-bottom: 1px solid #313244; }
            QListWidget::item:selected { background: #313244; color: #cba6f7; }
            QListWidget::item:hover { background: #2a2a3e; }
        """)


# ============================================================
# 主窗口
# ============================================================
class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("双屏播放器 v3.0")
        self.resize(1280, 800)
        self.setMinimumSize(900, 600)
        self._apply_dark_theme()

        # 检查 VLC
        if _vlc_mod is None:
            QMessageBox.critical(self, "VLC 未找到",
                f"未找到 VLC 播放器！\n\n错误：{_vlc_error}\n\n"
                "请安装 VLC 播放器（64位）：\nhttps://www.videolan.org/vlc/\n\n"
                "安装后重新启动本程序。")

        # 数据
        self._playlists = {"默认列表": []}
        self._current_playlist = "默认列表"
        self._current_index = -1
        self._is_black = False
        self._loop_mode = 0  # 0=不循环 1=单曲 2=列表

        # 图片计时器
        self._image_timer = QTimer()
        self._image_timer.setSingleShot(True)
        self._image_timer.timeout.connect(self._on_image_timer)

        # 进度条刷新计时器
        self._progress_timer = QTimer()
        self._progress_timer.setInterval(500)
        self._progress_timer.timeout.connect(self._refresh_progress)

        # 输出窗口
        self._output = OutputWindow()

        # VLC 实例和播放器
        self._vlc_instance = None
        self._vlc_player = None
        self._init_vlc_player()

        # 构建界面
        self._build_ui()
        self._build_menu()

        # 移动输出窗口到第二屏
        self._move_output_to_screen2()

        # 绑定 VLC 渲染到输出窗口（必须在窗口显示后）
        self._bind_vlc_output()

    def _init_vlc_player(self):
        """初始化 VLC 播放器"""
        if _vlc_mod is None:
            return
        try:
            self._vlc_instance = _vlc_mod.Instance(
                "--no-xlib",
                "--quiet",
                "--no-video-title-show",
            )
            self._vlc_player = self._vlc_instance.media_player_new()
            # 监听播放结束事件
            em = self._vlc_player.event_manager()
            em.event_attach(_vlc_mod.EventType.MediaPlayerEndReached,
                            self._on_vlc_end_reached)
            em.event_attach(_vlc_mod.EventType.MediaPlayerEncounteredError,
                            self._on_vlc_error)
        except Exception as e:
            self._vlc_player = None
            self.statusBar().showMessage(f"VLC 初始化失败: {e}")

    def _bind_vlc_output(self):
        """把 VLC 视频输出绑定到输出窗口（必须在窗口显示后调用）"""
        if self._vlc_player is None:
            return
        try:
            QApplication.processEvents()
            hwnd = self._output.get_video_hwnd()
            if sys.platform == "win32":
                self._vlc_player.set_hwnd(hwnd)
            elif sys.platform == "darwin":
                self._vlc_player.set_nsobject(hwnd)
            else:
                self._vlc_player.set_xwindow(hwnd)
        except Exception as e:
            self.statusBar().showMessage(f"VLC 绑定失败: {e}")

    # ==================== 深色主题 ====================

    def _apply_dark_theme(self):
        self.setStyleSheet("""
            QMainWindow, QWidget {
                background-color: #1e1e2e; color: #cdd6f4;
            }
            QPushButton {
                background-color: #313244; color: #cdd6f4;
                border: 1px solid #45475a; border-radius: 4px;
                padding: 5px 12px; font-size: 13px;
            }
            QPushButton:hover { background-color: #45475a; }
            QPushButton:pressed { background-color: #585b70; }
            QPushButton:checked { background-color: #cba6f7; color: #1e1e2e; }
            QSlider::groove:horizontal {
                height: 4px; background: #45475a; border-radius: 2px;
            }
            QSlider::handle:horizontal {
                width: 14px; height: 14px; background: #cba6f7;
                border-radius: 7px; margin: -5px 0;
            }
            QSlider::sub-page:horizontal { background: #cba6f7; border-radius: 2px; }
            QLabel { color: #cdd6f4; }
            QToolBar { background: #181825; border-bottom: 1px solid #313244; spacing: 4px; }
            QStatusBar { background: #181825; color: #6c7086; font-size: 12px; }
            QSplitter::handle { background: #313244; }
            QMenuBar { background: #181825; color: #cdd6f4; }
            QMenuBar::item:selected { background: #313244; }
            QMenu { background: #1e1e2e; color: #cdd6f4; border: 1px solid #45475a; }
            QMenu::item:selected { background: #313244; }
        """)

    # ==================== 界面构建 ====================

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        self._build_toolbar()

        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(3)
        splitter.addWidget(self._build_left_panel())
        splitter.addWidget(self._build_center_panel())
        splitter.addWidget(self._build_right_panel())
        splitter.setSizes([200, 700, 280])
        main_layout.addWidget(splitter)

        main_layout.addWidget(self._build_control_bar())
        self.statusBar().showMessage("就绪 | 请添加媒体文件开始使用")

    def _build_toolbar(self):
        tb = QToolBar("主工具栏")
        tb.setIconSize(QSize(20, 20))
        tb.setMovable(False)
        self.addToolBar(tb)

        def act(label, shortcut, slot):
            a = QAction(label, self)
            if shortcut:
                a.setShortcut(QKeySequence(shortcut))
            a.triggered.connect(slot)
            tb.addAction(a)
            return a

        act("➕ 添加媒体", "Ctrl+O", self._add_media)
        act("🗑 清空列表", None, self._clear_playlist)
        tb.addSeparator()
        act("💾 保存节目单", "Ctrl+S", self._save_playlist)
        act("📂 加载节目单", "Ctrl+L", self._load_playlist)
        tb.addSeparator()

        self._act_black = QAction("⬛ 黑屏", self)
        self._act_black.setShortcut(QKeySequence("Ctrl+B"))
        self._act_black.setCheckable(True)
        self._act_black.triggered.connect(self._toggle_black)
        tb.addAction(self._act_black)

        act("🖥 输出屏幕", None, self._show_screen_settings)
        tb.addSeparator()

        self._loop_btn = QPushButton("🔁 不循环")
        self._loop_btn.setFixedWidth(110)
        self._loop_btn.clicked.connect(self._toggle_loop)
        tb.addWidget(self._loop_btn)

        tb.addSeparator()
        tb.addWidget(QLabel(" 🔊 "))
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
        title.setStyleSheet("font-weight:bold; font-size:14px; color:#cba6f7; padding:4px;")
        layout.addWidget(title)

        btn_row = QHBoxLayout()
        btn_new = QPushButton("新建")
        btn_new.clicked.connect(self._new_playlist)
        btn_del = QPushButton("删除")
        btn_del.clicked.connect(self._delete_playlist)
        btn_row.addWidget(btn_new)
        btn_row.addWidget(btn_del)
        layout.addLayout(btn_row)

        self._playlist_list = QListWidget()
        self._playlist_list.setStyleSheet("""
            QListWidget { background:#181825; color:#cdd6f4; border:1px solid #45475a; font-size:13px; }
            QListWidget::item { padding:6px; border-bottom:1px solid #313244; }
            QListWidget::item:selected { background:#45475a; color:#cba6f7; }
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

        title_row = QHBoxLayout()
        title = QLabel("🎬 媒体素材")
        title.setStyleSheet("font-weight:bold; font-size:14px; color:#cba6f7; padding:4px;")
        title_row.addWidget(title)
        title_row.addStretch()
        btn_add = QPushButton("➕ 添加")
        btn_add.clicked.connect(self._add_media)
        title_row.addWidget(btn_add)
        layout.addLayout(title_row)

        self._media_list = PlaylistWidget()
        self._media_list.itemDoubleClicked.connect(self._on_item_double_clicked)
        self._media_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self._media_list.customContextMenuRequested.connect(self._show_context_menu)
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

        preview_title = QLabel("👁 预览")
        preview_title.setStyleSheet("font-weight:bold; font-size:14px; color:#cba6f7; padding:4px;")
        layout.addWidget(preview_title)

        self._preview_label = QLabel("（选中素材后预览）")
        self._preview_label.setFixedHeight(160)
        self._preview_label.setAlignment(Qt.AlignCenter)
        self._preview_label.setStyleSheet("background:black; color:#6c7086; font-size:12px;")
        layout.addWidget(self._preview_label)

        info_title = QLabel("ℹ 当前素材")
        info_title.setStyleSheet("font-weight:bold; font-size:14px; color:#cba6f7; padding:4px;")
        layout.addWidget(info_title)

        self._info_name = QLabel("—")
        self._info_name.setWordWrap(True)
        self._info_name.setStyleSheet("font-size:12px; color:#a6e3a1;")
        layout.addWidget(self._info_name)

        self._info_type = QLabel("类型：—")
        self._info_type.setStyleSheet("font-size:12px; color:#89b4fa;")
        layout.addWidget(self._info_type)

        self._info_duration = QLabel("时长：—")
        self._info_duration.setStyleSheet("font-size:12px; color:#89dceb;")
        layout.addWidget(self._info_duration)

        layout.addStretch()

        next_title = QLabel("⏭ 下一个")
        next_title.setStyleSheet("font-weight:bold; font-size:13px; color:#cba6f7; padding:4px;")
        layout.addWidget(next_title)

        self._next_label = QLabel("—")
        self._next_label.setWordWrap(True)
        self._next_label.setStyleSheet("font-size:12px; color:#f38ba8;")
        layout.addWidget(self._next_label)
        return panel

    def _build_control_bar(self):
        bar = QFrame()
        bar.setStyleSheet("background:#181825; border-top:1px solid #313244;")
        bar.setFixedHeight(80)
        layout = QVBoxLayout(bar)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(4)

        # 进度条
        prog_row = QHBoxLayout()
        self._time_label = QLabel("00:00:00")
        self._time_label.setStyleSheet("color:#a6e3a1; font-size:12px; min-width:60px;")
        self._progress = QSlider(Qt.Horizontal)
        self._progress.setRange(0, 1000)
        self._progress.sliderMoved.connect(self._on_seek)
        self._total_label = QLabel("00:00:00")
        self._total_label.setStyleSheet("color:#6c7086; font-size:12px; min-width:60px;")
        prog_row.addWidget(self._time_label)
        prog_row.addWidget(self._progress)
        prog_row.addWidget(self._total_label)
        layout.addLayout(prog_row)

        # 按钮
        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        s = "font-size:18px; min-width:40px; min-height:36px; border-radius:6px;"

        self._btn_prev = QPushButton("⏮"); self._btn_prev.setStyleSheet(s)
        self._btn_prev.setToolTip("上一个 (Ctrl+Left)")
        self._btn_prev.clicked.connect(self._play_prev)

        self._btn_play = QPushButton("▶")
        self._btn_play.setStyleSheet(s + "min-width:60px;")
        self._btn_play.setToolTip("播放/暂停 (Space)")
        self._btn_play.clicked.connect(self._toggle_play)

        self._btn_stop = QPushButton("⏹"); self._btn_stop.setStyleSheet(s)
        self._btn_stop.setToolTip("停止")
        self._btn_stop.clicked.connect(self._stop)

        self._btn_next = QPushButton("⏭"); self._btn_next.setStyleSheet(s)
        self._btn_next.setToolTip("下一个 (Ctrl+Right)")
        self._btn_next.clicked.connect(self._play_next)

        btn_row.addStretch()
        for b in [self._btn_prev, self._btn_play, self._btn_stop, self._btn_next]:
            btn_row.addWidget(b)
        btn_row.addStretch()
        layout.addLayout(btn_row)
        return bar

    def _build_menu(self):
        mb = self.menuBar()
        fm = mb.addMenu("文件(&F)")
        fm.addAction("添加媒体文件 (Ctrl+O)", self._add_media)
        fm.addAction("添加文件夹", self._add_folder)
        fm.addSeparator()
        fm.addAction("保存节目单 (Ctrl+S)", self._save_playlist)
        fm.addAction("加载节目单 (Ctrl+L)", self._load_playlist)
        fm.addSeparator()
        fm.addAction("退出", self.close)

        pm = mb.addMenu("播放(&P)")
        pm.addAction("播放/暂停 (Space)", self._toggle_play)
        pm.addAction("停止", self._stop)
        pm.addAction("上一个 (Ctrl+←)", self._play_prev)
        pm.addAction("下一个 (Ctrl+→)", self._play_next)
        pm.addSeparator()
        pm.addAction("黑屏 (Ctrl+B)", self._toggle_black)

        sm = mb.addMenu("屏幕(&S)")
        sm.addAction("输出屏幕设置", self._show_screen_settings)
        sm.addAction("显示/隐藏输出窗口", self._toggle_output)

        hm = mb.addMenu("帮助(&H)")
        hm.addAction("关于", self._show_about)

    # ==================== 快捷键 ====================

    def keyPressEvent(self, event):
        k, m = event.key(), event.modifiers()
        if k == Qt.Key_Space:
            self._toggle_play()
        elif k == Qt.Key_B and m == Qt.ControlModifier:
            self._toggle_black()
        elif k == Qt.Key_Left and m == Qt.ControlModifier:
            self._play_prev()
        elif k == Qt.Key_Right and m == Qt.ControlModifier:
            self._play_next()
        else:
            super().keyPressEvent(event)

    # ==================== 播放列表管理 ====================

    def _new_playlist(self):
        name, ok = QInputDialog.getText(self, "新建列表", "列表名称：")
        if ok and name.strip() and name.strip() not in self._playlists:
            self._playlists[name.strip()] = []
            self._playlist_list.addItem(name.strip())
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
        self._current_playlist = self._playlist_list.item(row).text()
        self._refresh_media_list()

    def _refresh_media_list(self):
        self._media_list.clear()
        items = self._playlists.get(self._current_playlist, [])
        for i, item in enumerate(items):
            icon = {"video": "🎬", "image": "🖼", "audio": "🎵"}.get(item.media_type, "📄")
            text = f"{icon} {item.name}"
            li = QListWidgetItem(("▶ " if i == self._current_index else "") + text)
            if i == self._current_index:
                li.setForeground(QColor("#a6e3a1"))
            self._media_list.addItem(li)

    # ==================== 媒体操作 ====================

    def _add_media(self):
        exts = " ".join([f"*{e}" for e in sorted(VIDEO_EXT | IMAGE_EXT | AUDIO_EXT)])
        paths, _ = QFileDialog.getOpenFileNames(
            self, "添加媒体文件", "",
            f"媒体文件 ({exts});;视频 (*.mp4 *.avi *.mkv *.mov *.wmv *.flv *.ts);;图片 (*.jpg *.jpeg *.png *.bmp *.gif);;音频 (*.mp3 *.wav *.aac *.flac);;所有文件 (*.*)"
        )
        for p in paths:
            self._add_item(p)

    def _add_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "选择文件夹")
        if not folder:
            return
        all_ext = VIDEO_EXT | IMAGE_EXT | AUDIO_EXT
        count = 0
        for fname in sorted(os.listdir(folder)):
            if os.path.splitext(fname)[1].lower() in all_ext:
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
        menu.addAction("▶ 播放此素材", lambda: self._play_at(row))
        menu.addAction("👁 预览缩略图", lambda: self._preview_item(row))
        menu.addSeparator()
        menu.addAction("⬆ 上移", lambda: self._move_item(row, -1))
        menu.addAction("⬇ 下移", lambda: self._move_item(row, 1))
        menu.addSeparator()
        menu.addAction("⚙ 属性", lambda: self._show_properties(row))
        menu.addSeparator()
        menu.addAction("🗑 删除", lambda: self._remove_item(row))
        menu.exec_(self._media_list.mapToGlobal(pos))

    def _on_item_double_clicked(self, _):
        self._play_at(self._media_list.currentRow())

    def _move_item(self, row, d):
        items = self._playlists[self._current_playlist]
        nr = row + d
        if 0 <= nr < len(items):
            items[row], items[nr] = items[nr], items[row]
            self._refresh_media_list()
            self._media_list.setCurrentRow(nr)

    def _remove_item(self, row):
        items = self._playlists[self._current_playlist]
        if 0 <= row < len(items):
            items.pop(row)
            self._refresh_media_list()

    def _show_properties(self, row):
        items = self._playlists[self._current_playlist]
        if 0 <= row < len(items):
            PropertiesDialog(items[row], self).exec_()

    def _preview_item(self, row):
        items = self._playlists[self._current_playlist]
        if row < 0 or row >= len(items):
            return
        item = items[row]
        if item.media_type == 'image':
            pix = QPixmap(item.path)
            if not pix.isNull():
                scaled = pix.scaled(280, 160, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                self._preview_label.setPixmap(scaled)
        else:
            self._preview_label.setText(f"📄 {item.name}\n（视频预览在输出屏幕显示）")

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
        self._info_duration.setText("时长：读取中...")

        # 下一个提示
        ni = index + 1
        items_list = self._playlists[self._current_playlist]
        self._next_label.setText(items_list[ni].name if ni < len(items_list) else "（列表末尾）")

        self._refresh_media_list()
        self._media_list.setCurrentRow(index)

        # 停止当前播放
        self._image_timer.stop()
        self._progress_timer.stop()
        if self._vlc_player:
            self._vlc_player.stop()

        if item.media_type == 'image':
            self._play_image(item)
        elif item.media_type in ('video', 'audio'):
            self._play_vlc(item)

        self.statusBar().showMessage(f"正在播放：{item.name}")

    def _play_image(self, item):
        self._output.show_image(item.path)
        dur_ms = item.duration_ms if item.duration_ms > 0 else 5000
        self._info_duration.setText(f"时长：{dur_ms // 1000} 秒")
        self._total_label.setText(format_ms(dur_ms))
        self._time_label.setText("00:00:00")
        self._btn_play.setText("⏸")
        if not item.loop:
            self._image_timer.start(dur_ms)

    def _play_vlc(self, item):
        if self._vlc_player is None:
            self.statusBar().showMessage("错误：VLC 未初始化，无法播放")
            return
        self._output.show_video()
        # 重新绑定输出（防止窗口移动后句柄变化）
        self._bind_vlc_output()

        media = self._vlc_instance.media_new(item.path)
        self._vlc_player.set_media(media)
        media.release()
        self._vlc_player.audio_set_volume(item.volume)
        self._vlc_player.play()
        self._btn_play.setText("⏸")
        self._progress_timer.start()

        # 延迟获取时长
        QTimer.singleShot(1500, self._update_duration)

    def _update_duration(self):
        if self._vlc_player is None:
            return
        dur = self._vlc_player.get_length()
        if dur > 0:
            items = self._playlists[self._current_playlist]
            if 0 <= self._current_index < len(items):
                items[self._current_index].duration_ms = dur
            self._total_label.setText(format_ms(dur))
            self._info_duration.setText(f"时长：{format_ms(dur)}")

    def _refresh_progress(self):
        """每500ms刷新进度条"""
        if self._vlc_player is None:
            return
        pos = self._vlc_player.get_position()  # 0.0~1.0
        time_ms = self._vlc_player.get_time()
        if pos >= 0:
            self._progress.setValue(int(pos * 1000))
        if time_ms >= 0:
            self._time_label.setText(format_ms(time_ms))
        # 检查是否播放完毕
        state = self._vlc_player.get_state()
        if _vlc_mod and state == _vlc_mod.State.Ended:
            self._progress_timer.stop()
            self._play_next()

    def _on_vlc_end_reached(self, event):
        """VLC 播放结束回调（在子线程，用 QTimer 切回主线程）"""
        QTimer.singleShot(100, self._play_next)

    def _on_vlc_error(self, event):
        """VLC 播放错误回调"""
        QTimer.singleShot(0, lambda: self.statusBar().showMessage("播放失败：VLC 无法解码此文件"))

    def _toggle_play(self):
        items = self._playlists[self._current_playlist]
        if not items:
            return
        if self._current_index < 0:
            self._play_at(0)
            return
        item = items[self._current_index]
        if item.media_type == 'image':
            if self._image_timer.isActive():
                self._image_timer.stop()
                self._btn_play.setText("▶")
            else:
                self._image_timer.start(item.duration_ms if item.duration_ms > 0 else 5000)
                self._btn_play.setText("⏸")
        else:
            if self._vlc_player:
                state = self._vlc_player.get_state()
                if _vlc_mod and state == _vlc_mod.State.Playing:
                    self._vlc_player.pause()
                    self._btn_play.setText("▶")
                    self._progress_timer.stop()
                else:
                    self._vlc_player.play()
                    self._btn_play.setText("⏸")
                    self._progress_timer.start()

    def _stop(self):
        self._image_timer.stop()
        self._progress_timer.stop()
        if self._vlc_player:
            self._vlc_player.stop()
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
        ni = self._current_index + 1
        if self._loop_mode == 1:
            self._play_at(self._current_index)
        elif ni < len(items):
            self._play_at(ni)
        elif self._loop_mode == 2:
            self._play_at(0)
        else:
            self._stop()
            self.statusBar().showMessage("播放完毕")

    def _on_image_timer(self):
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

    def _on_seek(self, value):
        if self._vlc_player:
            self._vlc_player.set_position(value / 1000.0)

    def _on_volume_changed(self, value):
        self._vol_label.setText(f"{value}%")
        if self._vlc_player:
            self._vlc_player.audio_set_volume(value)

    # ==================== 屏幕管理 ====================

    def _move_output_to_screen2(self):
        screens = QApplication.screens()
        if len(screens) >= 2:
            geo = screens[1].geometry()
            self._output.setGeometry(geo)
            self._output.show()
            QApplication.processEvents()
            self._output.showFullScreen()
            QApplication.processEvents()
            msg = f"检测到 {len(screens)} 个屏幕，输出到屏幕2（{geo.width()}x{geo.height()}）"
        else:
            self._output.resize(960, 540)
            self._output.show()
            QApplication.processEvents()
            msg = "单屏模式：输出窗口已显示（建议连接第二个屏幕）"
        self.statusBar().showMessage(msg)

    def _show_screen_settings(self):
        screens = QApplication.screens()
        msg = f"检测到 {len(screens)} 个屏幕：\n\n"
        for i, s in enumerate(screens):
            g = s.geometry()
            msg += f"屏幕 {i+1}：{g.width()}x{g.height()} @ ({g.x()},{g.y()})\n"
        dlg = QMessageBox(self)
        dlg.setWindowTitle("屏幕信息")
        dlg.setText(msg)
        dlg.addButton("重新分配屏幕", QMessageBox.ActionRole)
        dlg.addButton("关闭", QMessageBox.RejectRole)
        if dlg.exec_() == 0:
            self._move_output_to_screen2()
            QTimer.singleShot(500, self._bind_vlc_output)

    def _toggle_output(self):
        if self._output.isVisible():
            self._output.hide()
        else:
            self._output.show()
            QTimer.singleShot(300, self._bind_vlc_output)

    # ==================== 节目单 ====================

    def _save_playlist(self):
        path, _ = QFileDialog.getSaveFileName(self, "保存节目单", "", "节目单 (*.json)")
        if not path:
            return
        data = {name: [{"path": it.path, "duration_ms": it.duration_ms,
                         "loop": it.loop, "volume": it.volume}
                        for it in items]
                for name, items in self._playlists.items()}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        self.statusBar().showMessage(f"节目单已保存：{path}")

    def _load_playlist(self):
        path, _ = QFileDialog.getOpenFileName(self, "加载节目单", "", "节目单 (*.json)")
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
                for d in items:
                    if os.path.exists(d["path"]):
                        it = MediaItem(d["path"])
                        it.duration_ms = d.get("duration_ms", 0)
                        it.loop = d.get("loop", False)
                        it.volume = d.get("volume", 100)
                        self._playlists[name].append(it)
            self._playlist_list.setCurrentRow(0)
            self._current_index = -1
            self.statusBar().showMessage(f"节目单已加载：{path}")
        except Exception as e:
            QMessageBox.critical(self, "加载失败", str(e))

    # ==================== 关于 ====================

    def _show_about(self):
        vlc_ver = "未安装"
        if _vlc_mod:
            try:
                vlc_ver = _vlc_mod.libvlc_get_version().decode()
            except Exception:
                vlc_ver = "已安装"
        QMessageBox.about(self, "关于双屏播放器",
            f"双屏播放器 v3.0\n\n"
            f"VLC 版本：{vlc_ver}\n\n"
            "功能：双屏输出 / 视频图片音频 / 播放列表\n"
            "节目单保存加载 / 黑屏控制 / 循环播放\n\n"
            "快捷键：Space=播放暂停  Ctrl+B=黑屏\n"
            "Ctrl+←=上一个  Ctrl+→=下一个")

    def closeEvent(self, event):
        self._progress_timer.stop()
        self._image_timer.stop()
        if self._vlc_player:
            self._vlc_player.stop()
        if self._vlc_instance:
            self._vlc_instance.release()
        self._output.close()
        event.accept()


# ============================================================
# 属性对话框
# ============================================================
class PropertiesDialog(QDialog):
    def __init__(self, item: MediaItem, parent=None):
        super().__init__(parent)
        self.item = item
        self.setWindowTitle(f"属性 - {item.name}")
        self.setModal(True)
        self.resize(380, 200)
        layout = QFormLayout(self)

        path_label = QLabel(item.path)
        path_label.setWordWrap(True)
        path_label.setStyleSheet("color:#89b4fa; font-size:11px;")
        layout.addRow("文件路径：", path_label)

        self._dur = QSpinBox()
        self._dur.setRange(1, 3600)
        self._dur.setValue(max(1, item.duration_ms // 1000))
        self._dur.setSuffix(" 秒")
        self._dur.setEnabled(item.media_type == 'image')
        layout.addRow("图片显示时长：", self._dur)

        self._vol = QSpinBox()
        self._vol.setRange(0, 100)
        self._vol.setValue(item.volume)
        self._vol.setSuffix(" %")
        layout.addRow("音量：", self._vol)

        self._loop = QCheckBox("循环播放此素材")
        self._loop.setChecked(item.loop)
        layout.addRow("", self._loop)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self._save)
        btns.rejected.connect(self.reject)
        layout.addRow(btns)

    def _save(self):
        self.item.duration_ms = self._dur.value() * 1000
        self.item.volume = self._vol.value()
        self.item.loop = self._loop.isChecked()
        self.accept()
