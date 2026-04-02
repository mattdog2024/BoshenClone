"""
波神凯线策略 V3 - 多周期自动交易版
基于 pythongo BaseStrategy (Python 3.11+)

交易逻辑：
  日线(D1)  → 确认大方向（向上/向下）+ 计算1~8线线位
  60分钟(H1) → 找回调/反弹的入场区域（跌到/涨到关键线位）
  15分钟(M15)→ 确认60分钟回调结束（出现反转K线形态）
  5分钟(M5)  → 精准入场（KDJ金叉/死叉 + 价格在支撑/压力位）

仓位管理：
  - 单笔风险 2% 账户资金
  - 止损：跌破入场线位下一条线
  - 分批止盈：60分钟3线平50%，5-6线平30%，8线平20%
  - 动态止损：每突破一条线，止损上移到前一条线
"""

import numpy as np
from pydantic import Field

from pythongo.base import BaseParams, BaseState
from pythongo.classdef import KLineData, OrderData, TickData, TradeData
from pythongo.ui import BaseStrategy
from pythongo.utils import KLineGenerator


# ============================================================
# 参数 / 状态模型
# ============================================================

class Params(BaseParams):
    exchange: str = Field(default="SHFE", title="交易所代码")
    instrument_id: str = Field(default="rb2605", title="合约代码")
    lookback_daily: int = Field(default=40, title="日线回看周期(根)", ge=5)
    risk_pct: float = Field(default=2.0, title="单笔风险百分比(%)", ge=0.5, le=10.0)
    pay_up: float = Field(default=1.0, title="超价(点数)", ge=0.0)
    auto_trade: bool = Field(default=False, title="是否启用自动交易(True=开启)")
    min_volume: int = Field(default=1, title="最小下单手数", ge=1)


class State(BaseState):
    daily_direction: int = Field(default=0, title="日线方向(1=上 -1=下 0=未知)")
    daily_line_desc: str = Field(default="未知", title="日线线位描述")
    signal_stage: str = Field(default="等待日线确认", title="当前信号阶段")
    h1_zone: str = Field(default="", title="60分钟当前线位")
    m15_pattern: str = Field(default="", title="15分钟形态")
    m5_kdj_k: float = Field(default=0, title="5分钟KDJ-K")
    m5_kdj_j: float = Field(default=0, title="5分钟KDJ-J")
    line1: float = Field(default=0, title="1线")
    line3: float = Field(default=0, title="3线")
    line5: float = Field(default=0, title="5线")
    line8: float = Field(default=0, title="8线")
    entry_price: float = Field(default=0, title="入场价格")
    stop_loss: float = Field(default=0, title="止损价格")
    position_vol: int = Field(default=0, title="当前持仓手数")


# ============================================================
# 策略主类
# ============================================================

class BoshenStrategy(BaseStrategy):
    """
    波神凯线策略 V3（多周期自动交易版）
    日线→60分钟→15分钟→5分钟 四级过滤信号系统
    """

    # 波神八条线比例
    BOSHEN_RATIOS = [1.382, 1.618, 2.0, 2.382, 2.618, 3.0, 3.382, 3.618]
    RATIO_9 = 4.236  # 极线

    # 信号阶段常量
    STAGE_WAIT_DAILY = "等待日线确认"
    STAGE_WAIT_H1 = "等待60分钟入场区"
    STAGE_WAIT_M15 = "等待15分钟形态确认"
    STAGE_WAIT_M5 = "等待5分钟精准入场"
    STAGE_IN_POSITION = "已持仓"

    def __init__(self) -> None:
        super().__init__()

        self.params_map = Params()
        self.state_map = State()

        # K线生成器（四个周期）
        self.kg_daily: KLineGenerator = None   # 日线
        self.kg_h1: KLineGenerator = None      # 60分钟
        self.kg_m15: KLineGenerator = None     # 15分钟
        self.kg_m5: KLineGenerator = None      # 5分钟

        # 波神测量点（日线向上）
        self.mp_daily = self._new_mp()
        # 波神测量点（日线向下）
        self.mp_daily_down = self._new_mp_down()
        # 60分钟测量点
        self.mp_h1 = self._new_mp()

        # 日线状态
        self.daily_direction = 0          # 1=向上 -1=向下
        self.daily_line_desc = '未知'
        self.highest_since_ab = 0.0
        self.lowest_since_ab = 999999.0
        self.lowest_since_high = 999999.0
        self.highest_since_low = 0.0

        # 多周期信号状态机
        self.signal_stage = self.STAGE_WAIT_DAILY
        self.h1_entry_zone = ''           # 60分钟入场区域（如"2-3线区间"）
        self.h1_entry_direction = 0       # 60分钟期望入场方向（1=做多 -1=做空）
        self.m15_pattern_ok = False       # 15分钟形态是否确认
        self.m15_pattern_name = ''        # 形态名称

        # 5分钟KDJ状态
        self.m5_k_prev = 50.0
        self.m5_d_prev = 50.0
        self.m5_j_prev = 50.0
        self.m5_k = 50.0
        self.m5_d = 50.0
        self.m5_j = 50.0

        # 持仓管理
        self.order_id: int | None = None
        self.entry_price = 0.0
        self.stop_loss_price = 0.0
        self.take_profit_1 = 0.0         # 第一批止盈（50%）
        self.take_profit_2 = 0.0         # 第二批止盈（30%）
        self.take_profit_3 = 0.0         # 第三批止盈（20%）
        self.tp1_done = False
        self.tp2_done = False
        self.total_volume = 0             # 开仓总手数

        # 当前tick
        self._last_tick: TickData = None
        self.signal_price = 0.0

        # 历史回放标志
        self._is_history = True
        self._last_summary_text = ''

        # 水平横线容器（monkey-patch方式添加，主线程安全）
        self._hlines: dict = {}

    # ============================================================
    # 图表指标属性
    # ============================================================

    @property
    def main_indicator_data(self) -> dict[str, float]:
        """主图指标：日线1~8线 + 极线"""
        def safe_level(lst, idx):
            return lst[idx] if lst and len(lst) > idx else 0.0

        levels = self.mp_daily.get('levels', [])
        level9 = self.mp_daily.get('level9') or 0.0
        d_levels = self.mp_daily_down.get('levels', [])

        return {
            '1线': safe_level(levels, 0),
            '2线': safe_level(levels, 1),
            '3线': safe_level(levels, 2),
            '4线': safe_level(levels, 3),
            '5线': safe_level(levels, 4),
            '6线': safe_level(levels, 5),
            '7线': safe_level(levels, 6),
            '8线': safe_level(levels, 7),
            '极线': level9,
            '空1线': safe_level(d_levels, 0),
            '空3线': safe_level(d_levels, 2),
            '空5线': safe_level(d_levels, 4),
        }

    # ============================================================
    # 数据结构工厂
    # ============================================================

    def _new_mp(self):
        return {
            'start': None, 'end': None, 'direction': 0,
            'use_shadow': False, 'k_high': None, 'k_low': None,
            'levels': [], 'level9': None,
        }

    def _new_mp_down(self):
        return {
            'start': None, 'end': None, 'direction': -1,
            'use_shadow': False, 'shadow_end': None, 'body_end': None,
            'k_high': None, 'k_low': None, 'k_open': None, 'k_close': None,
            'shadow_levels': [], 'shadow_level9': None,
            'levels': [], 'level9': None,
            'phase': 'shadow', 'bar_idx': -1,
        }

    # ============================================================
    # 波神核心算法
    # ============================================================

    def calculate_levels(self, start, end, direction):
        """计算波神八条线价格"""
        diff = end - start
        levels = [start + diff * r for r in self.BOSHEN_RATIOS]
        level9 = start + diff * self.RATIO_9
        return levels, level9

    def _find_ab_from_arrays(self, highs, lows, lookback, trend):
        """
        在最近 lookback 根K线里找A/B点：
        - 向上(trend=1)：A=最低点，B=A点之后到现在的最高点
        - 向下(trend=-1)：A=最高点，B=A点之后到现在的最低点
        """
        n = len(highs)
        if n < 3:
            return None, None, None, None

        window_lows = lows[-lookback:]
        window_highs = highs[-lookback:]
        offset = n - lookback if n >= lookback else 0

        if trend == 1:
            # 向上：找最低点作为A
            a_idx_in_window = int(np.argmin(window_lows))
            a_idx = a_idx_in_window + offset
            a_price = float(lows[a_idx])
            # B = A点之后的最高点
            if a_idx < n - 1:
                b_price = float(np.max(highs[a_idx:]))
            else:
                b_price = float(highs[a_idx])
            k_high = float(highs[a_idx])
            k_low = float(lows[a_idx])
        else:
            # 向下：找最高点作为A
            a_idx_in_window = int(np.argmax(window_highs))
            a_idx = a_idx_in_window + offset
            a_price = float(highs[a_idx])
            # B = A点之后的最低点
            if a_idx < n - 1:
                b_price = float(np.min(lows[a_idx:]))
            else:
                b_price = float(lows[a_idx])
            k_high = float(highs[a_idx])
            k_low = float(lows[a_idx])

        if abs(b_price - a_price) < 1.0:
            return None, None, None, None

        return a_price, b_price, k_high, k_low

    def get_line_zone(self, mp, price):
        """判断价格在哪条线区间"""
        levels = mp.get('levels', [])
        level9 = mp.get('level9')
        start = mp.get('start', 0.0) or 0.0
        direction = mp.get('direction', 1)

        if not levels or not start:
            return '未知区域', 0, False, False

        tol = price * 0.008  # 0.8% 容差

        # 检查是否在线位附近
        all_levels = list(enumerate(levels, 1))
        if level9:
            all_levels.append((9, level9))

        for line_num, lv in all_levels:
            if abs(price - lv) <= tol:
                is_key = line_num in [3, 5, 6, 8]
                is_extreme = line_num in [7, 8, 9]
                return f'{line_num}线区域', line_num, is_key, is_extreme

        # 判断在哪个区间
        if direction == 1:
            if price < start:
                return 'A点以下', 0, False, False
            if price < levels[0]:
                return 'A~1线区间', 0, False, False
            for i in range(len(levels) - 1):
                if levels[i] <= price < levels[i + 1]:
                    return f'{i+1}~{i+2}线区间', i + 1, False, False
            if level9 and price < level9:
                return f'8~极线区间', 8, True, True
            return '极线以上', 9, False, True
        else:
            if price > start:
                return 'A点以上', 0, False, False
            if price > levels[0]:
                return 'A~1线区间', 0, False, False
            for i in range(len(levels) - 1):
                if levels[i + 1] < price <= levels[i]:
                    return f'{i+1}~{i+2}线区间', i + 1, False, False
            if level9 and price > level9:
                return f'8~极线区间', 8, True, True
            return '极线以下', 9, False, True

    def _check_reversal_pattern(self, highs, lows, opens, closes, direction):
        """
        检查最近3根K线是否出现反转形态：
        direction=1  → 检查向上反转（锤子线、阳包阴）
        direction=-1 → 检查向下反转（射击之星、阴包阳）
        """
        if len(closes) < 3:
            return False, ''

        c0, c1, c2 = float(closes[-3]), float(closes[-2]), float(closes[-1])
        o0, o1, o2 = float(opens[-3]), float(opens[-2]), float(opens[-1])
        h1, h2 = float(highs[-2]), float(highs[-1])
        l1, l2 = float(lows[-2]), float(lows[-1])

        if direction == 1:
            # 阳包阴：最新K线为阳线，且实体包含前一根阴线实体
            engulfing = (c2 > o2 and c1 < o1 and c2 > o1 and o2 < c1)
            if engulfing:
                return True, '阳包阴'
            # 锤子线：下影线 >= 实体2倍，上影线很短
            body2 = abs(c2 - o2)
            lower_shadow2 = min(o2, c2) - l2
            upper_shadow2 = h2 - max(o2, c2)
            hammer = (body2 > 0 and lower_shadow2 >= body2 * 2 and upper_shadow2 <= body2 * 0.5)
            if hammer:
                return True, '锤子线'
            # 连续两根阳线
            two_bull = (c1 > o1 and c2 > o2 and c2 > c1)
            if two_bull:
                return True, '连续阳线'
        else:
            # 阴包阳：最新K线为阴线，且实体包含前一根阳线实体
            engulfing = (c2 < o2 and c1 > o1 and c2 < o1 and o2 > c1)
            if engulfing:
                return True, '阴包阳'
            # 射击之星：上影线 >= 实体2倍，下影线很短
            body2 = abs(c2 - o2)
            upper_shadow2 = h2 - max(o2, c2)
            lower_shadow2 = min(o2, c2) - l2
            shooting_star = (body2 > 0 and upper_shadow2 >= body2 * 2 and lower_shadow2 <= body2 * 0.5)
            if shooting_star:
                return True, '射击之星'
            # 连续两根阴线
            two_bear = (c1 < o1 and c2 < o2 and c2 < c1)
            if two_bear:
                return True, '连续阴线'

        return False, ''

    # ============================================================
    # 仓位计算
    # ============================================================

    def _calc_volume(self, entry_price, stop_price):
        """
        按账户资金风险百分比计算下单手数
        螺纹钢：1手 = 10吨，最小变动0.5元/吨
        """
        try:
            investor = self.get_investor_data().investor_id
            account = self.get_account_fund_data(investor)
            total_equity = account.balance  # 账户权益
        except Exception:
            total_equity = 100000.0  # 默认10万（测试用）

        risk_amount = total_equity * (self.params_map.risk_pct / 100.0)
        risk_per_lot = abs(entry_price - stop_price) * 10  # 螺纹钢每手10吨

        if risk_per_lot <= 0:
            return self.params_map.min_volume

        volume = int(risk_amount / risk_per_lot)
        volume = max(volume, self.params_map.min_volume)

        self.output(
            f'[仓位计算] 账户权益={total_equity:.0f} 风险金额={risk_amount:.0f} '
            f'每手风险={risk_per_lot:.0f} 计算手数={volume}'
        )
        return volume

    def _calc_stop_loss(self, entry_price, direction, levels):
        """
        计算止损价：跌破入场线位的下一条线
        direction=1(做多)：止损在入场线位下方一条线
        direction=-1(做空)：止损在入场线位上方一条线
        """
        if not levels:
            return entry_price * (0.98 if direction == 1 else 1.02)

        if direction == 1:
            # 找入场价格下方最近的线位作为止损
            below_levels = [lv for lv in levels if lv < entry_price]
            if below_levels:
                return max(below_levels) - 3  # 多3点缓冲
            return entry_price * 0.98
        else:
            # 找入场价格上方最近的线位作为止损
            above_levels = [lv for lv in levels if lv > entry_price]
            if above_levels:
                return min(above_levels) + 3  # 多3点缓冲
            return entry_price * 1.02

    def _calc_take_profits(self, entry_price, direction, levels):
        """
        计算分批止盈价位：
        tp1 = 第一个关键线位（3线附近）→ 平50%
        tp2 = 第二个关键线位（5-6线）→ 平30%
        tp3 = 第三个关键线位（8线）→ 平20%
        """
        if not levels or len(levels) < 8:
            if direction == 1:
                return entry_price * 1.02, entry_price * 1.04, entry_price * 1.06
            else:
                return entry_price * 0.98, entry_price * 0.96, entry_price * 0.94

        if direction == 1:
            targets = [lv for lv in [levels[2], levels[4], levels[7]] if lv > entry_price]
        else:
            targets = [lv for lv in [levels[2], levels[4], levels[7]] if lv < entry_price]

        while len(targets) < 3:
            if direction == 1:
                targets.append(targets[-1] * 1.01 if targets else entry_price * 1.02)
            else:
                targets.append(targets[-1] * 0.99 if targets else entry_price * 0.98)

        return targets[0], targets[1], targets[2]

    # ============================================================
    # 日线回调
    # ============================================================

    def _on_daily_bar(self, kline: KLineData) -> None:
        """日线K线确认后回调"""
        producer = self.kg_daily.producer
        n = len(producer.close)
        if n < 10:
            return

        highs = np.array(producer.high, dtype=float)
        lows = np.array(producer.low, dtype=float)
        opens = np.array(producer.open, dtype=float)
        closes = np.array(producer.close, dtype=float)
        current_price = float(closes[-1])

        # 确定趋势方向
        lookback = min(n, self.params_map.lookback_daily)
        trend = self.daily_direction if self.daily_direction != 0 else 1

        # 检查是否超过八线（需要重新测量）
        if self.mp_daily.get('levels') and len(self.mp_daily['levels']) >= 8:
            eight_line = self.mp_daily['levels'][7]
            if (trend == 1 and current_price > eight_line) or \
               (trend == -1 and current_price < eight_line):
                # 超八线，重新判断方向
                l_tmp = lows[-lookback:]
                h_tmp = highs[-lookback:]
                low_idx = int(np.argmin(l_tmp))
                a_up = float(l_tmp[low_idx])
                b_up = float(h_tmp[low_idx])
                if b_up > a_up:
                    test_levels, _ = self.calculate_levels(a_up, b_up, 1)
                    three_line = test_levels[2] if len(test_levels) > 2 else b_up
                    trend = -1 if current_price > three_line else 1
                self.output(f'【日线】超过八线({eight_line:.2f})，重新判断方向: {"向下" if trend == -1 else "向上"}')

        # 更新A/B点
        a_price, b_price, k_high, k_low = self._find_ab_from_arrays(highs, lows, lookback, trend)
        if a_price is not None:
            if (self.mp_daily.get('start') != a_price or
                    self.mp_daily.get('end') != b_price or
                    self.mp_daily.get('direction') != trend):
                self.mp_daily['start'] = a_price
                self.mp_daily['end'] = b_price
                self.mp_daily['direction'] = trend
                self.mp_daily['k_high'] = k_high
                self.mp_daily['k_low'] = k_low
                self.mp_daily['levels'], self.mp_daily['level9'] = \
                    self.calculate_levels(a_price, b_price, trend)
                dir_str = '向上' if trend == 1 else '向下'
                self.output(
                    f'【日线】基准更新: A={a_price:.2f}, B={b_price:.2f}, 方向={dir_str}\n'
                    f'  1线={self.mp_daily["levels"][0]:.2f} | '
                    f'3线={self.mp_daily["levels"][2]:.2f} | '
                    f'5线={self.mp_daily["levels"][4]:.2f} | '
                    f'8线={self.mp_daily["levels"][7]:.2f}'
                )

        if not self.mp_daily.get('levels'):
            return

        # 更新极值追踪
        if current_price > self.highest_since_ab:
            self.highest_since_ab = current_price
        if current_price < self.lowest_since_ab:
            self.lowest_since_ab = current_price

        # 判断线位区域
        zone_desc, zone_num, is_key, is_extreme = self.get_line_zone(self.mp_daily, current_price)
        self.daily_line_desc = zone_desc
        self.daily_direction = trend

        # 更新State
        levels = self.mp_daily['levels']
        self.state_map.daily_direction = trend
        self.state_map.daily_line_desc = zone_desc
        self.state_map.line1 = levels[0]
        self.state_map.line3 = levels[2]
        self.state_map.line5 = levels[4]
        self.state_map.line8 = levels[7]

        # 检查反转形态（在关键线位）
        if is_key or is_extreme:
            pattern_ok, pattern_name = self._check_reversal_pattern(
                highs, lows, opens, closes, -trend
            )
            if pattern_ok:
                # 关键线位出现反转形态 → 方向反转
                self.daily_direction = -trend
                action = '上涨' if self.daily_direction == 1 else '下跌'
                self.signal_stage = f'日线{action}确认，等待60分钟机会'
                if not self._is_history:
                    self.output(f'【日线信号】{zone_desc}出现{pattern_name}，方向反转为{action}')
            else:
                action = '上涨' if trend == 1 else '下跌'
                self.signal_stage = f'日线{action}，等待60分钟机会'
        else:
            action = '上涨' if trend == 1 else '下跌'
            self.signal_stage = f'日线{action}，等待60分钟机会'

        self.state_map.signal_stage = self.signal_stage

        if not self._is_history and self.trading:
            self.update_status_bar()

    def _on_daily_realtime(self, kline: KLineData) -> None:
        """日线实时推送"""
        self._push_kline_to_widget(kline)

    # ============================================================
    # 60分钟回调
    # ============================================================

    def _on_h1_bar(self, kline: KLineData) -> None:
        """60分钟K线确认后回调"""
        if self._is_history:
            return
        if not self.mp_daily.get('levels'):
            return

        producer = self.kg_h1.producer
        n = len(producer.close)
        if n < 10:
            return

        current_price = float(producer.close[-1])
        daily_dir = self.daily_direction
        levels = self.mp_daily['levels']

        # 判断当前60分钟价格在日线线位的哪个区域
        zone_desc, zone_num, is_key, is_extreme = self.get_line_zone(self.mp_daily, current_price)
        self.state_map.h1_zone = zone_desc

        # 判断是否进入入场区域
        # 向上趋势：等待60分钟回调到2-3线区域（做多机会）
        # 向下趋势：等待60分钟反弹到2-3线区域（做空机会）
        entry_zone_hit = False
        entry_direction = 0

        if daily_dir == 1:
            # 日线向上，等待60分钟回调到1-3线区域
            if zone_num in [1, 2, 3] or (zone_num == 0 and current_price > levels[0] * 0.99):
                entry_zone_hit = True
                entry_direction = 1
        elif daily_dir == -1:
            # 日线向下，等待60分钟反弹到1-3线区域
            if zone_num in [1, 2, 3] or (zone_num == 0 and current_price < levels[0] * 1.01):
                entry_zone_hit = True
                entry_direction = -1

        if entry_zone_hit and self.signal_stage != self.STAGE_IN_POSITION:
            self.h1_entry_zone = zone_desc
            self.h1_entry_direction = entry_direction
            self.m15_pattern_ok = False  # 重置15分钟形态确认
            self.signal_stage = self.STAGE_WAIT_M15
            self.state_map.signal_stage = self.signal_stage
            dir_str = '做多' if entry_direction == 1 else '做空'
            self.output(
                f'【60分钟】进入入场区域 {zone_desc}，期望方向={dir_str}，'
                f'等待15分钟形态确认'
            )

    def _on_h1_realtime(self, kline: KLineData) -> None:
        """60分钟实时推送"""
        pass  # 60分钟实时不做额外处理

    # ============================================================
    # 15分钟回调
    # ============================================================

    def _on_m15_bar(self, kline: KLineData) -> None:
        """15分钟K线确认后回调"""
        if self._is_history:
            return
        if self.signal_stage not in [self.STAGE_WAIT_M15, self.STAGE_WAIT_M5]:
            return

        producer = self.kg_m15.producer
        n = len(producer.close)
        if n < 5:
            return

        highs = np.array(producer.high, dtype=float)
        lows = np.array(producer.low, dtype=float)
        opens = np.array(producer.open, dtype=float)
        closes = np.array(producer.close, dtype=float)

        # 检查15分钟是否出现反转形态（确认60分钟回调结束）
        pattern_ok, pattern_name = self._check_reversal_pattern(
            highs, lows, opens, closes, self.h1_entry_direction
        )

        if pattern_ok:
            self.m15_pattern_ok = True
            self.m15_pattern_name = pattern_name
            self.signal_stage = self.STAGE_WAIT_M5
            self.state_map.signal_stage = self.signal_stage
            self.state_map.m15_pattern = pattern_name
            dir_str = '做多' if self.h1_entry_direction == 1 else '做空'
            self.output(
                f'【15分钟】出现{pattern_name}，回调结束确认！'
                f'方向={dir_str}，等待5分钟精准入场'
            )
        else:
            self.state_map.m15_pattern = '等待形态'

    def _on_m15_realtime(self, kline: KLineData) -> None:
        """15分钟实时推送"""
        pass

    # ============================================================
    # 5分钟回调（精准入场）
    # ============================================================

    def _on_m5_bar(self, kline: KLineData) -> None:
        """5分钟K线确认后回调 - 精准入场逻辑"""
        if self._is_history:
            return

        producer = self.kg_m5.producer
        n = len(producer.close)
        if n < 15:
            return

        # 计算5分钟KDJ
        try:
            k_arr, d_arr, j_arr = producer.kdj(
                fastk_period=9, slowk_period=3, slowd_period=3, array=True
            )
            self.m5_k_prev = float(k_arr[-2])
            self.m5_d_prev = float(d_arr[-2])
            self.m5_j_prev = float(j_arr[-2])
            self.m5_k = float(k_arr[-1])
            self.m5_d = float(d_arr[-1])
            self.m5_j = float(j_arr[-1])
            self.state_map.m5_kdj_k = round(self.m5_k, 2)
            self.state_map.m5_kdj_j = round(self.m5_j, 2)
        except Exception:
            return

        # 止损管理（已持仓时）
        if self.signal_stage == self.STAGE_IN_POSITION:
            self._manage_position(kline)
            return

        # 入场信号检测
        if self.signal_stage != self.STAGE_WAIT_M5:
            return
        if not self.m15_pattern_ok:
            return

        current_price = float(producer.close[-1])
        entry_dir = self.h1_entry_direction

        # KDJ信号：
        # 做多：KDJ在超卖区（J<20）出现金叉（K上穿D）
        # 做空：KDJ在超买区（J>80）出现死叉（K下穿D）
        kdj_long_signal = (
            self.m5_j_prev < 30 and
            self.m5_k_prev <= self.m5_d_prev and
            self.m5_k > self.m5_d
        )
        kdj_short_signal = (
            self.m5_j_prev > 70 and
            self.m5_k_prev >= self.m5_d_prev and
            self.m5_k < self.m5_d
        )

        entry_signal = (entry_dir == 1 and kdj_long_signal) or \
                       (entry_dir == -1 and kdj_short_signal)

        if not entry_signal:
            return

        # 确认价格在合理位置（日线线位支撑/压力区域）
        levels = self.mp_daily.get('levels', [])
        if not levels:
            return

        zone_desc, zone_num, _, _ = self.get_line_zone(self.mp_daily, current_price)

        # 做多：价格在1-3线区间内
        # 做空：价格在1-3线区间内
        if zone_num > 4:
            self.output(f'【5分钟】KDJ信号出现但价格已超过4线({zone_desc})，信号作废')
            self.signal_stage = self.STAGE_WAIT_H1
            return

        # 计算止损
        stop_price = self._calc_stop_loss(current_price, entry_dir, levels)

        # 计算手数
        volume = self._calc_volume(current_price, stop_price)

        # 计算分批止盈
        tp1, tp2, tp3 = self._calc_take_profits(current_price, entry_dir, levels)

        # 记录入场信息
        self.entry_price = current_price
        self.stop_loss_price = stop_price
        self.take_profit_1 = tp1
        self.take_profit_2 = tp2
        self.take_profit_3 = tp3
        self.total_volume = volume
        self.tp1_done = False
        self.tp2_done = False

        dir_str = '做多(买开)' if entry_dir == 1 else '做空(卖开)'
        kdj_str = f'K={self.m5_k:.1f} D={self.m5_d:.1f} J={self.m5_j:.1f}'
        self.output(
            f'【5分钟入场信号】{dir_str}\n'
            f'  入场价={current_price:.2f} 止损={stop_price:.2f} 手数={volume}\n'
            f'  止盈1={tp1:.2f}(50%) 止盈2={tp2:.2f}(30%) 止盈3={tp3:.2f}(20%)\n'
            f'  KDJ: {kdj_str} | 线位: {zone_desc}'
        )

        self.signal_price = current_price if entry_dir == 1 else -current_price
        self.state_map.entry_price = current_price
        self.state_map.stop_loss = stop_price
        self.state_map.position_vol = volume

        # 执行下单
        if self.params_map.auto_trade and self.trading:
            order_dir = 'buy' if entry_dir == 1 else 'sell'
            entry_price_with_slippage = (
                current_price + self.params_map.pay_up if entry_dir == 1
                else current_price - self.params_map.pay_up
            )
            if self.order_id is not None:
                self.cancel_order(self.order_id)
            self.order_id = self.send_order(
                exchange=self.params_map.exchange,
                instrument_id=self.params_map.instrument_id,
                volume=volume,
                price=entry_price_with_slippage,
                order_direction=order_dir
            )
            self.output(f'【下单】{dir_str} {volume}手 @ {entry_price_with_slippage:.2f}')
        else:
            self.output(f'【信号提示】{dir_str}信号（auto_trade=False，未自动下单）')

        self.signal_stage = self.STAGE_IN_POSITION
        self.state_map.signal_stage = self.signal_stage

        if self.trading:
            self.update_status_bar()

    def _on_m5_realtime(self, kline: KLineData) -> None:
        """5分钟实时推送"""
        pass

    # ============================================================
    # 持仓管理（止盈/止损/动态移仓）
    # ============================================================

    def _manage_position(self, kline: KLineData) -> None:
        """管理已持仓的止盈止损"""
        if not self.mp_daily.get('levels'):
            return

        current_price = float(kline.close)
        entry_dir = self.h1_entry_direction
        levels = self.mp_daily['levels']

        # 获取当前持仓
        position = self.get_position(self.params_map.instrument_id)
        net_pos = position.net_position

        if net_pos == 0:
            # 持仓已清空，重置状态
            self.output('【持仓】仓位已全部平仓，重置信号状态')
            self._reset_signal_state()
            return

        # 止损检查
        stop_hit = (
            (entry_dir == 1 and current_price <= self.stop_loss_price) or
            (entry_dir == -1 and current_price >= self.stop_loss_price)
        )
        if stop_hit:
            self.output(
                f'【止损】触发止损! 当前价={current_price:.2f} '
                f'止损价={self.stop_loss_price:.2f}'
            )
            if self.params_map.auto_trade and self.trading:
                close_dir = 'sell' if entry_dir == 1 else 'buy'
                close_price = (
                    current_price - self.params_map.pay_up if entry_dir == 1
                    else current_price + self.params_map.pay_up
                )
                if self.order_id is not None:
                    self.cancel_order(self.order_id)
                self.order_id = self.auto_close_position(
                    exchange=self.params_map.exchange,
                    instrument_id=self.params_map.instrument_id,
                    volume=abs(net_pos),
                    price=close_price,
                    order_direction=close_dir
                )
            self._reset_signal_state()
            return

        # 分批止盈检查
        # 第一批止盈（50%）
        if not self.tp1_done:
            tp1_hit = (
                (entry_dir == 1 and current_price >= self.take_profit_1) or
                (entry_dir == -1 and current_price <= self.take_profit_1)
            )
            if tp1_hit:
                vol_to_close = max(1, int(abs(net_pos) * 0.5))
                self.output(
                    f'【止盈1】触发第一批止盈(50%) 价格={current_price:.2f} '
                    f'目标={self.take_profit_1:.2f} 平{vol_to_close}手'
                )
                if self.params_map.auto_trade and self.trading:
                    close_dir = 'sell' if entry_dir == 1 else 'buy'
                    close_price = (
                        current_price - self.params_map.pay_up if entry_dir == 1
                        else current_price + self.params_map.pay_up
                    )
                    if self.order_id is not None:
                        self.cancel_order(self.order_id)
                    self.order_id = self.auto_close_position(
                        exchange=self.params_map.exchange,
                        instrument_id=self.params_map.instrument_id,
                        volume=vol_to_close,
                        price=close_price,
                        order_direction=close_dir
                    )
                self.tp1_done = True
                # 止损上移到入场价（保本止损）
                self.stop_loss_price = self.entry_price
                self.output(f'【动态止损】止损上移到入场价 {self.entry_price:.2f}（保本）')

        # 第二批止盈（30%）
        elif not self.tp2_done:
            tp2_hit = (
                (entry_dir == 1 and current_price >= self.take_profit_2) or
                (entry_dir == -1 and current_price <= self.take_profit_2)
            )
            if tp2_hit:
                vol_to_close = max(1, int(abs(net_pos) * 0.6))  # 剩余50%中的60%≈30%
                self.output(
                    f'【止盈2】触发第二批止盈(30%) 价格={current_price:.2f} '
                    f'目标={self.take_profit_2:.2f} 平{vol_to_close}手'
                )
                if self.params_map.auto_trade and self.trading:
                    close_dir = 'sell' if entry_dir == 1 else 'buy'
                    close_price = (
                        current_price - self.params_map.pay_up if entry_dir == 1
                        else current_price + self.params_map.pay_up
                    )
                    if self.order_id is not None:
                        self.cancel_order(self.order_id)
                    self.order_id = self.auto_close_position(
                        exchange=self.params_map.exchange,
                        instrument_id=self.params_map.instrument_id,
                        volume=vol_to_close,
                        price=close_price,
                        order_direction=close_dir
                    )
                self.tp2_done = True
                # 止损上移到第一批止盈位
                self.stop_loss_price = self.take_profit_1
                self.output(f'【动态止损】止损上移到 {self.take_profit_1:.2f}（第一止盈位）')

        # 第三批止盈（剩余20%）
        else:
            tp3_hit = (
                (entry_dir == 1 and current_price >= self.take_profit_3) or
                (entry_dir == -1 and current_price <= self.take_profit_3)
            )
            if tp3_hit:
                self.output(
                    f'【止盈3】触发最终止盈(20%) 价格={current_price:.2f} '
                    f'目标={self.take_profit_3:.2f} 平{abs(net_pos)}手'
                )
                if self.params_map.auto_trade and self.trading:
                    close_dir = 'sell' if entry_dir == 1 else 'buy'
                    close_price = (
                        current_price - self.params_map.pay_up if entry_dir == 1
                        else current_price + self.params_map.pay_up
                    )
                    if self.order_id is not None:
                        self.cancel_order(self.order_id)
                    self.order_id = self.auto_close_position(
                        exchange=self.params_map.exchange,
                        instrument_id=self.params_map.instrument_id,
                        volume=abs(net_pos),
                        price=close_price,
                        order_direction=close_dir
                    )
                self._reset_signal_state()

        # 动态止损：价格每突破一条线，止损上移
        if entry_dir == 1:
            for lv in sorted(levels):
                if current_price > lv and lv > self.stop_loss_price:
                    # 找lv下方的线作为新止损
                    below = [l for l in levels if l < lv]
                    if below:
                        new_stop = max(below)
                        if new_stop > self.stop_loss_price:
                            self.stop_loss_price = new_stop
                            self.state_map.stop_loss = new_stop
                            if not self._is_history:
                                self.output(f'【动态止损】止损上移到 {new_stop:.2f}')

    def _reset_signal_state(self):
        """重置信号状态，等待下一次机会"""
        self.signal_stage = self.STAGE_WAIT_H1
        self.h1_entry_zone = ''
        self.h1_entry_direction = 0
        self.m15_pattern_ok = False
        self.m15_pattern_name = ''
        self.entry_price = 0.0
        self.stop_loss_price = 0.0
        self.tp1_done = False
        self.tp2_done = False
        self.total_volume = 0
        self.signal_price = 0.0
        self.state_map.signal_stage = self.signal_stage
        self.state_map.entry_price = 0.0
        self.state_map.stop_loss = 0.0
        self.state_map.position_vol = 0

    # ============================================================
    # 图表推送
    # ============================================================

    def _push_kline_to_widget(self, kline: KLineData) -> None:
        """将日线K线 + 波神测量线位推送到图表"""
        if not self.widget:
            return
        try:
            data = {"kline": kline, "signal_price": self.signal_price}
            data.update(self.main_indicator_data)
            self.widget.recv_kline(data)
            # 更新水平横线
            self._update_hlines()
        except Exception as e:
            self.output(f'[图表] 推送失败: {e}')

    def _init_hlines(self) -> None:
        """初始化水平横线（在主线程通过monkey-patch调用）"""
        try:
            import pyqtgraph as pg
            kw = self.widget.kline_widget
            crosshair = kw.crosshair
            kline_plot = crosshair.parent()

            line_configs = [
                ('1线', (220, 50, 50), 1.0, False),
                ('2线', (220, 50, 50), 0.8, True),
                ('3线', (220, 50, 50), 1.5, False),
                ('4线', (220, 50, 50), 0.8, True),
                ('5线', (220, 50, 50), 1.5, False),
                ('6线', (220, 50, 50), 0.8, True),
                ('7线', (220, 50, 50), 1.0, False),
                ('8线', (220, 50, 50), 2.0, False),
                ('极线', (255, 140, 0), 1.5, False),
                ('空1线', (50, 100, 220), 1.0, True),
                ('空3线', (50, 100, 220), 1.5, False),
                ('空5线', (50, 100, 220), 1.5, False),
            ]

            for name, color, width, dashed in line_configs:
                pen = pg.mkPen(color=color, width=width,
                               style=pg.QtCore.Qt.PenStyle.DashLine if dashed
                               else pg.QtCore.Qt.PenStyle.SolidLine)
                line = pg.InfiniteLine(angle=0, movable=False, pen=pen)
                line.setValue(0)
                kline_plot.addItem(line)
                self._hlines[name] = line

            self.output('[hlines] 水平横线初始化完成')
        except Exception as e:
            self.output(f'[hlines] 初始化失败: {e}')

    def _update_hlines(self) -> None:
        """更新水平横线位置"""
        if not self._hlines:
            return
        try:
            data = self.main_indicator_data
            for name, line in self._hlines.items():
                val = data.get(name, 0.0)
                if val and val > 0:
                    line.setValue(val)
        except Exception as e:
            self.output(f'[hlines] 更新失败: {e}')

    # ============================================================
    # 历史数据初始化后处理
    # ============================================================

    def _post_history_init(self) -> None:
        """历史数据加载完成后的初始化"""
        producer = self.kg_daily.producer
        n = len(producer.close)
        if n < 10:
            return

        highs = np.array(producer.high, dtype=float)
        lows = np.array(producer.low, dtype=float)
        closes = np.array(producer.close, dtype=float)

        # 找A点（最低点）索引
        lookback = min(n, self.params_map.lookback_daily)
        window_lows = lows[-lookback:]
        offset = n - lookback if n >= lookback else 0
        a_idx_in_window = int(np.argmin(window_lows))
        a_idx = a_idx_in_window + offset

        # 修正B点：A点之后的真正最高点
        if a_idx < n - 1:
            highest_after_a = float(np.max(highs[a_idx:]))
        else:
            highest_after_a = float(highs[a_idx])

        current_b = self.mp_daily.get('end', 0.0) or 0.0
        if highest_after_a > current_b and self.mp_daily.get('start'):
            old_b = current_b
            self.mp_daily['end'] = highest_after_a
            self.mp_daily['levels'], self.mp_daily['level9'] = self.calculate_levels(
                self.mp_daily['start'], highest_after_a, self.mp_daily['direction']
            )
            levels = self.mp_daily['levels']
            self.output(
                f'【日线】修正B点: {old_b:.2f} → {highest_after_a:.2f}（历史最高点）\n'
                f'  线位: 1线={levels[0]:.2f} | 3线={levels[2]:.2f} | '
                f'5线={levels[4]:.2f} | 8线={levels[7]:.2f} | 9线={self.mp_daily["level9"]:.2f}'
            )

        # 更新State
        if self.mp_daily.get('levels'):
            levels = self.mp_daily['levels']
            self.state_map.line1 = levels[0]
            self.state_map.line3 = levels[2]
            self.state_map.line5 = levels[4]
            self.state_map.line8 = levels[7]

        current_price = float(closes[-1])
        self.output(
            f'【初始化完成】当前价格={current_price:.2f} '
            f'日线方向={"向上" if self.daily_direction == 1 else "向下" if self.daily_direction == -1 else "未知"} '
            f'信号阶段={self.signal_stage}'
        )

    # ============================================================
    # 框架回调
    # ============================================================

    def on_tick(self, tick: TickData) -> None:
        super().on_tick(tick)
        self._last_tick = tick
        # 分发tick到各K线生成器
        self.kg_daily.tick_to_kline(tick)
        self.kg_h1.tick_to_kline(tick)
        self.kg_m15.tick_to_kline(tick)
        self.kg_m5.tick_to_kline(tick)

    def on_order_cancel(self, order: OrderData) -> None:
        super().on_order_cancel(order)
        self.order_id = None

    def on_trade(self, trade: TradeData, log: bool = False) -> None:
        super().on_trade(trade, log=True)
        self.order_id = None

    def on_start(self) -> None:
        # 创建四个K线生成器
        self.kg_daily = KLineGenerator(
            callback=self._on_daily_bar,
            real_time_callback=self._on_daily_realtime,
            exchange=self.params_map.exchange,
            instrument_id=self.params_map.instrument_id,
            style='D1'
        )
        self.kg_h1 = KLineGenerator(
            callback=self._on_h1_bar,
            real_time_callback=self._on_h1_realtime,
            exchange=self.params_map.exchange,
            instrument_id=self.params_map.instrument_id,
            style='H1'
        )
        self.kg_m15 = KLineGenerator(
            callback=self._on_m15_bar,
            real_time_callback=self._on_m15_realtime,
            exchange=self.params_map.exchange,
            instrument_id=self.params_map.instrument_id,
            style='M15'
        )
        self.kg_m5 = KLineGenerator(
            callback=self._on_m5_bar,
            real_time_callback=self._on_m5_realtime,
            exchange=self.params_map.exchange,
            instrument_id=self.params_map.instrument_id,
            style='M5'
        )

        # 推送历史数据（日线用于建立A/B点）
        self._is_history = True
        self.kg_daily.push_history_data()
        self._is_history = False

        # 历史数据加载完成后修正B点
        self._post_history_init()

        # 启动框架（初始化widget等）
        super().on_start()

        # monkey-patch widget.update_kline，在主线程初始化水平横线
        self._patch_widget_for_hlines()

        auto_str = '已开启' if self.params_map.auto_trade else '未开启（仅信号提示）'
        self.output(
            f'【波神策略V3启动】\n'
            f'  品种={self.params_map.instrument_id} '
            f'风险比例={self.params_map.risk_pct}% '
            f'自动交易={auto_str}\n'
            f'  信号阶段={self.signal_stage}'
        )

    def on_stop(self) -> None:
        super().on_stop()
        self.output('【波神策略V3停止】')

    def _patch_widget_for_hlines(self) -> None:
        """monkey-patch widget.update_kline，在主线程安全初始化水平横线"""
        try:
            if not self.widget:
                return
            original_update = self.widget.update_kline
            hlines_initialized = [False]
            strategy_ref = self

            def patched_update_kline(data):
                original_update(data)
                if not hlines_initialized[0]:
                    strategy_ref._init_hlines()
                    hlines_initialized[0] = True
                else:
                    strategy_ref._update_hlines()

            self.widget.update_kline = patched_update_kline
            self.output('[hlines] 已注入水平横线更新挂钩')
        except Exception as e:
            self.output(f'[hlines] 注入失败: {e}')
