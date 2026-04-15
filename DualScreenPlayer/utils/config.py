"""
全局配置管理
"""
import os
import json

CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".dualscreen_player")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")


DEFAULT_CONFIG = {
    "output_screen": 1,          # 输出屏幕索引（0=主屏，1=副屏）
    "output_width": 1920,
    "output_height": 1080,
    "master_volume": 100,
    "preview_volume": 50,
    "fade_duration": 0.5,        # 淡入淡出时长（秒）
    "blackout_duration": 0.5,    # 黑屏切换时长
    "default_transition": "fade",
    "auto_play_next": True,
    "loop_playlist": False,
    "last_playlist": "",
    "thumbnail_cache_dir": os.path.join(CONFIG_DIR, "thumbnails"),
    "double_click_play": True,
    "preload_next": True,
    "clear_ppt_cache_on_close": False,
}


class Config:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._data = {}
            cls._instance.load()
        return cls._instance

    def load(self):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        os.makedirs(DEFAULT_CONFIG["thumbnail_cache_dir"], exist_ok=True)
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                    self._data = json.load(f)
            except Exception:
                self._data = {}
        # 填充默认值
        for key, value in DEFAULT_CONFIG.items():
            if key not in self._data:
                self._data[key] = value

    def save(self):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)

    def get(self, key: str, default=None):
        return self._data.get(key, default)

    def set(self, key: str, value):
        self._data[key] = value
        self.save()

    def __getitem__(self, key):
        return self._data.get(key, DEFAULT_CONFIG.get(key))

    def __setitem__(self, key, value):
        self.set(key, value)
