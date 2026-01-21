import json
import os

class PresetManager:
    def __init__(self, filename="presets.json"):
        self.filename = filename
        self.presets = self.load_presets()

    def load_presets(self):
        if not os.path.exists(self.filename):
            return {}
        try:
            with open(self.filename, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading presets: {e}")
            return {}

    def save_preset(self, name, price_a, price_b, calibration=None):
        data = {"a": price_a, "b": price_b}
        if calibration:
            data['calibration'] = calibration
        self.presets[name] = data
        self.save_to_file()

    def delete_preset(self, name):
        if name in self.presets:
            del self.presets[name]
            self.save_to_file()

    def save_to_file(self):
        try:
            with open(self.filename, 'w', encoding='utf-8') as f:
                json.dump(self.presets, f, ensure_ascii=False, indent=4)
        except Exception as e:
            print(f"Error saving presets: {e}")

    def get_presets(self):
        return self.presets
