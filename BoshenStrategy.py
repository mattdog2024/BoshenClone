"""
波神凯线策略 V2 - 新框架版本
基于 pythongo BaseStrategy (Python 3.11+)

迁移说明：
- 旧框架：CtaTemplate + ArrayManager + BarManager + loadDay/loadBar
- 新框架：BaseStrategy + KLineGenerator(style="D1") + producer 数组
- 图表推送：widget.recv_kline({"kline": kline, ...})
- 所有波神核心算法（calculate_levels / get_line_zone / _print_action_summary 等）保持不变
"""

import numpy as np
import pyqtgraph as pg
from pydantic import Field

from pythongo.base import BaseParams, BaseState
from pythongo.classdef import KLineData, TickData
from pythongo.ui import BaseStrategy
from pythongo.utils import KLineGenerator


# ============================================================
# 参数 / 状态模型
# ============================================================

class Params(BaseParams):
    exchange: str = Field(default="SHFE", title="交易所代码")
    instrument_id: str = Field(default="rb2605", title="合约代码")
    lookback_daily: int = Field(default=40, title="日线回看周期(根)", ge=5)
    tolerance_pct: float = Field(default=0.8, title="线位误差百分比(0.8=0.8%)", ge=0.1)
    scalp_mode_enabled: bool = Field(default=True, title="是否启用横盘波段模式")


class State(BaseState):
    daily_direction: int = Field(default=0, title="日线方向(1=上 -1=下)")
    daily_line_desc: str = Field(default="未知", title="日线线位描述")
    signal_stage: str = Field(default="等待日线确认", title="信号阶段")
    line1: float = Field(default=0, title="1线")
    line3: float = Field(default=0, title="3线")
    line5: float = Field(default=0, title="5线")
    line8: float = Field(default=0, title="8线")


# ============================================================
# 策略主类
# ============================================================

class BoshenStrategy(BaseStrategy):
    """
    波神凯线策略 V2（新框架）
    - 日线级别波神八条线测量
    - 自动识别趋势/横盘/回调/反弹
    - 图表实时显示1~8线 + 极线 + 向下测量线
    """

    # 波神八条线比例（来自 BoshenClone/config.json，与博易大师插件完全一致）
    # 1线=1.784, 2线=2.351, 3线=3.027, 4线=3.459, 5线=3.865, 6线=4.622, 7线=5.135, 8线=5.865
    BOSHEN_RATIOS = [1.784, 2.351, 3.027, 3.459, 3.865, 4.622, 5.135, 5.865]
    RATIO_9 = 6.676  # 极线比例（来自 config.json）

    def __init__(self) -> None:
        super().__init__()

        self.params_map = Params()
        self.state_map = State()

        # 日线 K 线生成器
        self.kline_generator_daily: KLineGenerator = None

        # 60分钟 K 线生成器
        self.kline_generator_60min: KLineGenerator = None

        # 波神测量点（日线）
        self.mp_daily = self._new_mp()
        self.mp_daily_down = self._new_mp_down()

        # 波神测量点（60分钟向下测量）
        self.mp_60min_down = self._new_mp_down()

        # 60分钟数据存储（用于形态判断）
        self._60min_highs = []
        self._60min_lows = []
        self._60min_opens = []
        self._60min_closes = []
        self._60min_bar_count = 0

        # 状态变量
        self.daily_direction = 0
        self.daily_line_desc = '未知'
        self.signal_stage = '等待日线确认'
        self.market_mode = '趋势'

        # A/B 点建立后的历史极值
        self.highest_since_ab = 0.0
        self.lowest_since_ab = 999999.0
        self.lowest_since_high = 999999.0
        self.highest_since_low = 0.0

        # A/B 点锁定标志：找到后锁定，只有走完8线才解锁
        self._ab_locked = False
        self._ab_cycle_done = False  # 是否走完了8线

        # 影线测量状态
        self.daily_shadow_phase = False
        self.daily_shadow_level9 = None

        # 历史回放标志（push_history_data 期间为 True）
        self._is_history = True
        self._initial_summary_done = False
        self._last_summary_text = ''

        # 当前 tick
        self._last_tick: TickData = None

        # 图表信号价格（暂不下单，仅显示）
        self.signal_price = 0.0

    # ============================================================
    # 图表指标属性（框架会自动读取这些属性来决定显示哪些线）
    # ============================================================

    @property
    def main_indicator_data(self) -> dict[str, float]:
        """主图指标数据：波神1~8线 + 极线（9线）+ 向下测量线"""
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
        """计算波神八条线价格（与 BoshenClone/algorithms.py 完全一致）"""
        diff = end - start
        levels = [start + diff * r for r in self.BOSHEN_RATIOS]
        level9 = start + diff * self.RATIO_9
        return levels, level9

    def _detect_shadow_bar(self, k_high, k_low, k_open, k_close, direction):
        """判断是否为影线测量法对象"""
        body = abs(k_open - k_close)
        if direction == -1:
            upper_shadow = k_high - max(k_open, k_close)
            is_shadow = upper_shadow > body and body > 0
            shadow_start = k_high
            shadow_end = max(k_open, k_close) if is_shadow else k_low
            body_start, body_end = k_high, k_low
        else:
            lower_shadow = min(k_open, k_close) - k_low
            is_shadow = lower_shadow > body and body > 0
            shadow_start = k_low
            shadow_end = min(k_open, k_close) if is_shadow else k_high
            body_start, body_end = k_low, k_high
        return is_shadow, shadow_start, shadow_end, body_start, body_end

    def _init_down_measurement(self, mp_down, highs, lows, opens, closes, label, start_idx=None):
        """初始化向下测量（从最高点向下）"""
        n = len(closes)
        if n < 5:
            return False

        search_highs = highs[start_idx:] if (start_idx is not None and 0 <= start_idx < n) else highs
        search_offset = start_idx if (start_idx is not None and 0 <= start_idx < n) else 0

        max_idx = int(np.argmax(search_highs)) + search_offset
        k_high = float(highs[max_idx])
        k_low = float(lows[max_idx])
        k_close = float(closes[max_idx])
        k_open = float(opens[max_idx]) if opens is not None else k_close

        is_shadow, shadow_start, shadow_end, body_start, body_end = \
            self._detect_shadow_bar(k_high, k_low, k_open, k_close, -1)

        shadow_levels, shadow_level9 = self.calculate_levels(shadow_start, shadow_end, -1)
        body_levels, body_level9 = self.calculate_levels(body_start, body_end, -1)

        mp_down.update({
            'start': body_start, 'end': body_end,
            'use_shadow': is_shadow, 'shadow_end': shadow_end, 'body_end': body_end,
            'k_high': k_high, 'k_low': k_low, 'k_open': k_open, 'k_close': k_close,
            'shadow_levels': shadow_levels, 'shadow_level9': shadow_level9,
            'levels': body_levels, 'level9': body_level9,
            'phase': 'shadow' if is_shadow else 'body', 'bar_idx': max_idx,
        })

        if is_shadow:
            self.output(
                f'【{label}向下测量】影线测量法: high={k_high:.2f}, 实体顶={max(k_open,k_close):.2f}\n'
                f'  影线线位: 1线={shadow_levels[0]:.2f} | 3线={shadow_levels[2]:.2f} | '
                f'8线={shadow_levels[7]:.2f} | 9线={shadow_level9:.2f}\n'
                f'  单体线位: 1线={body_levels[0]:.2f} | 3线={body_levels[2]:.2f} | '
                f'8线={body_levels[7]:.2f} | 9线={body_level9:.2f}'
            )
        else:
            self.output(
                f'【{label}向下测量】单体测量法: high={k_high:.2f}, low={k_low:.2f}\n'
                f'  线位: 1线={body_levels[0]:.2f} | 3线={body_levels[2]:.2f} | '
                f'8线={body_levels[7]:.2f} | 9线={body_level9:.2f}'
            )
        return True

    def _get_down_active_levels(self, mp_down):
        """获取当前有效的向下测量线位"""
        if mp_down['start'] is None:
            return [], None, 'none'
        if mp_down['phase'] == 'shadow' and mp_down['shadow_levels']:
            return mp_down['shadow_levels'], mp_down['shadow_level9'], '影线'
        return mp_down['levels'], mp_down['level9'], '单体'

    def get_line_zone(self, mp, current_price):
        """区域化线位判断，返回 (zone_desc, zone_num, is_key, is_near_extreme)"""
        if not mp['levels']:
            return '未初始化', 0, False, False

        levels = mp['levels']
        level9 = mp['level9']
        direction = mp['direction']
        tol = current_price * (self.params_map.tolerance_pct / 100.0)

        if level9 and abs(current_price - level9) <= tol * 1.5:
            return '极线(9线)区域', 9, True, True
        if level9:
            if direction == 1 and current_price > level9:
                return '超过极线！需重新测量', 9.5, True, True
            if direction == -1 and current_price < level9:
                return '超过极线！需重新测量', 9.5, True, True

        for i, level in enumerate(levels):
            if abs(current_price - level) <= tol:
                line_num = i + 1
                is_key = line_num in [1, 3, 5, 6, 7, 8]
                return f'{line_num}线区域', line_num, is_key, False

        if direction == 1:
            if current_price < levels[0]:
                return '1线以下', 0, False, False
            for i in range(len(levels) - 1):
                if levels[i] < current_price < levels[i + 1]:
                    n = i + 1
                    is_key = n in [2, 4, 6]
                    return f'{n}线半区域', n + 0.5, is_key, False
            if current_price > levels[7]:
                return '8线半区域（接近极线）', 8.5, True, True
        else:
            if current_price > levels[0]:
                return '1线以上', 0, False, False
            for i in range(len(levels) - 1):
                if levels[i + 1] < current_price < levels[i]:
                    n = i + 1
                    is_key = n in [2, 4, 6]
                    return f'{n}线半区域', n + 0.5, is_key, False
            if current_price < levels[7]:
                return '8线半区域（接近极线）', 8.5, True, True

        return '未知区域', 0, False, False

    def _find_ab_from_arrays(self, highs, lows, lookback, direction):
        """从数组中找 A/B 点（单体测量法）
        
        波神单体测量法：A和B来自同一根K线
        向上：A = 最低点K线的low，B = 同一根K线的high
        向下：A = 最高点K线的high，B = 同一根K线的low
        振幅 = |B - A|，线位 = A + 振幅 × 比例
        """
        n = min(len(highs), lookback)
        if n < 5:
            return None, None, None, None
        h = highs[-n:]
        l = lows[-n:]
        if direction == 1:
            # 找绝对最低点K线
            a_idx = int(np.argmin(l))
            a_price = float(l[a_idx])   # A = 该K线的low
            b_price = float(h[a_idx])   # B = 同一根K线的high（单体测量法）
            return a_price, b_price, float(h[a_idx]), float(l[a_idx])
        else:
            # 找绝对最高点K线
            a_idx = int(np.argmax(h))
            a_price = float(h[a_idx])   # A = 该K线的high
            b_price = float(l[a_idx])   # B = 同一根K线的low（单体测量法）
            return a_price, b_price, float(h[a_idx]), float(l[a_idx])

    def _check_pattern(self, highs, lows, direction):
        """检查形态是否成立（需要至少3根K线）"""
        if len(highs) < 3:
            return False
        if direction == -1:
            left_idx = -2 if highs[-2] > highs[-3] else -3
            return lows[-1] < lows[left_idx] and highs[-1] <= highs[left_idx] * 1.001
        else:
            left_idx = -2 if lows[-2] < lows[-3] else -3
            return highs[-1] > highs[left_idx] and lows[-1] >= lows[left_idx] * 0.999

    def _check_pattern_realtime(self, highs, lows, direction, realtime_price):
        """实时形态检测（用 tick 价格）"""
        if len(highs) < 3:
            return False
        if direction == -1:
            left_idx = -1 if highs[-1] > highs[-2] else -2
            left_low = lows[left_idx]
            realtime_low = min(self.lowest_since_high, realtime_price) if self.lowest_since_high < 999999.0 else realtime_price
            return realtime_low < left_low
        else:
            lookback = min(10, len(lows) - 1)
            recent_lows = lows[-lookback:]
            min_offset = int(np.argmin(recent_lows))
            left_idx = -(lookback - min_offset)
            left_high = highs[left_idx]
            realtime_high = max(self.highest_since_low, realtime_price) if self.highest_since_low > 0 else realtime_price
            if left_idx == -1:
                return realtime_high > left_high and realtime_high > lows[left_idx]
            return realtime_high > left_high

    def detect_sideways(self, closes_daily, highs_h1, lows_h1):
        """检测横盘震荡"""
        if len(closes_daily) < 5 or len(highs_h1) < 20:
            return False, 0, 0
        daily_closes = closes_daily[-5:]
        daily_range_pct = (max(daily_closes) - min(daily_closes)) / min(daily_closes) * 100
        h1_highs = highs_h1[-20:]
        h1_lows = lows_h1[-20:]
        range_high = float(max(h1_highs))
        range_low = float(min(h1_lows))
        h1_range_pct = (range_high - range_low) / range_low * 100
        daily_up = all(daily_closes[i] <= daily_closes[i+1] for i in range(len(daily_closes)-1))
        daily_dn = all(daily_closes[i] >= daily_closes[i+1] for i in range(len(daily_closes)-1))
        if daily_up or daily_dn:
            return False, range_high, range_low
        is_sideways = daily_range_pct < 0.8 and h1_range_pct < 1.5
        return is_sideways, range_high, range_low

    # ============================================================
    # 新框架生命周期
    # ============================================================

    def on_tick(self, tick: TickData) -> None:
        super().on_tick(tick)
        self._last_tick = tick

        # 第一个有效 tick 到来时输出初始分析
        if not self._initial_summary_done and tick.last_price > 0:
            self._initial_summary_done = True
            self._output_daily_summary(tick.last_price)

        # 实时更新极值 + 监控新高/新低触发重新测量
        if not self._is_history and self.mp_daily['start'] is not None:
            price = tick.last_price
            trend = self.mp_daily['direction']
            if trend == 1:
                if price < self.lowest_since_high:
                    self.lowest_since_high = price
                    self.highest_since_low = price
                if price > self.highest_since_ab + 0.5:
                    old_high = self.highest_since_ab
                    self.highest_since_ab = price
                    self.lowest_since_high = price
                    self.output(f'实时高点更新: {old_high:.2f} → {price:.2f}，重新初始化向下测量...')
                    self.mp_daily_down = self._new_mp_down()
                    self._output_daily_summary(price)
            elif trend == -1:
                if price > self.highest_since_low:
                    self.highest_since_low = price
                if price < self.lowest_since_ab - 0.5:
                    old_low = self.lowest_since_ab
                    self.lowest_since_ab = price
                    self.output(f'实时低点更新: {old_low:.2f} → {price:.2f}，重新初始化向下测量...')
                    self.mp_daily_down = self._new_mp_down()
                    self._output_daily_summary(price)

        # 转发给日线生成器（日线不需要 tick 驱动，但保留接口）
        if self.kline_generator_daily is not None:
            self.kline_generator_daily.tick_to_kline(tick)

    def on_start(self) -> None:
        # 创建日线 K 线生成器
        self.kline_generator_daily = KLineGenerator(
            callback=self.on_daily_bar,
            real_time_callback=self.on_daily_bar_realtime,
            exchange=self.params_map.exchange,
            instrument_id=self.params_map.instrument_id,
            style="D1"
        )

        # 创建60分钟 K 线生成器
        self.kline_generator_60min = KLineGenerator(
            callback=self.on_60min_bar,
            exchange=self.params_map.exchange,
            instrument_id=self.params_map.instrument_id,
            style="H1"  # 60分钟
        )

        # 加载历史日线数据（这会同步调用 on_daily_bar 多次）
        self._is_history = True
        self.kline_generator_daily.push_history_data()

        # 加载历史60分钟数据
        try:
            self.kline_generator_60min.push_history_data()
        except Exception as e:
            self.output(f'60分钟历史数据加载失败（不影响日线功能）: {e}')
        self._is_history = False

        # 历史日线加载完成后，做一次完整初始化
        self._post_history_init()

        # 调用父类 on_start（会触发 widget 初始化 + load_data_signal）
        super().on_start()

        # 在 widget 初始化完成后，设置白色背景
        self._apply_light_theme()

    def on_stop(self) -> None:
        super().on_stop()

    def _apply_light_theme(self) -> None:
        """将图表背景改为白色亮色主题
        
        注意：pg.setConfigOption 必须在 QApplication 启动前调用才有效。
        框架已经先启动了 Qt，所以我们在 widget 初始化完成后
        直接操作图表组件设置白色背景。
        """
        try:
            import time as _time
            # 等待 widget 初始化完成（最多等 3 秒）
            for _ in range(30):
                if self.widget and self.widget.kline_widget:
                    break
                _time.sleep(0.1)

            if not (self.widget and self.widget.kline_widget):
                return

            kw = self.widget.kline_widget

            # 1. 设置 PlotWidget 背景为白色
            #    crosshair.parent() 就是创建时传入的 pg.PlotWidget
            plot_widget = kw.crosshair.parent()
            if hasattr(plot_widget, 'setBackground'):
                plot_widget.setBackground('w')

            # 2. 设置 GraphicsLayout 背景为白色
            if hasattr(kw, 'kline_layout'):
                kw.kline_layout.setBackground((255, 255, 255, 255))

            # 3. 设置每个 PlotItem 的 ViewBox 背景为白色
            for plot_item in [kw.kline_plot_item, kw.vol_plot_item, kw.bottom_chart]:
                if plot_item is not None:
                    vb = plot_item.getViewBox()
                    if vb is not None:
                        vb.setBackgroundColor((255, 255, 255, 255))
                    # 设置坐标轴颜色为深色
                    axis = plot_item.getAxis('right')
                    if axis:
                        axis.setPen(color=(50, 50, 50, 255), width=0.8)

            # 4. 设置标题颜色为深色
            if hasattr(kw, 'layout_title'):
                kw.layout_title.setText(
                    kw.layout_title.text, bold=True, color='k'
                )
        except Exception as e:
            self.output(f'[light theme] 设置白色主题失败: {e}')

    # ============================================================
    # 日线 K 线回调
    # ============================================================

    def on_daily_bar(self, kline: KLineData) -> None:
        """日线 K 线完成回调（历史 + 实盘每日收盘后触发）"""
        producer = self.kline_generator_daily.producer
        highs = np.array(producer.high, dtype=float)
        lows = np.array(producer.low, dtype=float)
        opens = np.array(producer.open, dtype=float)
        closes = np.array(producer.close, dtype=float)
        n = len(closes)

        if n < 5:
            # 数据不足，直接推图表
            self._push_kline_to_widget(kline)
            return

        # 更新极值（实盘阶段）
        if not self._is_history and self.mp_daily['start'] is not None:
            if kline.high > self.highest_since_ab:
                self.highest_since_ab = kline.high
            if kline.low < self.lowest_since_ab:
                self.lowest_since_ab = kline.low

        # 执行日线信号检查
        self._check_daily_signal(kline, highs, lows, opens, closes)

        # 推送到图表
        self._push_kline_to_widget(kline)

        # 实盘阶段输出分析
        if not self._is_history:
            tick_price = self._last_tick.last_price if self._last_tick and self._last_tick.last_price > 0 else None
            self._output_daily_summary(tick_price or kline.close)

    def on_daily_bar_realtime(self, kline: KLineData) -> None:
        """日线实时 K 线回调（盘中每个 tick 触发，用于实时更新图表）"""
        self._push_kline_to_widget(kline)

    # ============================================================
    # 60分钟 K 线回调
    # ============================================================

    def on_60min_bar(self, kline: KLineData) -> None:
        """60分钟K线完成回调（历史+实盘每小时触发）"""
        # 存储60分钟数据
        self._60min_highs.append(float(kline.high))
        self._60min_lows.append(float(kline.low))
        self._60min_opens.append(float(kline.open))
        self._60min_closes.append(float(kline.close))
        self._60min_bar_count += 1

        # 历史阶段只存数据，不分析
        if self._is_history:
            return

        # 实盘阶段：更新60分钟向下测量
        self._update_60min_analysis()

    def _init_60min_down_measurement(self):
        """初始化60分钟向下测量
        
        从60分钟数据中找到日线高点（highest_since_ab）对应的60分钟K线
        以该K线作为基准，向下测量8条线
        """
        n = len(self._60min_highs)
        if n < 3:
            return

        h = np.array(self._60min_highs)
        l = np.array(self._60min_lows)
        o = np.array(self._60min_opens)
        c = np.array(self._60min_closes)

        # 找60分钟最高点（对应日线highest_since_ab）
        # 在最近60根60分钟K线里找最高点
        lookback = min(n, 60)
        h_recent = h[-lookback:]
        l_recent = l[-lookback:]
        o_recent = o[-lookback:]
        c_recent = c[-lookback:]

        max_idx = int(np.argmax(h_recent))
        k_high = float(h_recent[max_idx])
        k_low = float(l_recent[max_idx])
        k_open = float(o_recent[max_idx])
        k_close = float(c_recent[max_idx])

        # 初始化60分钟向下测量
        self.mp_60min_down = self._new_mp_down()
        result = self._init_down_measurement(
            self.mp_60min_down, h_recent, l_recent, o_recent, c_recent, '60分钟',
            start_idx=max_idx
        )
        return result

    def _update_60min_analysis(self):
        """实盘阶段更新60分钟分析（每根60分钟K线完成后调用）"""
        if not self._60min_highs:
            return
        # 如果60分钟向下测量还没初始化，初始化一次
        if self.mp_60min_down['start'] is None:
            self._init_60min_down_measurement()

    def get_60min_analysis(self, current_price):
        """获取60分钟当前分析结果
        
        返回：
        - line_zone: 当前在60分钟的几线（如'3线区域'）
        - line_num: 线号（0=1线以下，1-8=具体线，9=极线）
        - pattern: 当前K线形态（'阳包阴'/'锤子线'/'止跌信号'/None）
        - shadow_levels: 影线测量线位
        - body_levels: 单体测量线位
        """
        result = {
            'line_zone': '未知',
            'line_num': 0,
            'pattern': None,
            'shadow_levels': [],
            'body_levels': [],
            'shadow_level9': None,
            'body_level9': None,
            'base_high': 0.0,
        }

        n = len(self._60min_highs)
        if n < 3:
            return result

        # 如果60分钟向下测量还没初始化，先初始化
        if self.mp_60min_down['start'] is None:
            self._init_60min_down_measurement()

        if self.mp_60min_down['start'] is None:
            return result

        result['base_high'] = self.mp_60min_down['start']

        # 获取当前有效线位
        active_levels, active_level9, phase_label = self._get_down_active_levels(self.mp_60min_down)
        if active_levels:
            result['line_zone'], result['line_num'], _, _ = self.get_line_zone(
                {'levels': active_levels, 'level9': active_level9, 'direction': -1},
                current_price
            )

        # 影线和单体线位
        result['shadow_levels'] = self.mp_60min_down.get('shadow_levels', [])
        result['shadow_level9'] = self.mp_60min_down.get('shadow_level9')
        result['body_levels'] = self.mp_60min_down.get('levels', [])
        result['body_level9'] = self.mp_60min_down.get('level9')

        # 60分钟K线形态判断（最近3根K线）
        result['pattern'] = self._check_60min_pattern()

        return result

    def _check_60min_pattern(self):
        """检查60分钟最近K线形态
        
        做多形态（价格在支撑区域时）：
        - 阳包阴：当前阳线实体包住前一根阴线
        - 锤子线：下影线>实体2倍的阳线
        - 止跌信号：连续2根阴线后出现阳线且收盘突破前阴线高点
        
        做空形态（价格在压力区域时）：
        - 阴包阳：当前阴线实体包住前一根阳线
        - 长上影线：上影线>实体2倍的阴线
        - 止涨信号：连续2根阳线后出现阴线且收盘跌破前阳线低点
        """
        n = len(self._60min_closes)
        if n < 3:
            return None

        # 取最近3根K线
        c = self._60min_closes
        o = self._60min_opens
        h = self._60min_highs
        l = self._60min_lows

        # 当前K线
        cur_c, cur_o, cur_h, cur_l = c[-1], o[-1], h[-1], l[-1]
        # 前一根K线
        pre_c, pre_o, pre_h, pre_l = c[-2], o[-2], h[-2], l[-2]
        # 前两根K线
        pre2_c, pre2_o = c[-3], o[-3]

        cur_body = abs(cur_c - cur_o)
        pre_body = abs(pre_c - pre_o)
        cur_is_bull = cur_c > cur_o
        pre_is_bull = pre_c > pre_o
        pre2_is_bull = pre2_c > pre2_o

        # === 做空形态（日线回调时关注）===
        # 阴包阳：当前阴线实体包住前一根阳线
        if (not cur_is_bull and pre_is_bull and
                cur_o >= pre_c and cur_c <= pre_o and cur_body > pre_body * 0.8):
            return '阴包阳（看空形态确认）'

        # 长上影线阴线（射击之星）
        upper_shadow = cur_h - max(cur_c, cur_o)
        if not cur_is_bull and cur_body > 0 and upper_shadow > cur_body * 2:
            return '长上影线阴线（看空形态）'

        # 止涨信号：连续2根阳线后出现阴线且收盘跌破前阳线低点
        if (not cur_is_bull and pre_is_bull and pre2_is_bull and
                cur_c < pre_l):
            return '止涨信号（连阳后阴线跌破前低）'

        # === 做多形态（回调结束时关注）===
        # 阳包阴：当前阳线实体包住前一根阴线
        if (cur_is_bull and not pre_is_bull and
                cur_o <= pre_c and cur_c >= pre_o and cur_body > pre_body * 0.8):
            return '阳包阴（看多形态确认）'

        # 锤子线：下影线>实体2倍的阳线
        lower_shadow = min(cur_c, cur_o) - cur_l
        if cur_is_bull and cur_body > 0 and lower_shadow > cur_body * 2:
            return '锤子线（看多形态）'

        # 止跌信号：连续2根阴线后出现阳线且收盘突破前阴线高点
        if (cur_is_bull and not pre_is_bull and not pre2_is_bull and
                cur_c > pre_h):
            return '止跌信号（连阴后阳线突破前高）'

        return None

    def _calc_3group_resonance(self, current_price):
        """计算日线影线+日线单体+60分钟单体三组共振区域"""
        tol = 8.0  # 允许偏差点数
        results = []
        d_down = self.mp_daily_down
        m60 = self.mp_60min_down
        if not d_down or not m60:
            return results
        d_shadow = d_down.get('shadow_levels', [])
        d_body = d_down.get('levels', [])
        m60_body = m60.get('levels', [])
        # 三组共振：日线影线 + 日线单体 + 60分钟单体
        for i, ds in enumerate(d_shadow):
            if ds >= current_price:
                continue
            for j, db in enumerate(d_body):
                if db >= current_price or abs(ds - db) > tol * 2:
                    continue
                for k, mb in enumerate(m60_body):
                    if mb >= current_price or abs(ds - mb) > tol * 2:
                        continue
                    center = (ds + db + mb) / 3
                    stars = '★★★' if abs(ds - db) < tol and abs(db - mb) < tol else '★★'
                    results.append(f"{stars} {center:.0f}区 (日线影线{i+1}线={ds:.0f} + 日线单体{j+1}线={db:.0f} + 60分钟单体{k+1}线={mb:.0f})")
        # 去重并按价格降序排序
        seen = set()
        unique = []
        for r in sorted(results, key=lambda x: float(x.split('区')[0].split()[-1]), reverse=True):
            center = round(float(r.split('区')[0].split()[-1]) / 5) * 5
            if center not in seen:
                seen.add(center)
                unique.append(r)
        return unique[:5]

    # ============================================================
    # 历史数据加载完成后的初始化
    # ============================================================

    def _post_history_init(self):
        """历史日线加载完成后，统一初始化 A/B 点和向下测量"""
        producer = self.kline_generator_daily.producer
        if producer is None or len(producer.close) < 5:
            return

        highs = np.array(producer.high, dtype=float)
        lows = np.array(producer.low, dtype=float)
        opens = np.array(producer.open, dtype=float)
        closes = np.array(producer.close, dtype=float)
        n = len(closes)

        # 如果 mp_daily 还没初始化，手动初始化一次
        if self.mp_daily['start'] is None:
            self._init_daily_measurement(highs, lows, opens, closes)

        # 初始化极值
        if self.mp_daily['start'] is not None:
            a_price = self.mp_daily['start']
            trend = self.mp_daily['direction']

            # 找 A 点在数组中的位置
            a_idx = 0
            for i in range(n):
                if trend == 1 and abs(lows[i] - a_price) < 1.0:
                    a_idx = i
                    break
                elif trend == -1 and abs(highs[i] - a_price) < 1.0:
                    a_idx = i
                    break

            self.highest_since_ab = float(np.max(highs[a_idx:]))
            self.lowest_since_ab = float(np.min(lows[a_idx:]))

            # 初始化高点后最低价
            high_idx = a_idx
            for j in range(a_idx, n):
                if highs[j] >= self.highest_since_ab - 0.5:
                    high_idx = j
                    break
            self.lowest_since_high = float(np.min(lows[high_idx:]))

            # 初始化低点后最高价
            pullback_low_idx = high_idx
            pullback_low_val = self.lowest_since_high
            for j in range(high_idx, n):
                if lows[j] <= pullback_low_val + 0.5:
                    pullback_low_idx = j
                    break
            after_low_highs = highs[pullback_low_idx + 1:]
            self.highest_since_low = float(np.max(after_low_highs)) if len(after_low_highs) > 0 else 0.0

            self.output(
                f'初始化极值: A点索引={a_idx}, 最高={self.highest_since_ab:.2f}, '
                f'最低={self.lowest_since_ab:.2f}, 高点后最低={self.lowest_since_high:.2f}, '
                f'低点后最高={self.highest_since_low:.2f}'
            )

            # 初始化向下测量
            if n >= 5:
                self._init_down_measurement(
                    self.mp_daily_down, highs, lows, opens, closes, '日线',
                    start_idx=a_idx
                )

        # 历史数据加载完成后，锁定A/B点
        if self.mp_daily['start'] is not None:
            self._ab_locked = True
            self._ab_cycle_done = False
            self.output(
                f'【日线】历史数据加载完成，A/B点已锁定！\n'
                f'  A点={self.mp_daily["start"]:.2f}, B点={self.mp_daily["end"]:.2f}, '
                f'方向={"向上" if self.mp_daily["direction"]==1 else "向下"}\n'
                f'  1线={self.mp_daily["levels"][0]:.2f} | 3线={self.mp_daily["levels"][2]:.2f} | '
                f'5线={self.mp_daily["levels"][4]:.2f} | 8线={self.mp_daily["levels"][7]:.2f}\n'
                f'  只有价格走到八线才会解锁重新识别'
            )

        # 初始化60分钟向下测量（如果有历史60分钟数据）
        if len(self._60min_highs) >= 3:
            self._init_60min_down_measurement()
        else:
            self.output('60分钟历史数据不足，将在实盘中动态初始化')

        self.output('策略初始化完成，等待第一个 tick 输出实时价格分析...')

    def _init_daily_measurement(self, highs, lows, opens, closes):
        """初始化日线 A/B 点（单体测量法）
        
        波神单体测量法：A和B来自同一根K线
        A = 最低点K线的low，B = 同一根K线的high
        振幅 = B - A，线位 = A + 振幅 × 比例
        """
        n = len(closes)
        lookback = min(n, 40)  # 最近40根日线找基准
        recent_lows = lows[-lookback:]
        recent_highs = highs[-lookback:]
        # A点 = 绝对最低点K线的low
        min_idx = int(np.argmin(recent_lows))
        a_price = float(recent_lows[min_idx])
        # B点 = 同一根K线的high（单体测量法！不是A点之后的最高点）
        b_price = float(recent_highs[min_idx])
        amplitude = b_price - a_price
        self.mp_daily['start'] = a_price
        self.mp_daily['end'] = b_price
        self.mp_daily['direction'] = 1
        self.mp_daily['levels'], self.mp_daily['level9'] = self.calculate_levels(a_price, b_price, 1)
        self.output(
            f'【日线】初始化基准K线(单体测量法): '
            f'A点(low)={a_price:.2f}, B点(high)={b_price:.2f}, 振幅={amplitude:.2f}, 方向=向上\n'
            f'  1线={self.mp_daily["levels"][0]:.2f} | 2线={self.mp_daily["levels"][1]:.2f} | '
            f'3线={self.mp_daily["levels"][2]:.2f} | 5线={self.mp_daily["levels"][4]:.2f} | '
            f'8线={self.mp_daily["levels"][7]:.2f}'
        )

    # ============================================================
    # 日线信号检查
    # ============================================================

    def _check_daily_signal(self, kline, highs, lows, opens, closes):
        """日线分析核心逻辑"""
        current_price = kline.close
        n = len(closes)
        if n < 10:
            return

        # 首次初始化
        if self.mp_daily['start'] is None:
            self._init_daily_measurement(highs, lows, opens, closes)
            if not self.mp_daily['levels']:
                return
            zone_desc, _, _, _ = self.get_line_zone(self.mp_daily, current_price)
            self.daily_line_desc = zone_desc
            return

        trend = self.mp_daily['direction']

        # ── A/B 点锁定机制 ──────────────────────────────────────────
        # 已锁定时：只检查是否走完8线（解锁条件）
        if self._ab_locked and self.mp_daily['levels']:
            eight_line = self.mp_daily['levels'][7]
            cycle_done = (
                (trend == 1 and current_price >= eight_line * 0.998) or
                (trend == -1 and current_price <= eight_line * 1.002)
            )
            if cycle_done and not self._ab_cycle_done:
                self._ab_cycle_done = True
                self._ab_locked = False  # 解锁，允许重新识别
                self.output(
                    f'【日线】走完完整8线循环！8线={eight_line:.2f}，'
                    f'当前价={current_price:.2f}，解锁A/B点，准备重新识别...'
                )
                # 重新识别A/B点
                self._ab_cycle_done = False
                self._init_daily_measurement(highs, lows, opens, closes)
                self._ab_locked = True  # 重新锁定
            # 锁定期间不更新A/B点，直接跳到线位判断
            pass
        else:
            # 未锁定：在历史回放期间每根K线更新，历史结束后锁定
            if not self._is_history:
                # 实盘阶段且未锁定：重新识别并锁定
                lookback = min(n, self.params_map.lookback_daily)
                a_price, b_price, k_high, k_low = self._find_ab_from_arrays(highs, lows, lookback, trend)
                if a_price is not None and (self.mp_daily['start'] != a_price or self.mp_daily['end'] != b_price):
                    self.mp_daily['start'] = a_price
                    self.mp_daily['end'] = b_price
                    self.mp_daily['direction'] = trend
                    self.mp_daily['k_high'] = k_high
                    self.mp_daily['k_low'] = k_low
                    self.mp_daily['levels'], self.mp_daily['level9'] = self.calculate_levels(a_price, b_price, trend)
                    dir_str = '向上' if trend == 1 else '向下'
                    self.output(
                        f'【日线】识别A/B点: A={a_price:.2f}, B={b_price:.2f}, 方向={dir_str}\n'
                        f'  线位: 1线={self.mp_daily["levels"][0]:.2f} | 3线={self.mp_daily["levels"][2]:.2f} | '
                        f'5线={self.mp_daily["levels"][4]:.2f} | 8线={self.mp_daily["levels"][7]:.2f} | '
                        f'9线={self.mp_daily["level9"]:.2f}'
                    )
                self._ab_locked = True
                self.output(f'【日线】A/B点已锁定: A={self.mp_daily["start"]:.2f}, B={self.mp_daily["end"]:.2f}，走完8线才会解锁')
            else:
                # 历史回放期间：每根K线更新（找最终的A/B点）
                lookback = min(n, self.params_map.lookback_daily)
                a_price, b_price, k_high, k_low = self._find_ab_from_arrays(highs, lows, lookback, trend)
                if a_price is not None and (self.mp_daily['start'] != a_price or self.mp_daily['end'] != b_price):
                    self.mp_daily['start'] = a_price
                    self.mp_daily['end'] = b_price
                    self.mp_daily['direction'] = trend
                    self.mp_daily['k_high'] = k_high
                    self.mp_daily['k_low'] = k_low
                    self.mp_daily['levels'], self.mp_daily['level9'] = self.calculate_levels(a_price, b_price, trend)

        if not self.mp_daily['levels']:
            return

        zone_desc, zone_num, is_key, is_extreme = self.get_line_zone(self.mp_daily, current_price)
        self.daily_line_desc = zone_desc

        # 更新 State（用于状态栏显示）
        levels = self.mp_daily['levels']
        self.state_map.daily_direction = trend
        self.state_map.daily_line_desc = zone_desc
        self.state_map.signal_stage = self.signal_stage
        self.state_map.line1 = levels[0]
        self.state_map.line3 = levels[2]
        self.state_map.line5 = levels[4]
        self.state_map.line8 = levels[7]

        # 形态检测
        pattern_ok = self._check_pattern(highs, lows, -trend) if (is_key or is_extreme) else False
        if (is_key or is_extreme) and pattern_ok:
            self.daily_direction = -trend
            action = '上涨' if self.daily_direction == 1 else '下跌'
            self.signal_stage = f'日线{action}确认，等待60分钟'
        else:
            self.daily_direction = trend
            action = '上涨' if trend == 1 else '下跌'
            self.signal_stage = f'日线{action}，等待60分钟机会'

        if self._is_history:
            return

        # 实盘时更新状态栏
        if self.trading:
            self.update_status_bar()

    # ============================================================
    # 图表推送
    # ============================================================

    def _push_kline_to_widget(self, kline: KLineData) -> None:
        """将日线 K 线 + 波神测量线位推送到图表
        
        键名必须与 main_indicator_data 属性返回的字典键名完全一致，
        框架才能正确将数据关联到对应的指标线上。
        """
        if not self.widget:
            return
        try:
            # 直接使用 main_indicator_data 的当前值，保证键名一致
            indicator_values = self.main_indicator_data
            data = {"kline": kline, "signal_price": self.signal_price}
            data.update(indicator_values)
            self.widget.recv_kline(data)
        except Exception:
            pass

    # ============================================================
    # 日线分析输出（叙述式）
    # ============================================================

    def _output_daily_summary(self, realtime_price=None):
        """输出日线状态摘要"""
        if not self.mp_daily['levels']:
            return

        levels = self.mp_daily['levels']
        level9 = self.mp_daily['level9']
        start = self.mp_daily['start']
        end = self.mp_daily['end']
        trend = self.mp_daily['direction']

        producer = self.kline_generator_daily.producer if self.kline_generator_daily else None
        if realtime_price and realtime_price > 0:
            current_price = realtime_price
        elif producer and len(producer.close) > 0:
            current_price = float(producer.close[-1])
        else:
            return

        zone_desc, _, _, _ = self.get_line_zone(self.mp_daily, current_price)

        if trend == 1:
            measure_info = f'A={start:.2f}(最低价) B={end:.2f}(最高价) 向上测量'
            dir_str = '上涨趋势'
        else:
            measure_info = f'A={start:.2f}(最高价) B={end:.2f}(最低价) 向下测量'
            dir_str = '下跌趋势'

        recent_high = max(self.highest_since_ab, current_price) if self.highest_since_ab > 0 else current_price
        recent_low = min(self.lowest_since_ab, current_price) if self.lowest_since_ab < 999999.0 else current_price

        tol_pct = 0.01
        is_pullback = False
        is_rebound = False
        waiting_pattern = False
        pullback_from = ''
        rebound_from = ''

        if producer and len(producer.high) >= 3:
            highs = np.array(producer.high, dtype=float)
            lows = np.array(producer.low, dtype=float)

            if trend == 1:
                pullback_low = min(self.lowest_since_high, current_price) if self.lowest_since_high < 999999.0 else current_price
                space_condition_met = recent_high >= levels[2] * (1 - tol_pct) and pullback_low < recent_high * 0.99
                if space_condition_met:
                    if recent_high >= levels[7] * (1 - tol_pct):
                        pullback_from = "8线"
                    elif recent_high >= levels[5] * (1 - tol_pct):
                        pullback_from = "6线"
                    elif recent_high >= levels[4] * (1 - tol_pct):
                        pullback_from = "5线"
                    elif recent_high >= levels[2] * (1 - tol_pct):
                        pullback_from = "3线"
                    pattern_confirmed = self._check_pattern_realtime(highs, lows, -1, current_price)
                    if pattern_confirmed:
                        is_pullback = True
                    else:
                        waiting_pattern = True
            else:
                rebound_high = max(self.highest_since_low, current_price) if self.highest_since_low > 0 else current_price
                space_condition_rebound = recent_low <= levels[2] * (1 + tol_pct) and rebound_high > recent_low * 1.01
                if space_condition_rebound:
                    if recent_low <= levels[7] * (1 + tol_pct):
                        rebound_from = "8线"
                    elif recent_low <= levels[5] * (1 + tol_pct):
                        rebound_from = "6线"
                    elif recent_low <= levels[4] * (1 + tol_pct):
                        rebound_from = "5线"
                    elif recent_low <= levels[2] * (1 + tol_pct):
                        rebound_from = "3线"
                    pattern_confirmed_rebound = self._check_pattern(highs, lows, 1)
                    if pattern_confirmed_rebound:
                        is_rebound = True
                    else:
                        waiting_pattern = True

        # 计算回调结束信息
        pullback_bottom = 0.0
        pullback_end_confirmed = False
        rebound_zone = ''
        rebound_pattern_confirmed = False
        rebound_high_price = 0.0

        if trend == 1 and is_pullback:
            pullback_bottom = self.lowest_since_high if self.lowest_since_high < 999999.0 else 0.0
            if pullback_bottom > 0 and current_price > pullback_bottom * 1.003:
                pullback_end_confirmed = True
                rebound_zone, _, _, _ = self.get_line_zone(self.mp_daily, current_price)
                rebound_high_price = self.highest_since_low if self.highest_since_low > 0 else current_price
                if producer and len(producer.high) >= 3:
                    highs = np.array(producer.high, dtype=float)
                    lows = np.array(producer.low, dtype=float)
                    rebound_pattern_confirmed = self._check_pattern_realtime(highs, lows, 1, current_price)

        # 获取回调分析数据
        pullback_data = None
        if (trend == 1 and is_pullback) or (trend == -1 and is_rebound):
            pullback_data = self._get_pullback_analysis_data(current_price)

        self._print_action_summary(
            current_price=current_price,
            trend=trend,
            dir_str=dir_str,
            advice='',
            pullback_data=pullback_data,
            is_pullback=is_pullback,
            is_rebound=is_rebound,
            waiting_pattern=waiting_pattern,
            recent_high=recent_high,
            recent_low=recent_low,
            pullback_from=pullback_from if trend == 1 else rebound_from,
            pullback_bottom=pullback_bottom,
            pullback_end_confirmed=pullback_end_confirmed,
            rebound_zone=rebound_zone,
            rebound_pattern_confirmed=rebound_pattern_confirmed,
            rebound_high_price=rebound_high_price
        )

    def _get_pullback_analysis_data(self, current_price):
        """获取回调/反弹分析数据（共振区）"""
        data = {'resonance_zones': [], 'closest_zone': None, 'closest_diff': 9999}
        mp = self.mp_daily_down
        if mp['start'] is None:
            return data

        groups = []
        if mp['shadow_levels']:
            groups.append(('日线影线', mp['shadow_levels'], mp['shadow_level9']))
        if mp['levels']:
            groups.append(('日线单体', mp['levels'], mp['level9']))

        resonance_zones = []
        if len(groups) >= 2:
            tolerance = 8.0
            d_shadow = next((g for g in groups if g[0] == '日线影线'), None)
            d_body = next((g for g in groups if g[0] == '日线单体'), None)
            daily_internal = []
            if d_shadow and d_body:
                d_sl, d_sl9 = d_shadow[1], d_shadow[2]
                d_bl, d_bl9 = d_body[1], d_body[2]
                s_pts = [(lv, '日线影线', i+1) for i, lv in enumerate(d_sl)]
                if d_sl9: s_pts.append((d_sl9, '日线影线', 9))
                b_pts = [(lv, '日线单体', i+1) for i, lv in enumerate(d_bl)]
                if d_bl9: b_pts.append((d_bl9, '日线单体', 9))
                used_s, used_b = set(), set()
                for si, (sp, sl, sn) in enumerate(s_pts):
                    if si in used_s: continue
                    for bi, (bp, bl, bn) in enumerate(b_pts):
                        if bi in used_b: continue
                        if abs(sp - bp) <= tolerance:
                            avg_p = (sp + bp) / 2
                            daily_internal.append((avg_p, [(sp, sl, sn), (bp, bl, bn)], 2))
                            used_s.add(si); used_b.add(bi); break
            for avg_p, matched, cnt in daily_internal:
                resonance_zones.append((avg_p, matched, cnt))
            resonance_zones.sort(key=lambda x: (-x[2], -x[0]))
            for avg_p, matched, cnt in resonance_zones[:10]:
                star = '*' * cnt
                match_desc = ' + '.join(f'{l}{n}线' for p, l, n in matched)
                data['resonance_zones'].append({
                    'price': avg_p,
                    'desc': f'{star} {avg_p:.0f}区 ({match_desc})',
                    'stars': cnt
                })
        if resonance_zones:
            below = [(p, m, c) for p, m, c in resonance_zones if p < current_price - 5]
            if below:
                avg_p, matched, cnt = below[0]
                star = '*' * cnt
                match_desc = ' + '.join(f'{l}{n}线' for p, l, n in matched)
                data['closest_zone'] = f'{star} {avg_p:.0f}区 ({match_desc})'
                data['closest_diff'] = current_price - avg_p
        return data

    def _print_action_summary(self, current_price, trend, dir_str, advice, pullback_data,
                               is_pullback, is_rebound, waiting_pattern=False,
                               recent_high=0.0, recent_low=0.0, pullback_from='',
                               pullback_bottom=0.0, pullback_end_confirmed=False,
                               rebound_zone='', rebound_pattern_confirmed=False,
                               rebound_high_price=0.0):
        """输出叙述式日线提示"""
        lines = ["--------------------------------"]
        start = self.mp_daily.get('start', 0.0)
        end = self.mp_daily.get('end', 0.0)
        mp_levels = self.mp_daily.get('levels', [])
        mp_level9 = self.mp_daily.get('level9', 0.0)

        lines.append(f"日线大方向：{dir_str}")
        lock_status = '✅已锁定（走完8线才解锁）' if self._ab_locked else '⚠️未锁定'
        lines.append(f"日线测量基准：A点={start:.2f}，B点={end:.2f} [{lock_status}]")
        if self.mp_daily.get('levels'):
            lvs = self.mp_daily['levels']
            lines.append(f"  线位: 1线={lvs[0]:.1f} | 3线={lvs[2]:.1f} | 5线={lvs[4]:.1f} | 8线={lvs[7]:.1f}")

        if (is_pullback or is_rebound) and pullback_end_confirmed and pullback_bottom > 0:
            action_type = "回调" if is_pullback else "反弹"
            if is_pullback and recent_high > 0:
                lines.append(f"本波段最高走到了 {pullback_from}区域，最高点位 {recent_high:.2f}")
            elif is_rebound and recent_low > 0:
                lines.append(f"本波段最低走到了 {pullback_from}区域，最低点位 {recent_low:.2f}")
            lines.append(f"然后从{pullback_from}开始向下{action_type}，{action_type}形态已得到确认。")
            if pullback_data and pullback_data.get('closest_zone'):
                lines.append(f"大概{action_type}到：{pullback_data['closest_zone']}。")
            pullback_bottom_zone, _, _, _ = self.get_line_zone(self.mp_daily, pullback_bottom)
            lines.append(f"实际{action_type}结束位置：{pullback_bottom_zone}（{pullback_bottom:.2f}）。")
            cur_zone, _, _, _ = self.get_line_zone(self.mp_daily, current_price)
            lines.append(f"目前已从最低点 {pullback_bottom:.2f} 上涨到了 {cur_zone}，当前点位 {current_price:.2f}。")
            if rebound_pattern_confirmed:
                if mp_levels and trend == 1:
                    next_targets = [(ln, mp_levels[ln-1]) for ln in [3, 5, 6, 8] if mp_levels[ln-1] > current_price]
                    if mp_level9 and mp_level9 > current_price:
                        next_targets.append((9, mp_level9))
                    if next_targets:
                        ln, lv = next_targets[0]
                        lname = '极线(9线)' if ln == 9 else f'{ln}线'
                        lines.append(f"向上形态已确认，未来大概上涨到 {lname} = {lv:.2f} 位置。")
            else:
                if rebound_high_price > 0:
                    rh_zone, _, _, _ = self.get_line_zone(self.mp_daily, rebound_high_price)
                    lines.append(f"目前反弹高点在 {rh_zone}（{rebound_high_price:.2f}）。")
                # 根据知识库：回调结束后反弹 = 顺着日线大方向找多单机会！
                # 日线大方向是向上，回调结束就是新的做多机会
                if is_pullback and trend == 1:
                    lines.append("▶ 日线回调已结束，现在是顺着日线大方向做多的机会！")
                    lines.append("  ✅ 切挆60分钟图，等昆60分钟出现向上形态（阳包阴/止跌信号）")
                    lines.append("  ✅ 形态成立后再切挆15分钟找精确入场点做多")
                    if mp_levels:
                        next_targets = [(ln, mp_levels[ln-1]) for ln in [3, 5, 6, 8] if ln <= len(mp_levels) and mp_levels[ln-1] > current_price]
                        if next_targets:
                            ln, lv = next_targets[0]
                            lines.append(f"  ★ 多单目标：日线{ln}线 = {lv:.2f}")
                else:
                    lines.append("向上形态尚未成立，等待新K线突破反弹高点后才能确认上涨。")
                    lines.append("如果有K线跌破最低点，则下跌形态再次成立，需重新向下测量。")

            # 不管回调有没有结束，只要日线处于回调阶段，就要显示60分钟分析
            lines.append("")
            lines.append("【60分钟分析】")
            m60 = self.mp_60min_down
            if m60 and m60.get('start') is not None:
                base_h = m60['start']
                lines.append(f"60分钟向下测量（高点={base_h:.2f}）")
                if m60.get('shadow_levels'):
                    sl = m60['shadow_levels']
                    sl9 = m60.get('shadow_level9')
                    sl_str = ' | '.join(f'{i+1}线={sl[i]:.2f}' for i in [0,1,2,5,7] if i < len(sl))
                    if sl9: sl_str += f' | 9线={sl9:.2f}'
                    lines.append(f"  影线测量: {sl_str}")
                if m60.get('levels'):
                    bl = m60['levels']
                    bl9 = m60.get('level9')
                    bl_str = ' | '.join(f'{i+1}线={bl[i]:.2f}' for i in [0,1,2,5,7] if i < len(bl))
                    if bl9: bl_str += f' | 9线={bl9:.2f}'
                    lines.append(f"  单体测量: {bl_str}")
                active_60, active_60_9, phase_60 = self._get_down_active_levels(m60)
                if active_60:
                    zone_60, _, _, _ = self.get_line_zone(
                        {'levels': active_60, 'level9': active_60_9, 'direction': -1},
                        current_price
                    )
                    lines.append(f"60分钟当前位置: {zone_60}（{phase_60}）")
                pattern_60 = self._check_60min_pattern()
                if pattern_60:
                    lines.append(f"60分钟形态: ❗ {pattern_60}")
                    if '看空' in pattern_60 or '阴包阳' in pattern_60 or '止涨' in pattern_60:
                        lines.append("→ 60分钟向下形态已确认！可切挆15分钟找精确入场点做空")
                else:
                    lines.append("【60分钟形态】: 尚未出现向下形态，继续等待...")
                if m60.get('levels') and self.mp_daily_down.get('levels'):
                    lines.append("")
                    lines.append("【共振目标区】（日线影线+日线单体+60分钟单体重合）")
                    resonance = self._calc_3group_resonance(current_price)
                    for zone in resonance[:3]:
                        lines.append(f"  {zone}")
            else:
                lines.append("60分钟向下测量尚未初始化，等待实盘数据...")

        elif is_pullback or is_rebound:
            action_type = "回调" if is_pullback else "反弹"
            cur_zone, _, _, _ = self.get_line_zone(self.mp_daily, current_price)
            if is_pullback and recent_high > 0:
                lines.append(f"本波段最高走到了 {pullback_from}，最高点位 {recent_high:.2f}")
            elif is_rebound and recent_low > 0:
                lines.append(f"本波段最低走到了 {pullback_from}，最低点位 {recent_low:.2f}")
            lines.append(f"然后从{pullback_from}开始向下{action_type}，{action_type}形态已确认。")
            lines.append(f"当前价格在 {cur_zone}，点位 {current_price:.2f}")

            # 根据日线到达的线位，输出正确操作指引
            if is_pullback and trend == 1:
                # 日线上涨到达3线后回调：这是调整，不是反转！
                # 正确做法：切挆60分钟找回调做空机会，不要等日线向上形态！
                pullback_from_num = {'3线': 3, '5线': 5, '6线': 6, '7线': 7, '8线': 8}.get(pullback_from, 0)
                if pullback_from_num >= 7:
                    lines.append("▶ 日线到达7-8线反转区！这是趋势性反转，需等日线形态确认后再做空。")
                elif pullback_from_num >= 5:
                    lines.append("▶ 日线到达5-6线关键抗拓区！多单全部离场，可切挆60分钟找轻仓做空机会。")
                elif pullback_from_num >= 3:
                    lines.append("▶ 日线到达3线，这是调整开始，不是反转！日线大方向仍是向上。")
                    lines.append("  ✅ 多单落袋为安，禁止新开多单")
                    lines.append("  ✅ 切挆60分钟图，寻找回调做空机会（轻仓短空，顺着调整方向）")
                    lines.append("  ⚠️ 空单目标：60分钟2-3线就要考虑出场，不要贪心")
                    lines.append("  ⚠️ 调整结束后再回到日线方向找多单机会")

            # 显示向下测量线位作为回调目标参考
            if pullback_data and pullback_data.get('closest_zone'):
                lines.append(f"回调目标参考位：{pullback_data['closest_zone']}")
            else:
                d_levels, d_level9, d_label = self._get_down_active_levels(self.mp_daily_down)
                if d_levels and len(d_levels) >= 8:
                    lines.append(f"日线向下测量参考（{d_label}）：第7线={d_levels[6]:.0f} | 第8线={d_levels[7]:.0f}")
                    if d_level9:
                        lines.append(f"  极线={d_level9:.0f}（最大回调目标）")

            # 添加60分钟分析输出
            lines.append("")
            lines.append("【60分钟分析】")
            m60 = self.mp_60min_down
            if m60 and m60.get('start') is not None:
                base_h = m60['start']
                lines.append(f"60分钟向下测量（高点={base_h:.2f}）")
                if m60.get('shadow_levels'):
                    sl = m60['shadow_levels']
                    sl9 = m60.get('shadow_level9')
                    sl_str = ' | '.join(f'{i+1}线={sl[i]:.2f}' for i in [0,1,2,5,7] if i < len(sl))
                    if sl9: sl_str += f' | 9线={sl9:.2f}'
                    lines.append(f"  影线测量: {sl_str}")
                if m60.get('levels'):
                    bl = m60['levels']
                    bl9 = m60.get('level9')
                    bl_str = ' | '.join(f'{i+1}线={bl[i]:.2f}' for i in [0,1,2,5,7] if i < len(bl))
                    if bl9: bl_str += f' | 9线={bl9:.2f}'
                    lines.append(f"  单体测量: {bl_str}")
                # 60分钟当前在几线
                active_60, active_60_9, phase_60 = self._get_down_active_levels(m60)
                if active_60:
                    zone_60, _, _, _ = self.get_line_zone(
                        {'levels': active_60, 'level9': active_60_9, 'direction': -1},
                        current_price
                    )
                    lines.append(f"60分钟当前位置: {zone_60}（{phase_60}）")
                # 60分钟K线形态
                pattern_60 = self._check_60min_pattern()
                if pattern_60:
                    lines.append(f"60分钟形态: ❗ {pattern_60}")
                    if '看空' in pattern_60 or '阴包阳' in pattern_60 or '止涨' in pattern_60:
                        lines.append("→ 60分钟向下形态已确认！可切接15分钟找精确入场点做空")
                else:
                    lines.append("【60分钟形态】: 尚未出现向下形态，继续等待...")
                # 日线+60分钟共振区
                if m60.get('levels') and self.mp_daily_down.get('levels'):
                    lines.append("")
                    lines.append("【共振目标区】（日线影线+日线单体+60分钟单体重合）")
                    resonance = self._calc_3group_resonance(current_price)
                    for zone in resonance[:3]:
                        lines.append(f"  {zone}")
            else:
                lines.append("60分钟向下测量尚未初始化，等待实盘数据...")

        elif waiting_pattern:
            action_type = "回调" if trend == 1 else "反弹"
            cur_zone, _, _, _ = self.get_line_zone(self.mp_daily, current_price)
            if trend == 1 and recent_high > 0:
                lines.append(f"本波段最高走到了 {pullback_from}区域，最高点位 {recent_high:.2f}")
            elif trend == -1 and recent_low > 0:
                lines.append(f"本波段最低走到了 {pullback_from}区域，最低点位 {recent_low:.2f}")
            lines.append(f"当前价格回落到了 {cur_zone}，当前点位 {current_price:.2f}")
            lines.append(f"目前正在{action_type}，但尚未形成向下的 K 线形态，无法确认{action_type}是否正式开始。")
            lines.append("继续等待，形态未成立前不做任何判断。")

        else:
            cur_zone, cur_zone_num, _, _ = self.get_line_zone(self.mp_daily, current_price)
            b_point = self.mp_daily.get('end', 0.0) or 0.0

            if mp_levels:
                if trend == 1:
                    # 判断是否处于B点回落阶段（价格在B点以下且尚未突破任何测量线）
                    if current_price < mp_levels[0] and b_point > 0:
                        # B点回落阶段
                        b_zone, _, _, _ = self.get_line_zone(self.mp_daily, b_point)
                        lines.append(f"价格从 B点({b_point:.2f}) 回落，当前在 {cur_zone}，点位 {current_price:.2f}")
                        lines.append(f"说明：日线大方向向上，但价格还未突破第1线({mp_levels[0]:.2f})，还在测量起始区间内。")
                        # 显示向下测量线位作为参考
                        mp_down = self.mp_daily_down
                        if mp_down and mp_down.get('start'):
                            d_levels, d_level9, d_label = self._get_down_active_levels(mp_down)
                            if d_levels:
                                lines.append(f"向下测量参考线位（{d_label}）：")
                                for ln_idx, ln_num in enumerate([0, 2, 4, 7]):
                                    if ln_num < len(d_levels):
                                        lines.append(f"  {ln_num+1}线 = {d_levels[ln_num]:.2f}")
                        lines.append(f"等待价格突破 1线({mp_levels[0]:.2f}) 后，才能确认日线上涨开始。")
                    else:
                        # 已突破至少一条线
                        lines.append(f"当前走到了 {cur_zone}，点位 {current_price:.2f}")
                        next_targets = [(ln, mp_levels[ln-1]) for ln in [3, 5, 6, 8] if mp_levels[ln-1] > current_price]
                        if mp_level9 and mp_level9 > current_price:
                            next_targets.append((9, mp_level9))
                        if next_targets:
                            ln, lv = next_targets[0]
                            lname = '极线(9线)' if ln == 9 else f'{ln}线'
                            lines.append(f"日线大方向向上，上方最近目标线位：{lname} = {lv:.2f}")
                        else:
                            lines.append("已超过所有线位，待重新测量。")
                else:
                    if current_price > mp_levels[0] and b_point > 0:
                        lines.append(f"价格从 B点({b_point:.2f}) 反弹，当前在 {cur_zone}，点位 {current_price:.2f}")
                        lines.append(f"说明：日线大方向向下，但价格还未突破第1线({mp_levels[0]:.2f})，还在测量起始区间内。")
                        lines.append(f"等待价格突破 1线({mp_levels[0]:.2f}) 后，才能确认日线下跌开始。")
                    else:
                        lines.append(f"当前走到了 {cur_zone}，点位 {current_price:.2f}")
                        next_targets = [(ln, mp_levels[ln-1]) for ln in [3, 5, 6, 8] if mp_levels[ln-1] < current_price]
                        if mp_level9 and mp_level9 < current_price:
                            next_targets.append((9, mp_level9))
                        if next_targets:
                            ln, lv = next_targets[0]
                            lname = '极线(9线)' if ln == 9 else f'{ln}线'
                            lines.append(f"日线大方向向下，下方最近目标线位：{lname} = {lv:.2f}")
                        else:
                            lines.append("已超过所有线位，待重新测量。")

        lines.append("--------------------------------")
        summary_text = "\n".join(lines)
        if self._last_summary_text != summary_text:
            self.output("\n" + summary_text)
            self._last_summary_text = summary_text
