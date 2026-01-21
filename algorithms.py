import json
import os

class BoshenAlgorithms:
    """
    Implements the Boshen Kai Line calculation logic.
    Loads ratios from config.json if available.
    """
    
    _config_cache = None

    @staticmethod
    def get_config():
        if BoshenAlgorithms._config_cache:
            return BoshenAlgorithms._config_cache
            
        default_config = {
            "ratios": [1.784, 2.351, 3.027, 3.459, 3.865, 4.622, 5.135, 5.865, 6.676]
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
