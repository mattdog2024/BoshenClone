"""
媒体素材数据模型
"""
import os
import json
from enum import Enum


class MediaType(Enum):
    VIDEO = "video"
    IMAGE = "image"
    AUDIO = "audio"
    UNKNOWN = "unknown"


# 支持的文件格式
VIDEO_EXTENSIONS = {'.mp4', '.avi', '.mov', '.mkv', '.wmv', '.flv', '.rmvb',
                    '.rm', '.ts', '.m4v', '.3gp', '.webm', '.mpg', '.mpeg',
                    '.m2ts', '.mts', '.vob', '.ogv', '.asf', '.divx', '.xvid'}
IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.bmp', '.gif', '.tiff', '.tif',
                    '.webp', '.ico', '.svg'}
AUDIO_EXTENSIONS = {'.mp3', '.wav', '.aac', '.flac', '.ogg', '.wma', '.m4a',
                    '.opus', '.ape', '.aiff'}


class MediaItem:
    """媒体素材类"""

    def __init__(self, file_path: str):
        self.file_path = file_path
        self.label = os.path.basename(file_path)  # 显示标签（可自定义）
        self.media_type = self._detect_type()
        self.duration = 0.0  # 时长（秒）
        self.width = 0
        self.height = 0
        self.volume = 100  # 音量 0-100
        self.loop = False  # 是否循环
        self.play_next = True  # 播放完后播放下一个
        self.shortcut_key = ""  # 快捷键
        self.is_kv = False  # 是否为主KV
        self.thumbnail_path = ""  # 缩略图路径
        self.transition_type = "fade"  # 转场类型: fade/cut/push/wipe
        self.transition_duration = 0.5  # 转场时长

    def _detect_type(self) -> MediaType:
        ext = os.path.splitext(self.file_path)[1].lower()
        if ext in VIDEO_EXTENSIONS:
            return MediaType.VIDEO
        elif ext in IMAGE_EXTENSIONS:
            return MediaType.IMAGE
        elif ext in AUDIO_EXTENSIONS:
            return MediaType.AUDIO
        return MediaType.UNKNOWN

    def to_dict(self) -> dict:
        return {
            'file_path': self.file_path,
            'label': self.label,
            'media_type': self.media_type.value,
            'duration': self.duration,
            'width': self.width,
            'height': self.height,
            'volume': self.volume,
            'loop': self.loop,
            'play_next': self.play_next,
            'shortcut_key': self.shortcut_key,
            'is_kv': self.is_kv,
            'transition_type': self.transition_type,
            'transition_duration': self.transition_duration,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'MediaItem':
        item = cls(data['file_path'])
        item.label = data.get('label', item.label)
        item.duration = data.get('duration', 0.0)
        item.width = data.get('width', 0)
        item.height = data.get('height', 0)
        item.volume = data.get('volume', 100)
        item.loop = data.get('loop', False)
        item.play_next = data.get('play_next', True)
        item.shortcut_key = data.get('shortcut_key', "")
        item.is_kv = data.get('is_kv', False)
        item.transition_type = data.get('transition_type', 'fade')
        item.transition_duration = data.get('transition_duration', 0.5)
        return item

    def format_duration(self) -> str:
        """格式化时长为 HH:MM:SS"""
        total = int(self.duration)
        h = total // 3600
        m = (total % 3600) // 60
        s = total % 60
        return f"{h:02d}:{m:02d}:{s:02d}"

    def format_resolution(self) -> str:
        if self.width and self.height:
            return f"{self.width}x{self.height}"
        return ""
