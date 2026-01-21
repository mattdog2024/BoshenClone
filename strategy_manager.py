from enum import Enum
import json
import os
from datetime import datetime

class Timeframe(Enum):
    DAILY = "日线"
    H4 = "4小时"
    H1 = "1小时"
    M5 = "5分钟"

class StrategyManager:
    """
    Manages the trading session state, timeframe Selection, and strategy steps.
    """
    def __init__(self):
        self.session_data = {
            "created_at": str(datetime.now()),
            "current_timeframe": Timeframe.DAILY.value,
            "daily_direction": None, # 'UP', 'DOWN', 'SIDEWAYS'
            "h4_status": None,       # 'TRENDING', 'ADJUSTING'
            "h1_zone": None,         # Entry zone description
            "checklist": {
                Timeframe.DAILY.value: False,
                Timeframe.H4.value: False,
                Timeframe.H1.value: False,
                Timeframe.M5.value: False
            }
        }
        self.load_session()

    def set_timeframe(self, timeframe: Timeframe):
        self.session_data["current_timeframe"] = timeframe.value
        self.save_session()

    def get_current_timeframe(self) -> Timeframe:
        return Timeframe(self.session_data.get("current_timeframe", Timeframe.DAILY.value))

    def update_status(self, key, value):
        self.session_data[key] = value
        self.save_session()
        
    def get_status(self, key):
        return self.session_data.get(key)
    
    def set_step_complete(self, timeframe: Timeframe, is_complete: bool = True):
        self.session_data["checklist"][timeframe.value] = is_complete
        self.save_session()

    def get_step_complete(self, timeframe: Timeframe):
        return self.session_data["checklist"].get(timeframe.value, False)

    def save_session(self):
        try:
            with open("session_state.json", "w", encoding='utf-8') as f:
                json.dump(self.session_data, f, ensure_ascii=False, indent=4)
        except Exception as e:
            print(f"Error saving session: {e}")

    def load_session(self):
        if os.path.exists("session_state.json"):
            try:
                with open("session_state.json", "r", encoding='utf-8') as f:
                    data = json.load(f)
                    # Merge keys to avoid errors if schema changes
                    self.session_data.update(data) 
            except Exception as e:
                print(f"Error loading session: {e}")

    def get_guidance_text(self):
        """Returns the guidance text based on current state"""
        tf = self.get_current_timeframe()
        suggestion = self.session_data.get("last_analysis", "")
        
        base_text = ""
        if tf == Timeframe.DAILY:
            direction = self.session_data.get("daily_direction", "未定")
            base_text = f"【日线阶段】\n当前设定方向：{direction}\n\n1. 请画出波神线。\n2. 点击下面的“输入当前价格”进行分析。\n3. 系统将提示您当前价格处于第几线，以及是否关键阻力位。"
        elif tf == Timeframe.H4:
            direction = self.session_data.get("daily_direction", "未定")
            h4_status = self.session_data.get("h4_status", "未定")
            base_text = f"【4H 阶段】\n日线方向：{direction} | 4H状态：{h4_status}\n\n1. 观察是否出现回调/反弹信号。\n2. 只有当大周期（日线）与本周期（4H）共振时，胜率最高。"
        elif tf == Timeframe.H1:
            base_text = "【1H 阶段 - 选点】\n寻找入场区间。\n1. 关注顶分型/底分型。\n2. 等待右侧K线突破关键点。"
        elif tf == Timeframe.M5:
            base_text = "【5M 阶段 - 狙击】\n精准入场。\n1. 关注微观波神位（6线/8线）。\n2. 结合KDJ指标。"
        
        if suggestion:
            return f"{base_text}\n\n>>> 智能分析：\n{suggestion}"
        return base_text

    def analyze_situation(self, current_price, drawings):
        """
        Analyzes the current price against the provided drawings.
        Implements 'Boshen Advisor' logic.
        """
        from algorithms import BoshenAlgorithms
        
        if not drawings:
            return "❌无法分析：当前周期没有找到波神线。\n请使用‘单’工具画出波神线（A点到B点）。"

        # 1. Use the last drawing to determine current cycle
        d = drawings[-1]
        price_a = d.get('price_a', 0)
        price_b = d.get('price_b', 0)
        
        if price_a == 0 or price_b == 0:
            return "⚠️画线数据不完整。\n请重新画线或调整价格。"

        # 2. Infer Trend
        is_uptrend = price_b > price_a
        trend_str = "看涨 (UP)" if is_uptrend else "看跌 (DOWN)"
        
        # Save trend to session for cross-timeframe logic
        current_tf = self.get_current_timeframe()
        if current_tf == Timeframe.DAILY:
            self.session_data["daily_direction"] = "看涨" if is_uptrend else "看跌"
            self.save_session()
        
        # 3. Calculate Levels & Location
        levels = BoshenAlgorithms.calculate_levels(price_a, price_b)
        range_span = abs(price_b - price_a)
        
        # Boshen Line Names (Approximate mapping based on effective usage)
        # 1-2: Trend Start
        # 3: Key Reversal 1
        # 6: Key Reversal 2
        # 8: Final Reversal
        line_names = ["1线", "2线", "3线 (关键)", "4线", "5线", "6线 (关键)", "7线", "8线 (终极)"]
        
        nearest_line_idx = -1
        nearest_line_name = ""
        nearest_line_price = 0
        min_dist = float('inf')
        
        # Check A, B first
        dist_a = abs(current_price - price_a)
        if dist_a < min_dist:
            min_dist = dist_a
            nearest_line_name = "A点 (起点)"
            nearest_line_price = price_a
            
        dist_b = abs(current_price - price_b)
        if dist_b < min_dist:
            min_dist = dist_b
            nearest_line_name = "B点 (终点)"
            nearest_line_price = price_b

        for i, (ratio, price) in enumerate(levels):
            dist = abs(current_price - price)
            if dist < min_dist:
                min_dist = dist
                # i=0 is Line 1, i=2 is Line 3, etc.
                nearest_line_idx = i 
                nearest_line_name = line_names[i] if i < len(line_names) else f"{i+1}线"
                nearest_line_price = price

        # Tolerance: 3% of the A-B range (Tighter tolerance for 'Hit')
        tolerance = range_span * 0.05
        # is_hit = min_dist <= tolerance
        
        # 4. Generate Advice
        advice = ""
        
        # --- Logic: Reversal Zones ---
        # Line 3 (i=2), Line 6 (i=5), Line 8 (i=7)
        is_reversal_zone = False
        if nearest_line_idx in [2, 5, 7]: 
             if min_dist <= tolerance:
                 is_reversal_zone = True
                 advice += f"⚠️【警惕翻转】价格触及 {nearest_line_name}！\n这是波神法则的标准回调/反弹位。\n"

        # --- Logic: Callback Support (Retracement Targets) [Based on koujue.md] ---
        # Refined to be CONDITIONAL based on User's observation of the High.
        
        # Rule 1: Line 3 calls back to Line B
        if nearest_line_name.startswith("B点") or (nearest_line_idx == -1 and nearest_line_name == "B点 (终点)"):
             advice += "💡【回调口诀】若之前的最高点在3线附近：\n此位置 (B线) 是标准支撑位。\n等待形态成立确认回调结束。\n"
             
        # Rule 2: Line 5 calls back to Line 1
        elif nearest_line_idx == 0: # Line 1
             advice += "💡【回调支撑判断】请对照前高：\n1. 若前高曾触及5线 -> 此处(1线)为标准支撑，可关注形态。\n2. 若前高仅触及3线 -> 此时应回调至B线。停在1线说明回调力度偏弱或未到位。\n"
             
        # Rule 3: Line 6 calls back to Line 2
        elif nearest_line_idx == 1: # Line 2
             advice += "💡【回调支撑判断】请对照前高：\n1. 若前高曾触及6线 -> 此处(2线)为标准支撑。\n2. 若前高触及7/8线 -> 回调可能更深。\n"

        # --- Logic: Multi-Timeframe ---
        daily_dir = self.session_data.get("daily_direction", "未定")
        
        if current_tf == Timeframe.H4:
            # Check Resonance or Adjustment
            if daily_dir == "未定":
                advice += "💡提示：请先在日线图画线以确认大方向。\n"
            elif daily_dir == trend_str.split()[0]: # "看涨" == "看涨"
                advice += f"✅【趋势共振】日线与4H方向一致 ({daily_dir})。\n顺势而为，寻找突破机会。\n"
            else:
                # Opposite logic: Daily Up vs 4H Down = Adjustment
                advice += f"🔄【调整机会】日线({daily_dir}) vs 4H({trend_str})。\n这是大方向中的回调阶段。\n若价格在3/6/8线企稳，是极佳的【顺大势进场点】！\n"
        
        elif current_tf == Timeframe.DAILY:
             if is_reversal_zone:
                 advice += "建议：大周期面临阻力，请切换至4H观察是否出现反转形态。\n"
             else:
                 advice += "建议：持有顺势单，关注下一个关键波神线位置。\n"

        # 5. Formulate Result
        status_line = f"当前位置：{nearest_line_name} ({nearest_line_price:.2f})"
        if min_dist > tolerance:
             status_line = f"当前位置：位于 {nearest_line_name} 附近 (偏差 {min_dist:.1f})"
        
        final_msg = (
            f"🔍【波神智能分析报告】\n"
            f"------------------------------\n"
            f"📈 当前趋势：{trend_str}\n"
            f"📍 {status_line}\n"
            f"------------------------------\n"
            f"{advice}\n"
            f"------------------------------\n"
            f"💡 操作口诀：\n"
            f"日线看方向，4H看调整，\n"
            f"3线6线8线，关键位要警惕。"
        )

        self.session_data["last_analysis"] = final_msg
        self.save_session()
        return final_msg
