import json
import os

class BoshenAlgorithms:
    """
    Implements the Boshen Kai Line calculation logic.
    Loads ratios from config.json if available.
    """
    
    _config_cache = None

    LINE_NAMES = [
        "开门线", "一线", "二线", "三线", "四线",
        "五线", "六线", "七线", "八线", "关门线"
    ]

    DEFAULT_RATIOS = [1.508, 2.0, 2.4, 3.05, 3.75, 4.15, 4.8, 5.5, 6.1, 6.9]

    @staticmethod
    def get_config():
        if BoshenAlgorithms._config_cache:
            return BoshenAlgorithms._config_cache
            
        default_config = {
            "ratios": BoshenAlgorithms.DEFAULT_RATIOS
        }
        
        try:
            if os.path.exists("config.json"):
                with open("config.json", "r") as f:
                    return json.load(f)
        except Exception as e:
            print(f"Error loading config: {e}")
            
        return default_config

    @staticmethod
    def calculate_levels(start_price, end_price):
        """
        Calculates the Boshen levels based on a start (A) and end (B) price.
        Formula: Level = Start + (End - Start) * Ratio
        """
        config = BoshenAlgorithms.get_config()
        ratios = config.get("ratios", [])
        
        diff = end_price - start_price
        levels = []
        for r in ratios:
            price = start_price + diff * r
            levels.append((r, price))
        return levels
