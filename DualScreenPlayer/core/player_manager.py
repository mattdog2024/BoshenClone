"""
播放器管理核心模块
封装 VLC 播放器，管理主输出和预览两个播放器实例
"""
import vlc
from PyQt5.QtCore import QObject, pyqtSignal, QTimer
from models.media_item import MediaItem, MediaType


class PlayerManager(QObject):
    """管理两路 VLC 播放器（主输出 + 预览）"""

    # 信号定义
    position_changed = pyqtSignal(float)       # 播放进度变化 (0.0~1.0)
    time_changed = pyqtSignal(int)             # 当前时间变化（毫秒）
    duration_changed = pyqtSignal(int)         # 总时长变化（毫秒）
    state_changed = pyqtSignal(str)            # 播放状态变化
    media_ended = pyqtSignal()                 # 媒体播放结束
    preview_ended = pyqtSignal()               # 预览播放结束

    def __init__(self, parent=None):
        super().__init__(parent)
        # 创建 VLC 实例
        self._instance = vlc.Instance([
            '--no-xlib',
            '--quiet',
            '--no-video-title-show',
        ])
        # 主输出播放器
        self._main_player = self._instance.media_player_new()
        # 预览播放器
        self._preview_player = self._instance.media_player_new()

        self._current_item: MediaItem = None
        self._preview_item: MediaItem = None
        self._is_playing = False
        self._volume = 100
        self._preview_volume = 50

        # 进度更新定时器
        self._timer = QTimer()
        self._timer.setInterval(200)
        self._timer.timeout.connect(self._update_progress)
        self._timer.start()

        # 注册 VLC 事件
        self._setup_events()

    def _setup_events(self):
        """注册 VLC 事件回调"""
        em = self._main_player.event_manager()
        em.event_attach(vlc.EventType.MediaPlayerEndReached,
                        lambda e: self.media_ended.emit())
        em.event_attach(vlc.EventType.MediaPlayerPlaying,
                        lambda e: self.state_changed.emit('playing'))
        em.event_attach(vlc.EventType.MediaPlayerPaused,
                        lambda e: self.state_changed.emit('paused'))
        em.event_attach(vlc.EventType.MediaPlayerStopped,
                        lambda e: self.state_changed.emit('stopped'))

        em2 = self._preview_player.event_manager()
        em2.event_attach(vlc.EventType.MediaPlayerEndReached,
                         lambda e: self.preview_ended.emit())

    def set_main_output(self, win_id: int):
        """设置主输出窗口句柄"""
        import sys
        if sys.platform == 'win32':
            self._main_player.set_hwnd(win_id)
        elif sys.platform == 'darwin':
            self._main_player.set_nsobject(win_id)
        else:
            self._main_player.set_xwindow(win_id)

    def set_preview_output(self, win_id: int):
        """设置预览窗口句柄"""
        import sys
        if sys.platform == 'win32':
            self._preview_player.set_hwnd(win_id)
        elif sys.platform == 'darwin':
            self._preview_player.set_nsobject(win_id)
        else:
            self._preview_player.set_xwindow(win_id)

    def play(self, item: MediaItem):
        """在主输出播放媒体"""
        self._current_item = item
        media = self._instance.media_new(item.file_path)
        self._main_player.set_media(media)
        self._main_player.audio_set_volume(item.volume)
        self._main_player.play()
        self._is_playing = True

    def play_preview(self, item: MediaItem):
        """在预览窗口播放媒体"""
        self._preview_item = item
        media = self._instance.media_new(item.file_path)
        self._preview_player.set_media(media)
        self._preview_player.audio_set_volume(self._preview_volume)
        self._preview_player.play()

    def pause(self):
        """暂停/恢复主输出"""
        if self._main_player.is_playing():
            self._main_player.pause()
            self._is_playing = False
        else:
            self._main_player.play()
            self._is_playing = True

    def stop(self):
        """停止主输出"""
        self._main_player.stop()
        self._is_playing = False

    def stop_preview(self):
        """停止预览"""
        self._preview_player.stop()

    def seek(self, position: float):
        """跳转到指定位置（0.0~1.0）"""
        if self._main_player.get_length() > 0:
            self._main_player.set_position(position)

    def seek_ms(self, ms: int):
        """跳转到指定毫秒位置"""
        self._main_player.set_time(ms)

    def set_volume(self, volume: int):
        """设置主输出音量（0-100）"""
        self._volume = volume
        self._main_player.audio_set_volume(volume)

    def set_preview_volume(self, volume: int):
        """设置预览音量（0-100）"""
        self._preview_volume = volume
        self._preview_player.audio_set_volume(volume)

    def get_position(self) -> float:
        return self._main_player.get_position()

    def get_time(self) -> int:
        """返回当前时间（毫秒）"""
        return self._main_player.get_time()

    def get_duration(self) -> int:
        """返回总时长（毫秒）"""
        return self._main_player.get_length()

    def is_playing(self) -> bool:
        return self._main_player.is_playing()

    def _update_progress(self):
        """定时更新进度信息"""
        if self._main_player.is_playing():
            pos = self._main_player.get_position()
            t = self._main_player.get_time()
            dur = self._main_player.get_length()
            if pos >= 0:
                self.position_changed.emit(pos)
            if t >= 0:
                self.time_changed.emit(t)
            if dur > 0:
                self.duration_changed.emit(dur)

    def set_fade_volume(self, volume: int):
        """用于淡入淡出效果：设置主输出音量"""
        self._main_player.audio_set_volume(max(0, min(100, volume)))

    def cleanup(self):
        """释放资源"""
        self._timer.stop()
        self._main_player.stop()
        self._preview_player.stop()
        self._main_player.release()
        self._preview_player.release()
        self._instance.release()
