"""
播放列表数据模型
"""
import json
from typing import List, Optional
from models.media_item import MediaItem


class Playlist:
    """播放列表类"""

    def __init__(self, name: str = "列表1"):
        self.name = name
        self.items: List[MediaItem] = []
        self.current_index = -1

    def add_item(self, item: MediaItem):
        self.items.append(item)

    def remove_item(self, index: int):
        if 0 <= index < len(self.items):
            self.items.pop(index)

    def move_item(self, from_index: int, to_index: int):
        if 0 <= from_index < len(self.items) and 0 <= to_index < len(self.items):
            item = self.items.pop(from_index)
            self.items.insert(to_index, item)

    def get_item(self, index: int) -> Optional[MediaItem]:
        if 0 <= index < len(self.items):
            return self.items[index]
        return None

    def get_next_item(self) -> Optional[MediaItem]:
        if not self.items:
            return None
        next_index = self.current_index + 1
        if next_index < len(self.items):
            return self.items[next_index]
        return None

    def count(self) -> int:
        return len(self.items)

    def to_dict(self) -> dict:
        return {
            'name': self.name,
            'items': [item.to_dict() for item in self.items],
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'Playlist':
        playlist = cls(data.get('name', '列表1'))
        for item_data in data.get('items', []):
            playlist.add_item(MediaItem.from_dict(item_data))
        return playlist
