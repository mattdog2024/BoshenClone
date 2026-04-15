"""
播放列表管理器
管理多个播放列表，处理播放逻辑
"""
import os
import json
from typing import List, Optional
from PyQt5.QtCore import QObject, pyqtSignal
from models.playlist import Playlist
from models.media_item import MediaItem


class PlaylistManager(QObject):
    """播放列表管理器"""

    playlist_changed = pyqtSignal()
    item_added = pyqtSignal(int, int)     # playlist_index, item_index
    item_removed = pyqtSignal(int, int)   # playlist_index, item_index

    def __init__(self, parent=None):
        super().__init__(parent)
        self._playlists: List[Playlist] = []
        self._current_playlist_index = 0
        self._current_item_index = -1
        self._kv_item: Optional[MediaItem] = None
        self._add_default_playlist()

    def _add_default_playlist(self):
        self._playlists.append(Playlist("列表1"))

    def add_playlist(self, name: str = None) -> int:
        if name is None:
            name = f"列表{len(self._playlists) + 1}"
        self._playlists.append(Playlist(name))
        self.playlist_changed.emit()
        return len(self._playlists) - 1

    def remove_playlist(self, index: int):
        if len(self._playlists) > 1 and 0 <= index < len(self._playlists):
            self._playlists.pop(index)
            if self._current_playlist_index >= len(self._playlists):
                self._current_playlist_index = len(self._playlists) - 1
            self.playlist_changed.emit()

    def get_playlist(self, index: int) -> Optional[Playlist]:
        if 0 <= index < len(self._playlists):
            return self._playlists[index]
        return None

    def get_current_playlist(self) -> Optional[Playlist]:
        return self.get_playlist(self._current_playlist_index)

    def set_current_playlist(self, index: int):
        if 0 <= index < len(self._playlists):
            self._current_playlist_index = index

    def get_playlist_count(self) -> int:
        return len(self._playlists)

    def add_media_to_playlist(self, playlist_index: int, item: MediaItem):
        pl = self.get_playlist(playlist_index)
        if pl:
            pl.add_item(item)
            self.item_added.emit(playlist_index, pl.count() - 1)

    def remove_media_from_playlist(self, playlist_index: int, item_index: int):
        pl = self.get_playlist(playlist_index)
        if pl:
            pl.remove_item(item_index)
            self.item_removed.emit(playlist_index, item_index)

    def get_current_item(self) -> Optional[MediaItem]:
        pl = self.get_current_playlist()
        if pl:
            return pl.get_item(self._current_item_index)
        return None

    def get_next_item(self) -> Optional[MediaItem]:
        pl = self.get_current_playlist()
        if pl:
            next_idx = self._current_item_index + 1
            return pl.get_item(next_idx)
        return None

    def set_current_item(self, playlist_index: int, item_index: int):
        self._current_playlist_index = playlist_index
        self._current_item_index = item_index
        pl = self.get_current_playlist()
        if pl:
            pl.current_index = item_index

    def advance_to_next(self) -> Optional[MediaItem]:
        """播放下一个素材"""
        pl = self.get_current_playlist()
        if not pl:
            return None
        next_idx = self._current_item_index + 1
        if next_idx < pl.count():
            self._current_item_index = next_idx
            pl.current_index = next_idx
            return pl.get_item(next_idx)
        return None

    def set_kv(self, item: MediaItem):
        """设置主 KV"""
        self._kv_item = item
        item.is_kv = True

    def get_kv(self) -> Optional[MediaItem]:
        return self._kv_item

    def save_to_file(self, file_path: str):
        """保存节目单到文件"""
        data = {
            'playlists': [pl.to_dict() for pl in self._playlists],
            'current_playlist': self._current_playlist_index,
        }
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def load_from_file(self, file_path: str):
        """从文件加载节目单"""
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        self._playlists = []
        for pl_data in data.get('playlists', []):
            self._playlists.append(Playlist.from_dict(pl_data))
        if not self._playlists:
            self._add_default_playlist()
        self._current_playlist_index = data.get('current_playlist', 0)
        self._current_item_index = -1
        self.playlist_changed.emit()
