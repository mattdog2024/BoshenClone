# -*- coding: utf-8 -*-
"""
波神凯线策略 V2 - BoshenStrategy
基于波神凯线测量方法的半自动交易提示策略
适用平台：无限易 InfiniTrader PythonGO V1

策略逻辑（V2 升级版）：
1. 日线：定大方向，找最近有效波段高低点，测量八条线，判断当前所处线位
2. 60分钟：判断顺势/调整，找关键线位，等待形态成立
   - 顺势到标准线位时，切换到15分钟/5分钟提前判断是否回调
   - 60分钟到1线而5分钟才在2线时，直接提示入场（不等5分钟形态）
3. 15分钟（桥梁层）：辅助判断60分钟的回调/反转
4. 5分钟：形态+KDJ配合，发出入场/出场提示；横盘时做波段

核心升级点（来自用户操盘心得）：
- 线位区域化：大于N线小于N+1线的都算N线半，不死板
- 超过线位一点点再回来（长影线）也算该线位的反应
- 多时区共振：5分钟极线+60分钟标准线位同时出现，信号最强
- 小时区提前判断大时区出场：5分钟到极线+形态 → 预判60分钟回调
- 横盘识别：日线十字星/小实体+60分钟高低点收窄 → 切换到5分钟波段模式

测量方法（与波神Clone一致）：
- A点和B点取自同一根K线：
    上涨方向：A点=回看内最低点K线的最低价(low)，B点=同根K线的最高价(high)
    下跌方向：A点=回看内最高点K线的最高价(high)，B点=同根K线的最低价(low)
- 公式：Level = A + (B - A) * Ratio
- 超过八线后重新寻找新的基准K线（循环结束）

波神比率：1.784, 2.351, 3.027, 3.459, 3.865, 4.622, 5.135, 5.865, 6.676
"""

import datetime
from ctaBase import *
from ctaTemplate import *
try:
    from ctaTemplate import KLWidget  # 无限易图表组件（客户端环境才有）
    _KLWIDGET_AVAILABLE = True
except ImportError:
    KLWidget = None
    _KLWIDGET_AVAILABLE = False

# ============================================================
# 参数映射表（显示在无限易界面左侧参数栏）
# ============================================================
paramMap = {
    'vtSymbol': '合约代码',
    'exchange': '交易所',
    'investor': '投资者账号',
    'lookback_daily': '日线回看周期',
    'lookback_h1': '60分钟回看周期',
    'lookback_m15': '15分钟回看周期',
    'lookback_m5': '5分钟回看周期',
    'tolerance_pct': '线位误差百分比',
    'enable_m15': '启用15分钟桥梁层',
    'enable_range_band': '启用线位区域化',
    'early_entry_enabled': '启用提前入场',
    'scalp_mode_enabled': '启用横盘波段模式',
}
paramList = list(paramMap.keys())

# ============================================================
# 变量映射表（显示在无限易界面状态栏）
# ============================================================
varMap = {
    'trading': '交易中',
    'daily_direction': '日线方向',
    'daily_line_desc': '日线线位描述',
    'h1_direction': '60分钟方向',
    'h1_line_desc': '60分钟线位描述',
    'm15_line_desc': '15分钟线位描述',
    'signal_stage': '信号阶段',
    'market_mode': '行情模式',
}
varList = list(varMap.keys())


class boshen_strategy(CtaTemplate):
    """波神凯线半自动提示策略 V2"""

    # ============================================================
    # 波神比率（一线到八线对应的比率）
    # ============================================================
    # 与用户 GitHub BoshenClone 源码完全一致
    BOSHEN_RATIOS = [1.784, 2.351, 3.027, 3.459, 3.865, 4.622, 5.135, 5.865]
    # 九线比率（极线，超过则可能重新循环）
    RATIO_9 = 6.676

    # 关键线位索引（0-based）：1线、2线半、3线、5线、6线、7线、8线
    # 2线半 = 在2线和3线之间的区域
    KEY_LINE_INDICES = [0, 2, 4, 5, 6, 7]  # 1线、3线、5线、6线、7线、8线

    # ============================================================
    # 默认参数
    # ============================================================
    vtSymbol = 'rb2605'        # 合约代码（螺纹钢主力）
    exchange = 'SHFE'          # 交易所
    investor = ''              # 投资者账号
    lookback_daily = 100       # 日线回看周期（根），约半年，确保能找到大波段的真正低点
    lookback_h1 = 40           # 60分钟回看周期（根）
    lookback_m15 = 30          # 15分钟回看周期（根）
    lookback_m5 = 30           # 5分钟回看周期（根）
    tolerance_pct = 0.8        # 线位误差百分比（0.8表示0.8%，比V1更宽松）
    enable_m15 = True          # 是否启用15分钟桥梁层
    enable_range_band = True   # 是否启用线位区域化判断
    early_entry_enabled = True # 是否启用提前入场（60分钟1线+5分钟2线时直接入）
    scalp_mode_enabled = True  # 是否启用横盘波段模式

    def __init__(self, ctaEngine=None, setting={}):
        """初始化"""
        super().__init__(ctaEngine, setting)

        # 状态变量
        self.daily_direction = 0       # 日线方向：1=向上 -1=向下 0=未知
        self.daily_line_desc = '未知'  # 日线线位描述（区域化）
        self.h1_direction = 0          # 60分钟方向
        self.h1_line_desc = '未知'     # 60分钟线位描述
        self.m15_line_desc = '未知'    # 15分钟线位描述
        self.signal_stage = '等待日线确认'
        self.market_mode = '趋势'      # 趋势 / 横盘

        # K线数据缓存（ArrayManager）
        self.am_week = ArrayManager(size=60)   # 周线缓存
        self.am_daily = ArrayManager(size=60)   # 日线缓存，加载60根日线（约半年）
        self.am_h1 = ArrayManager(size=100)
        self.am_m15 = ArrayManager(size=100)
        self.am_m5 = ArrayManager(size=100)

        # 各时间周期的测量数据
        self.mp_daily = self._new_mp()   # 日线测量点
        self.mp_h1 = self._new_mp()      # 60分钟测量点
        self.mp_m15 = self._new_mp()     # 15分钟测量点
        self.mp_m5 = self._new_mp()      # 5分钟测量点
        self.bm = BarManager(self.onBar, 1, None)
        self.bm_h1 = BarManager(self.onBarH1_raw, 60, self.onBarH1)
        self.bm_m15 = BarManager(self.onBarM15_raw, 15, self.onBarM15)
        self.bm_m5 = BarManager(self.onBarM5_raw, 5, self.onBarM5)

        # KDJ数据
        self.kdj_k = 50.0
        self.kdj_d = 50.0
        self.kdj_j = 50.0

        # 信号防重复
        self.last_signal_time = None
        self.last_exit_signal_time = None

        # 横盘检测
        self.h1_range_high = 0.0   # 60分钟横盘区间高点
        self.h1_range_low = 0.0    # 60分钟横盘区间低点
        self.scalp_direction = 0   # 横盘波段方向

        self.tick = None
        self._is_history = True   # 历史回放标志，onStart结束后设为False
        self._initial_summary_done = False  # 初始摘要是否已输出（等第一个tick到来后输出）

        # A/B点建立后的历史极值（用于判断回调/反弹状态）
        self.highest_since_ab = 0.0   # A/B点建立后的最高价
        self.lowest_since_ab = 999999.0  # A/B点建立后的最低价
        self.lowest_since_high = 999999.0  # 高点建立后的最低价（用于判断回调深度）
        self.highest_since_low = 0.0   # 低点建立后的最高价（用于判断反弹高度）

        # 回调分析：从高点向下测量（日线 + 60分钟）
        # 用于判断回调目标位和多时区共振
        self.mp_daily_down = self._new_mp_down()  # 日线从高点向下测量
        self.mp_h1_down = self._new_mp_down()     # 60分钟从高点向下测量
        # 注：60分钟影线底部识别与向上测量已统一到_output_pullback_analysis中展示四组线位重合区逻辑
        # 影线测量状态（日线）：记录是否处于影线测量阶段
        # 影线阶段：先用影线短基准测量；超过影线8线后切换为单体测量
        self.daily_shadow_phase = False   # True=当前使用影线测量法，False=单体测量法
        self.daily_shadow_level9 = None   # 影线测量法的9线（极线），超过后切换单体
        self.h1_shadow_phase = False      # 60分钟影线测量状态
        self.h1_shadow_level9 = None      # 60分钟影线9线

        # 调试计数器
        self._debug_bar_count = 0
        self._debug_m5_count = 0
        self._debug_h1_count = 0

        # ============================================================
        # K线图可视化（波神测量线）
        # 在无限易客户端中弹出专属图表窗口，实时显示1-8线
        # ============================================================
        if _KLWIDGET_AVAILABLE and KLWidget is not None:
            self.widgetClass = KLWidget  # 绑定图表组件
        self.widget = None              # 图表实例（getGui后自动赋值）
        # 主图显示的波神测量线（1线~8线，以及向下测量的回调目标线）
        self.mainSigs = [
            'line1', 'line2', 'line3', 'line4',
            'line5', 'line6', 'line7', 'line8',
            'line9',          # 极线
            'down_line1', 'down_line3', 'down_line5',  # 向下测量关键线位
        ]
        self.subSigs = []  # 副图暂不使用

    def _new_mp(self):
        """创建一个新的测量点数据结构"""
        return {
            'start': None,
            'end': None,
            'direction': 0,
            'use_shadow': False,
            'k_high': None,
            'k_low': None,
            'levels': [],
            'level9': None,
        }

    def _new_mp_down(self):
        """创建一个新的向下测量数据结构（包含影线测量法支持）"""
        return {
            'start': None,           # A点（高点，high）
            'end': None,             # B点（低点，影线时=实体底）
            'direction': -1,         # 始终向下
            'use_shadow': False,     # 是否影线测量阶段
            'shadow_end': None,      # 影线测量时的B点（实体底）
            'body_end': None,        # 单体测量时的B点（K线最低价）
            'k_high': None,          # 基准K线最高价
            'k_low': None,           # 基准K线最低价
            'k_open': None,          # 基准K线开盘价
            'k_close': None,         # 基准K线收盘价
            'shadow_levels': [],     # 影线测量的线位（1~8线）
            'shadow_level9': None,   # 影线测量的极线
            'levels': [],            # 单体测量的线位（1~8线）
            'level9': None,          # 单体测量的极线
            'phase': 'shadow',       # 'shadow'=影线阶段, 'body'=单体阶段
            'bar_idx': -1,           # 基准K线在am中的索引
        }

    # ============================================================
    # 波神核心算法
    # ============================================================

    def calculate_levels(self, start, end, direction):
        """
        计算波神八条线价格
        公式：Level = Start + (End - Start) * Ratio
        （与用户 GitHub BoshenClone 源码 algorithms.py 完全一致）
        
        向上测量：start=A点(最低价), end=B点(最高价), diff为正，线位在B点上方
        向下测量：start=A点(最高价), end=B点(最低价), diff为负，线位在B点下方
        """
        diff = end - start  # 向上时diff为正，向下时diff为负，方向自动包含在其中
        levels = []
        for ratio in self.BOSHEN_RATIOS:
            levels.append(start + diff * ratio)
        level9 = start + diff * self.RATIO_9
        return levels, level9

    def _detect_shadow_bar(self, k_high, k_low, k_open, k_close, direction):
        """
        判断一根K线是否为影线测量法的对象（测速配线）
        
        影线测量法规则（波神课程）：
        当一根K线的影线长度 > 实体长度时，该K线为“测速配线”
        影线测量时：先用影线部分作为A/B基准（较短）
        超过影线测量的9线（极线）后，切换为单体测量法
        
        direction=-1（向下测量）：
          判断上影线 = high - max(open, close)
          实体 = |open - close|
          若上影线 > 实体 → 影线测量：A=high, B=max(open,close)（实体顶部）
          若上影线 <= 实体 → 单体测量：A=high, B=low
        
        direction=1（向上测量）：
          判断下影线 = min(open, close) - low
          实体 = |open - close|
          若下影线 > 实体 → 影线测量：A=low, B=min(open,close)（实体底部）
          若下影线 <= 实体 → 单体测量：A=low, B=high
        
        返回: (is_shadow, shadow_ab_start, shadow_ab_end, body_ab_start, body_ab_end)
        """
        body = abs(k_open - k_close)
        
        if direction == -1:  # 向下测量，判断上影线
            upper_shadow = k_high - max(k_open, k_close)
            is_shadow = upper_shadow > body and body > 0  # 上影线大于实体
            if is_shadow:
                # 影线测量：A=high, B=实体顶部（短基准）
                shadow_start = k_high
                shadow_end = max(k_open, k_close)
            else:
                shadow_start = k_high
                shadow_end = k_low
            # 单体测量：A=high, B=low（长基准）
            body_start = k_high
            body_end = k_low
        else:  # 向上测量，判断下影线
            lower_shadow = min(k_open, k_close) - k_low
            is_shadow = lower_shadow > body and body > 0  # 下影线大于实体
            if is_shadow:
                # 影线测量：A=low, B=实体底部（短基准）
                shadow_start = k_low
                shadow_end = min(k_open, k_close)
            else:
                shadow_start = k_low
                shadow_end = k_high
            # 单体测量：A=low, B=high（长基准）
            body_start = k_low
            body_end = k_high
        
        return is_shadow, shadow_start, shadow_end, body_start, body_end

    def _init_down_measurement(self, mp_down, am, label, start_idx=None):
        """
        初始化向下测量：在am中找到最高点K线，应用影线测量法判断
        
        影线测量法规则（先用短的）：
        1. 如果高点K线的上影线 > 实体 → 影线测量阶段
           - 影线测量：A=high, B=max(open,close)（实体顶）
           - 单体测量：A=high, B=low（备用，超过影线8线后启用）
        2. 如果上影线 <= 实体 → 直接单体测量阶段
        
        start_idx: 只在am[start_idx:](即A点之后)搜索最高点，为None时搜索全部
        返回: True=初始化成功, False=失败
        """
        if not am.inited or am.count < 5:
            return False
        
        # 找到am中的最高点K线
        # 如果指定start_idx，只在A点之后的数据中搜索（避免选到历史旧高点）
        highs = am.high[:am.count]
        lows = am.low[:am.count]
        opens = am.open[:am.count] if hasattr(am, 'open') else None
        closes = am.close[:am.count]
        
        if start_idx is not None and 0 <= start_idx < am.count:
            search_highs = highs[start_idx:]
            search_offset = start_idx
        else:
            search_highs = highs
            search_offset = 0
        
        max_idx = int(search_highs.argmax()) + search_offset
        k_high = float(highs[max_idx])
        k_low = float(lows[max_idx])
        k_close = float(closes[max_idx])
        # 如果没有open数据，用close代替
        k_open = float(opens[max_idx]) if opens is not None else k_close
        
        # 影线判断
        is_shadow, shadow_start, shadow_end, body_start, body_end = \
            self._detect_shadow_bar(k_high, k_low, k_open, k_close, -1)
        
        # 计算影线测量线位
        shadow_levels, shadow_level9 = self.calculate_levels(shadow_start, shadow_end, -1)
        # 计算单体测量线位
        body_levels, body_level9 = self.calculate_levels(body_start, body_end, -1)
        
        # 更新mp_down
        mp_down['start'] = body_start    # A点（始终用high）
        mp_down['end'] = body_end        # B点（单体时用low）
        mp_down['use_shadow'] = is_shadow
        mp_down['shadow_end'] = shadow_end
        mp_down['body_end'] = body_end
        mp_down['k_high'] = k_high
        mp_down['k_low'] = k_low
        mp_down['k_open'] = k_open
        mp_down['k_close'] = k_close
        mp_down['shadow_levels'] = shadow_levels
        mp_down['shadow_level9'] = shadow_level9
        mp_down['levels'] = body_levels
        mp_down['level9'] = body_level9
        mp_down['phase'] = 'shadow' if is_shadow else 'body'
        mp_down['bar_idx'] = max_idx
        
        # 输出初始化信息
        if is_shadow:
            upper_shadow = k_high - max(k_open, k_close)
            body_size = abs(k_open - k_close)
            self.output(
                f'【{label}向下测量】影线测量法: '
                f'高点K线 high={k_high:.2f}, 实体顶={max(k_open,k_close):.2f}, '
                f'上影线={upper_shadow:.2f} > 实体={body_size:.2f}\n'
                f'  影线测量: A={shadow_start:.2f}, B={shadow_end:.2f}\n'
                f'  影线线位: 1线={shadow_levels[0]:.2f} | 3线={shadow_levels[2]:.2f} | '
                f'6线={shadow_levels[5]:.2f} | 8线={shadow_levels[7]:.2f} | 9线={shadow_level9:.2f}\n'
                f'  单体测量(备用): A={body_start:.2f}, B={body_end:.2f}\n'
                f'  单体线位: 1线={body_levels[0]:.2f} | 3线={body_levels[2]:.2f} | '
                f'6线={body_levels[5]:.2f} | 8线={body_levels[7]:.2f} | 9线={body_level9:.2f}'
            )
        else:
            self.output(
                f'【{label}向下测量】单体测量法: '
                f'高点K线 high={k_high:.2f}, low={k_low:.2f}\n'
                f'  单体线位: 1线={body_levels[0]:.2f} | 3线={body_levels[2]:.2f} | '
                f'6线={body_levels[5]:.2f} | 8线={body_levels[7]:.2f} | 9线={body_level9:.2f}'
            )
        
        return True

    def _check_down_phase_switch(self, mp_down, current_price, label):
        """
        检查向下测量是否需要切换阶段：
        影线阶段：当价格超过影线8线（价格下穿影线8线）时，切换为单体测量
        返回: True=发生了阶段切换
        """
        if mp_down['start'] is None:
            return False
        
        if mp_down['phase'] == 'shadow' and mp_down['shadow_levels']:
            shadow_8line = mp_down['shadow_levels'][7]  # 影线8线
            shadow_9line = mp_down['shadow_level9']     # 影线9线（极线）
            
            # 向下测量：价格低于影线8线 = 超过影线8线，切换单体
            if current_price < shadow_8line:
                mp_down['phase'] = 'body'
                self.output(
                    f'【{label}向下测量】超过影线8线({shadow_8line:.2f})，'
                    f'切换单体测量法\n'
                    f'  单体线位: 1线={mp_down["levels"][0]:.2f} | 3线={mp_down["levels"][2]:.2f} | '
                    f'6线={mp_down["levels"][5]:.2f} | 8线={mp_down["levels"][7]:.2f} | '
                    f'9线={mp_down["level9"]:.2f}'
                )
                return True
        return False

    def _get_down_active_levels(self, mp_down):
        """
        获取当前有效的向下测量线位（根据影线/单体阶段）
        返回: (levels, level9, phase_name)
        """
        if mp_down['start'] is None:
            return [], None, 'none'
        
        if mp_down['phase'] == 'shadow' and mp_down['shadow_levels']:
            return mp_down['shadow_levels'], mp_down['shadow_level9'], '\u5f71\u7ebf'
        else:
            return mp_down['levels'], mp_down['level9'], '\u5355\u4f53'

    def _get_price_line_position(self, price, levels, level9, phase_name, label=''):
        """
        返回价格在线位中的精确位置描述字符串
        返回格式示例：“在影线2线(3122)和3线(3109)之间”
        """
        if not levels:
            return ''
        
        # 构建全部线位列表（包括9线）
        all_levels = list(enumerate(levels, 1))  # [(1, price1), (2, price2), ...]
        if level9:
            all_levels.append((9, level9))
        
        # 向下测量：价格越高越靠近起点，即线位编号越小越高
        # 判断价格在哪两条线之间
        # 向下测量：高点 > 1线 > 2线 > ... > 9线
        
        # 先判断是否在某条线的容差范围内（±0.3%）
        for num, lv in all_levels:
            if abs(price - lv) / lv < 0.003:
                line_name = f'{num}线' if num < 9 else '9线(极线)'
                return f'{label}{phase_name}{line_name}({lv:.2f})附近'
        
        # 判断在哪两条线之间
        # 向下测量：价格高于所有线位 = 在高点和1线之间
        if price > all_levels[0][1]:
            return f'{label}在高点和{phase_name}1线({all_levels[0][1]:.2f})之间'
        
        # 价格低于所有线位 = 超过极线
        if price < all_levels[-1][1]:
            return f'{label}已超过{phase_name}9线/极线({all_levels[-1][1]:.2f})，将切换单体测量'
        
        # 在两条线之间
        for i in range(len(all_levels) - 1):
            num_upper, lv_upper = all_levels[i]
            num_lower, lv_lower = all_levels[i + 1]
            if lv_lower <= price <= lv_upper:
                upper_name = f'{num_upper}线' if num_upper < 9 else '9线(极线)'
                lower_name = f'{num_lower}线' if num_lower < 9 else '9线(极线)'
                # 判断更接近哪一条
                if abs(price - lv_upper) < abs(price - lv_lower):
                    closer = f'靠近{upper_name}({lv_upper:.2f})'
                else:
                    closer = f'靠近{lower_name}({lv_lower:.2f})'
                return f'{label}在{phase_name}{upper_name}({lv_upper:.2f})和{lower_name}({lv_lower:.2f})之间，{closer}'
        
        return ''

    def _find_resonance_zones(self, levels_a, level9_a, levels_b, level9_b, tolerance_pct=0.5):
        """
        找到两组线位的共振区域
        共振条件：两组线位中任意两条线的价格差距小于 tolerance_pct%
        
        返回: [(price_a, line_num_a, price_b, line_num_b, avg_price), ...]
        按平均价格从高到低排序（向下测量，价格越高越先到）
        """
        if not levels_a or not levels_b:
            return []
        
        resonance = []
        all_a = list(enumerate(levels_a, 1))  # [(1, price), (2, price), ...]
        if level9_a:
            all_a.append((9, level9_a))
        all_b = list(enumerate(levels_b, 1))
        if level9_b:
            all_b.append((9, level9_b))
        
        for num_a, price_a in all_a:
            for num_b, price_b in all_b:
                diff_pct = abs(price_a - price_b) / ((price_a + price_b) / 2) * 100
                if diff_pct <= tolerance_pct:
                    avg_price = (price_a + price_b) / 2
                    resonance.append((price_a, num_a, price_b, num_b, avg_price))
        
        # 按平均价格从高到低排序
        resonance.sort(key=lambda x: x[4], reverse=True)
        return resonance

    def find_swing_point(self, am, lookback, direction):
        """
        在ArrayManager中寻找基准K线，确定A点和B点。
        
        direction=1 (向上测量)：
          找回看内最低点的那根K线
          A点 = 该K线最低价(low)
          B点 = 该K线最高价(high)
          diff = B - A > 0，线位在B点上方延伸
        
        direction=-1 (向下测量)：
          找回看内最高点的那根K线
          A点 = 该K线最高价(high)
          B点 = 该K线最低价(low)
          diff = B - A < 0，线位在B点下方延伸
        
        返回: (start_price, end_price, use_shadow, k_high, k_low)
        """
        if not am.inited or len(am.close) < lookback:
            return None, None, False, None, None

        highs = am.high[-lookback:]
        lows = am.low[-lookback:]

        if direction == 1:
            # 向上测量：找最低点K线
            base_idx = int(lows.argmin())
            k_high = highs[base_idx]
            k_low = lows[base_idx]
            start_price = k_low   # A点 = 最低价
            end_price = k_high    # B点 = 同根K线最高价
        else:
            # 向下测量：找最高点K线
            base_idx = int(highs.argmax())
            k_high = highs[base_idx]
            k_low = lows[base_idx]
            start_price = k_high  # A点 = 最高价
            end_price = k_low     # B点 = 同根K线最低价

        return start_price, end_price, False, k_high, k_low

    def update_measure_point(self, mp, am, lookback, direction, label):
        """
        更新测量点。
        A/B点规则：
          direction=1 (向上)：A点=回看内最低点K线的最低价，B点=同一根K线的最高价
          direction=-1(向下)：A点=回看内最高点K线的最高价，B点=同一根K线的最低价
        公式：Level = A + (B - A) * Ratio（diff正负自动决定方向）
        超八线后重新寻找新的基准K线。
        """
        current_price = am.close[-1]
        need_update = False

        if mp['start'] is None:
            need_update = True
        elif mp['levels'] and not self._is_history:
            # 只有在实盘阶段（历史回放结束）才做超八线检查，防止历史回放中频繁重置
            eight_line = mp['levels'][7]
            # 超过八线（循环结束），重新寻找新基准点
            if direction == 1 and current_price > eight_line:
                need_update = True
                self.output(f'【{label}】超过八线({eight_line:.2f})，重新寻找基准K线')
            elif direction == -1 and current_price < eight_line:
                need_update = True
                self.output(f'【{label}】超过八线({eight_line:.2f})，重新寻找基准K线')

        if need_update:
            start_price, end_price, use_shadow, k_high, k_low = \
                self.find_swing_point(am, lookback, direction)

            if start_price is not None:
                mp['start'] = start_price
                mp['end'] = end_price
                mp['direction'] = direction
                mp['use_shadow'] = use_shadow
                mp['k_high'] = k_high
                mp['k_low'] = k_low
                levels, level9 = self.calculate_levels(start_price, end_price, direction)
                mp['levels'] = levels
                mp['level9'] = level9

                dir_str = '向上' if direction == 1 else '向下'
                if direction == 1:
                    a_label, b_label = '最低价', '最高价'
                else:
                    a_label, b_label = '最高价', '最低价'
                self.output(
                    f'【{label}】更新基准K线: '
                    f'A点({a_label})={start_price:.2f}, B点({b_label})={end_price:.2f}, '
                    f'方向={dir_str}'
                )
                self.output(
                    f'【{label}】线位: '
                    f'一线={levels[0]:.2f}, 三线={levels[2]:.2f}, '
                    f'五线={levels[4]:.2f}, 六线={levels[5]:.2f}, '
                    f'八线={levels[7]:.2f}, 九线(极线)={level9:.2f}'
                )
                # 重置A/B点建立后的极值记录
                if label == '日线':
                    self.highest_since_ab = am.high[-1] if am.count > 0 else start_price
                    self.lowest_since_ab = am.low[-1] if am.count > 0 else start_price

    def get_line_zone(self, mp, current_price):
        """
        【V2核心升级】区域化线位判断
        返回 (zone_desc, zone_num, is_key_zone, is_near_extreme)
        
        zone_desc: 描述字符串，如"2线半"、"3线区域"、"极线区域"
        zone_num: 数字表示（1=1线, 2.5=2线半, 3=3线...）
        is_key_zone: 是否在关键区域（3线/5线/6线/7线/8线/极线）
        is_near_extreme: 是否接近极线（9线）
        
        核心逻辑：
        - 精确在某线位附近（±tolerance_pct%）→ 该线位
        - 在N线和N+1线之间 → N线半（区域）
        - 超过8线但未到9线 → 8线半区域
        - 接近9线（极线）→ 极线区域
        """
        if not mp['levels']:
            return '未初始化', 0, False, False

        levels = mp['levels']
        level9 = mp['level9']
        direction = mp['direction']
        tol = current_price * (self.tolerance_pct / 100.0)

        # 1. 检查是否接近极线（9线）
        if level9 and abs(current_price - level9) <= tol * 1.5:
            return '极线(9线)区域', 9, True, True

        # 2. 检查是否超过极线（行情超出预期）
        if level9:
            if direction == 1 and current_price > level9:
                return '超过极线！需重新测量', 9.5, True, True
            if direction == -1 and current_price < level9:
                return '超过极线！需重新测量', 9.5, True, True

        # 3. 逐线检查（精确匹配）
        for i, level in enumerate(levels):
            if abs(current_price - level) <= tol:
                line_num = i + 1
                is_key = line_num in [1, 3, 5, 6, 7, 8]
                return f'{line_num}线区域', line_num, is_key, False

        # 4. 区间判断（区域化核心：N线和N+1线之间 = N线半）
        if direction == 1:
            if current_price < levels[0]:
                return '1线以下', 0, False, False
            for i in range(len(levels) - 1):
                if levels[i] < current_price < levels[i + 1]:
                    n = i + 1
                    # 2线半区域（大于2线小于3线）
                    zone_name = f'{n}线半区域'
                    # 2线半和5线半是常见关键区域
                    is_key = n in [2, 4, 6]  # 2线半、4线半（接近5线）、6线半（接近7线）
                    return zone_name, n + 0.5, is_key, False
            # 超过8线但未到9线
            if current_price > levels[7]:
                return '8线半区域（接近极线）', 8.5, True, True
        else:
            if current_price > levels[0]:
                return '1线以上', 0, False, False
            for i in range(len(levels) - 1):
                if levels[i + 1] < current_price < levels[i]:
                    n = i + 1
                    zone_name = f'{n}线半区域'
                    is_key = n in [2, 4, 6]
                    return zone_name, n + 0.5, is_key, False
            if current_price < levels[7]:
                return '8线半区域（接近极线）', 8.5, True, True

        return '未知区域', 0, False, False

    def is_near_key_line(self, mp, current_price):
        """
        判断当前价格是否在关键线位附近（兼容旧接口）
        关键线位：3线、5线、6线、7线、8线
        返回 (是否在关键线位, 最近的线号)
        """
        zone_desc, zone_num, is_key, is_extreme = self.get_line_zone(mp, current_price)
        return is_key, zone_num

    def check_pattern(self, am, direction):
        """
        检查形态是否成立
        做空形态：右边新K线的低点 < 左边高点K线的低点
        做多形态：右边新K线的高点 > 左边低点K线的高点
        """
        if not am.inited or len(am.high) < 3:
            return False

        highs = am.high
        lows = am.low

        if direction == -1:
            left_idx = -2 if highs[-2] > highs[-3] else -3
            left_high = highs[left_idx]
            left_low = lows[left_idx]
            right_low = lows[-1]
            right_high = highs[-1]
            return right_low < left_low and right_high <= left_high * 1.001
        else:
            left_idx = -2 if lows[-2] < lows[-3] else -3
            left_low = lows[left_idx]
            left_high = highs[left_idx]
            right_high = highs[-1]
            right_low = lows[-1]
            return right_high > left_high and right_low >= left_low * 0.999

    def _check_pattern_realtime(self, direction, realtime_price):
        """
        实时形态检测：用当前 tick 的实时极值 + 历史K线数组判断形态是否成立。
        解决 check_pattern 只能检测已收盘K线、无法感知盘中实时突破的问题。

        direction == -1（向下形态/回调确认）：
            左边K线 = am_daily 中最近的最高点K线
            右边虚拟K线最低价 = self.lowest_since_high（从高点以来实时最低价）
            成立条件：实时最低价 < 左边高点K线的最低价

        direction == 1（向上形态/反弹确认）：
            左边K线 = am_daily 中最近的最低点K线
            右边虚拟K线最高价 = self.highest_since_low（从低点以来实时最高价）
            成立条件：实时最高价 > 左边低点K线的最高价
        """
        am = self.am_daily
        if not am.inited or am.count < 3:
            return False

        highs = am.high
        lows = am.low

        if direction == -1:
            # 找左边高点K线：最近两根里最高的那根
            left_idx = -1 if highs[-1] > highs[-2] else -2
            left_low = lows[left_idx]
            # 右边虚拟K线的最低价：从高点以来的实时最低价
            realtime_low = self.lowest_since_high if self.lowest_since_high < 999999.0 else realtime_price
            # 也要包含当前 tick 价格（防止 lowest_since_high 未及时更新）
            realtime_low = min(realtime_low, realtime_price)
            # 形态成立：实时最低价跌破了左边高点K线的最低价
            return realtime_low < left_low
        else:
            # 找左边低点K线：应该是回调最低点那根K线（即 am_daily 中最近的最低价最小的那根）
            # 而不是“最近两根里最低的那根”，那样容易找错K线
            # 在最近 lookback 根K线里找最低价最小的那根，作为左边基准K线
            lookback = min(10, am.count - 1)
            recent_lows = lows[-lookback:]
            min_low_offset = int(recent_lows.argmin())  # 在 recent_lows 中的相对位置
            left_idx = -(lookback - min_low_offset)  # 转换为负索引
            left_high = highs[left_idx]
            left_low_val = lows[left_idx]
            # 右边虚拟K线的最高价：从最低点以来的实时最高价
            # 但必须是在左边K线之后发生的，所以只用 highest_since_low
            realtime_high = self.highest_since_low if self.highest_since_low > 0 else realtime_price
            # 也要包含当前 tick 价格
            realtime_high = max(realtime_high, realtime_price)
            # 形态成立：实时最高价必须明确超过左边低点K线的最高价
            # 同时要排除“左边K线就是当前最新K线”的情况（左边 == 右边）
            if left_idx == -1:
                # 左边就是最新已收盘K线，右边虚拟K线就是盘中这根
                # 需要实时价格超过左边最高价，且实时价格不能就是左边价格本身
                return realtime_high > left_high and realtime_high > left_low_val
            return realtime_high > left_high

    def calc_kdj(self, am, n=9, m1=3, m2=3):
        """计算KDJ指标"""
        if not am.inited or len(am.high) < n:
            return 50.0, 50.0, 50.0

        highs = am.high[-n:]
        lows = am.low[-n:]
        close = am.close[-1]

        highest = max(highs)
        lowest = min(lows)

        if highest == lowest:
            rsv = 50.0
        else:
            rsv = (close - lowest) / (highest - lowest) * 100

        k = (2 / 3) * self.kdj_k + (1 / 3) * rsv
        d = (2 / 3) * self.kdj_d + (1 / 3) * k
        j = 3 * k - 2 * d

        return k, d, j

    def detect_sideways(self):
        """
        检测是否处于横盘震荡行情
        判断依据：
        1. 日线最近5根K线实体较小（收盘价变化幅度 < 1%）
        2. 60分钟高低点在一定范围内震荡
        返回 (is_sideways, range_high, range_low)
        """
        if not self.am_daily.inited or len(self.am_daily.close) < 5:
            return False, 0, 0
        if not self.am_h1.inited or len(self.am_h1.close) < 20:
            return False, 0, 0

        # 日线近5根收盘价变化幅度
        daily_closes = self.am_daily.close[-5:]
        daily_range_pct = (max(daily_closes) - min(daily_closes)) / min(daily_closes) * 100

        # 60分钟近20根高低点范围
        h1_highs = self.am_h1.high[-20:]
        h1_lows = self.am_h1.low[-20:]
        range_high = float(max(h1_highs))
        range_low = float(min(h1_lows))
        h1_range_pct = (range_high - range_low) / range_low * 100

        # 日线近5根收盘价全部在同一方向（均上涨或均下跌）则不是横盘
        daily_up = all(daily_closes[i] <= daily_closes[i+1] for i in range(len(daily_closes)-1))
        daily_dn = all(daily_closes[i] >= daily_closes[i+1] for i in range(len(daily_closes)-1))
        if daily_up or daily_dn:
            return False, range_high, range_low

        # 日线变化小于0.8%，60分钟波动在1.5%以内 → 横盘（提高门槛，减少误判）
        is_sideways = daily_range_pct < 0.8 and h1_range_pct < 1.5

        return is_sideways, range_high, range_low

    # ============================================================
    # 策略回调函数
    # ============================================================

    def onInit(self):
        """策略初始化（加载时调用，用于UI组件初始化）"""
        super().onInit()
        
        # ── K线图可视化：启动图表窗口 ──────────────────────────────
        # 必须在 onInit 中调用 getGui()，这是官方示例的标准做法
        if _KLWIDGET_AVAILABLE and KLWidget is not None:
            try:
                self.getGui()
                self.output('K线图表窗口已启动，将实时显示波神1-8线')
            except Exception as e:
                self.output(f'K线图表启动失败（不影响策略运行）: {e}')

    def onStart(self):
        """策略启动"""
        # 如果 vtSymbol 为空，自动从实例名称推断合约代码
        if not self.vtSymbol:
            # 实例名称通常就是合约代码，如 rb2605
            instance_name = self.name if hasattr(self, 'name') and self.name else ''
            if not instance_name:
                # 尝试从 ctaEngine 获取实例名
                try:
                    instance_name = self.ctaEngine.strategyDict.get(self.__class__.__name__, {}).get('name', '')
                except Exception:
                    instance_name = ''
            if instance_name:
                self.vtSymbol = instance_name
                self.output(f'vtSymbol 为空，自动使用实例名称: {instance_name}')

        # 如果 exchange 为空，根据合约前缀自动推断
        if not self.exchange and self.vtSymbol:
            prefix = self.vtSymbol[:2].upper()
            exchange_map = {
                'RB': 'SHFE', 'HC': 'SHFE', 'CU': 'SHFE', 'AL': 'SHFE',
                'ZN': 'SHFE', 'NI': 'SHFE', 'SN': 'SHFE', 'PB': 'SHFE',
                'AU': 'SHFE', 'AG': 'SHFE', 'FU': 'SHFE', 'BU': 'SHFE',
                'RU': 'SHFE', 'SP': 'SHFE', 'SS': 'SHFE', 'BC': 'SHFE',
                'I_': 'DCE',  'J_': 'DCE',  'JM': 'DCE',  'A_': 'DCE',
                'B_': 'DCE',  'M_': 'DCE',  'Y_': 'DCE',  'P_': 'DCE',
                'C_': 'DCE',  'CS': 'DCE',  'L_': 'DCE',  'V_': 'DCE',
                'PP': 'DCE',  'EG': 'DCE',  'EB': 'DCE',  'PG': 'DCE',
                'LH': 'DCE',  'JD': 'DCE',  'FB': 'DCE',  'BB': 'DCE',
                'CF': 'CZCE', 'SR': 'CZCE', 'TA': 'CZCE', 'OI': 'CZCE',
                'MA': 'CZCE', 'FG': 'CZCE', 'RM': 'CZCE', 'ZC': 'CZCE',
                'WH': 'CZCE', 'PM': 'CZCE', 'RS': 'CZCE', 'RI': 'CZCE',
                'SF': 'CZCE', 'SM': 'CZCE', 'CY': 'CZCE', 'AP': 'CZCE',
                'CJ': 'CZCE', 'UR': 'CZCE', 'SA': 'CZCE', 'PF': 'CZCE',
                'IF': 'CFFEX','IC': 'CFFEX','IH': 'CFFEX','IM': 'CFFEX',
                'TF': 'CFFEX','T_': 'CFFEX','TS': 'CFFEX','SC': 'INE',
                'NR': 'INE',  'LU': 'INE',
            }
            # 先尝试2字符前缀，再尝试首字母+下划线
            self.exchange = exchange_map.get(prefix, '')
            if not self.exchange:
                self.exchange = exchange_map.get(self.vtSymbol[0].upper() + '_', 'SHFE')
            self.output(f'exchange 为空，根据合约前缀自动推断: {self.exchange}')

        self.output('波神凯线策略启动...')
        self.output(f'合约: {self.vtSymbol}，交易所: {self.exchange}')

        # 必须先调用 super().onStart()，让平台完成订阅和 symbolList 初始化
        # 然后再手动同步 vtSymbol/exchange，确保 loadBar 能拿到正确的合约
        super().onStart()

        # super().onStart() 会用 self.vtSymbol 重置 symbolList
        # 如果此时 vtSymbol 已经被我们设置好了，symbolList 就是正确的
        self.output(f'订阅合约列表: {self.symbolList}')

        # 加载历史K线
        try:
            self.output(f'开始加载历史1分钟K线（{self.vtSymbol}，{self.exchange}）...')
            self.loadBar(20)
        except Exception as e:
            self.output(f'历史1分钟K线加载失败: {e}，将使用实盘数据初始化')

        try:
            self.output(f'开始加载历史日线...')
            self.loadDay(1, func=self.onDay)  # 加载1年日线（约250根，取最近60根）
        except Exception as e:
            self.output(f'历史日线加载失败: {e}')

        # 尝试加载周线数据（如果平台支持）
        try:
            self.output(f'开始加载历史周线...')
            # 无限易平台通常支持 loadWeek，如果不支持，我们在 onDay 里合成
            if hasattr(self, 'loadWeek'):
                self.loadWeek(60, func=self.onWeek)
            else:
                self.output('平台不支持直接加载周线，将通过日线合成（暂未实现）')
        except Exception as e:
            self.output(f'历史周线加载失败: {e}')
        # 在所有历史日线加载完成后，统一做一次日线 A/B点初始化
        if self.am_daily.count > 0:
            self.output('开始初始化日线 A/B点...')
            # 构造一个虚拟的bar来触发初始化
            dummy_bar = KLineData()
            dummy_bar.close = self.am_daily.close[-1]
            self._check_daily_signal(dummy_bar)
            
            # 初始化highest_since_ab/lowest_since_ab
            # 找到A点在am_daily中的位置，从该位置开始计算极值
            if self.mp_daily['start'] is not None:
                a_price = self.mp_daily['start']
                trend = self.mp_daily['direction']
                # 在am_daily中找到A点所在的K线索引
                a_idx = 0
                for i in range(self.am_daily.count):
                    if trend == 1 and abs(self.am_daily.low[i] - a_price) < 1.0:
                        a_idx = i
                        break
                    elif trend == -1 and abs(self.am_daily.high[i] - a_price) < 1.0:
                        a_idx = i
                        break
                # 从找到的A点位置开始计算极值
                if a_idx < self.am_daily.count:
                    self.highest_since_ab = float(max(self.am_daily.high[a_idx:]))
                    self.lowest_since_ab = float(min(self.am_daily.low[a_idx:]))
                    self.output(f'初始化极値: A点索引={a_idx}, 最高价={self.highest_since_ab:.2f}, 最低价={self.lowest_since_ab:.2f}')
                    # 初始化高点后的最低价（用于判断回调深度）
                    # 找到最高价所在的K线索引，从该点开始计算最低价
                    high_idx = a_idx
                    for j in range(a_idx, self.am_daily.count):
                        if self.am_daily.high[j] >= self.highest_since_ab - 0.5:
                            high_idx = j
                            break
                    self.lowest_since_high = float(min(self.am_daily.low[high_idx:]))
                    # 初始化低点后的最高价（用于判断反弹高度）
                    # 必须从“高点后的最低点”开始算，而不是从 A 点开始算
                    # 否则会把高点本身的价格也算进去，导致 highest_since_low 过高
                    pullback_low_val = self.lowest_since_high  # 回调最低点价格
                    pullback_low_idx = high_idx  # 从高点开始搜索最低点
                    for j in range(high_idx, self.am_daily.count):
                        if self.am_daily.low[j] <= pullback_low_val + 0.5:
                            pullback_low_idx = j
                            break
                    # highest_since_low 只记录“回调最低点 K 线之后”的最高价
                    after_low_highs = self.am_daily.high[pullback_low_idx + 1:]
                    self.highest_since_low = float(max(after_low_highs)) if len(after_low_highs) > 0 else 0.0
                    self.output(f'初始化回调深度: 高点后最低={self.lowest_since_high:.2f}, 回调低点后最高={self.highest_since_low:.2f}')

        # 初始化向下测量（日线 + 60分钟）
        # 日线：从 A点之后的最高点向下测量（避免选到历史旧高点）
        if self.am_daily.count >= 5:
            self.output('开始初始化日线向下测量（回调目标分析）...')
            # 使用a_idx作为搜索起始位置，确保只在A点之后搜索最高点
            daily_start_idx = a_idx if 'a_idx' in dir() else None
            self._init_down_measurement(self.mp_daily_down, self.am_daily, '日线', start_idx=daily_start_idx)
        # 60分钟：从 am_h1 中对应A点时间之后的最高点向下测量
        if self.am_h1.inited and self.am_h1.count >= 5:
            self.output('开始初始化60分钟向下测量...')
            # 60分钟也从 A点之后搜索最高点：找到am_h1中最接近A点价格的位置
            h1_start_idx = 0
            if self.mp_daily['start'] is not None:
                a_price = self.mp_daily['start']
                trend = self.mp_daily['direction']
                h1_max_idx = min(self.am_h1.count, self.am_h1.size) - 1
                for i in range(h1_max_idx):
                    if trend == 1 and abs(self.am_h1.low[i] - a_price) < 5.0:
                        h1_start_idx = i
                        break
                    elif trend == -1 and abs(self.am_h1.high[i] - a_price) < 5.0:
                        h1_start_idx = i
                        break
            self._init_down_measurement(self.mp_h1_down, self.am_h1, '60分钟', start_idx=h1_start_idx)

        self._is_history = False  # 历史回放结束，切换到实盘模式
        self.output('策略初始化完成，开始监控行情...（等待第一个tick输出实时价格分析）')

        # ── 将历史日线K线批量推送到图表 ──────────────────────────
        # 图表窗口已经在 onInit 中启动，现在把历史K线数据推进去
        if self.widget is not None and self.am_daily.inited:
            try:
                # 遍历 am_daily 中的所有历史日线，构造 bar 对象并推送
                for i in range(self.am_daily.count):
                    # 构造一个简单的 K 线对象（仅包含图表所需的字段）
                    class SimpleBar:
                        def __init__(self, dt, o, h, l, c):
                            self.datetime = dt
                            self.open = o
                            self.high = h
                            self.low = l
                            self.close = c
                    
                    bar = SimpleBar(
                        dt=self.am_daily.datetime[i],
                        o=self.am_daily.open[i],
                        h=self.am_daily.high[i],
                        l=self.am_daily.low[i],
                        c=self.am_daily.close[i]
                    )
                    self._push_daily_kline_to_widget(bar)
                
                self.output(f'已将 {self.am_daily.count} 根历史日线推送到图表窗口')
            except Exception as e:
                self.output(f'历史K线推送失败: {e}')

    def onTick(self, tick):
        """收到行情Tick"""
        super().onTick(tick)
        if tick.lastPrice == 0 or tick.askPrice1 == 0 or tick.bidPrice1 == 0:
            return
        self.tick = tick
        # 第一个有效tick到来时，输出初始分析（使用实时价格）
        if not self._initial_summary_done:
            self._initial_summary_done = True
            self._output_daily_summary(tick.lastPrice)
        # 实时更新A/B点建立后的极値，一旦创新高点就重新初始化向下测量
        if not self._is_history and self.mp_daily['start'] is not None:
            price = tick.lastPrice
            trend = self.mp_daily['direction']
            if trend == 1:  # 上涨趋势，监控高点突破
                # 实时更新高点后的最低价（用于判断回调深度）
                if price < self.lowest_since_high:
                    self.lowest_since_high = price
                    # 回调创新低时，重置 highest_since_low，让它重新从当前价格开始计时
                    # 确保它只记录“回调最低点之后”的最高价，而不是历史高点
                    self.highest_since_low = price
                if price > self.highest_since_ab + 0.5:  # 容差0.5点避免噪声
                    old_high = self.highest_since_ab
                    self.highest_since_ab = price
                    self.lowest_since_high = price  # 高点更新，重置高点后最低价
                    self.output(
                        f'实时高点更新: {old_high:.2f} → {price:.2f}，'
                        f'重新初始化向下测量...'
                    )
                    # 重置向下测量数据结构
                    self.mp_daily_down = self._new_mp_down()
                    self.mp_h1_down = self._new_mp_down()
                    # 重新初始化日线向下测量（从新高点开始）
                    a_idx = 0
                    a_price = self.mp_daily['start']
                    for i in range(self.am_daily.count):
                        if abs(self.am_daily.low[i] - a_price) < 1.0:
                            a_idx = i
                            break
                    self._init_down_measurement(self.mp_daily_down, self.am_daily, '日线', start_idx=a_idx)
                    # 重新初始化60分钟向下测量
                    h1_start_idx = 0
                    h1_max_idx = min(self.am_h1.count, self.am_h1.size - 1)
                    for i in range(h1_max_idx):
                        if abs(self.am_h1.low[i] - a_price) < 5.0:
                            h1_start_idx = i
                            break
                    self._init_down_measurement(self.mp_h1_down, self.am_h1, '60分钟', start_idx=h1_start_idx)
                    # 重新输出日线分析
                    self._output_daily_summary(price)
            elif trend == -1:  # 下跌趋势，监控低点突破
                # 实时更新低点后的最高价（用于判断反弹高度）
                if price > self.highest_since_low:
                    self.highest_since_low = price
                if price < self.lowest_since_ab - 0.5:
                    old_low = self.lowest_since_ab
                    self.lowest_since_ab = price
                    self.output(
                        f'实时低点更新: {old_low:.2f} → {price:.2f}，'
                        f'重新初始化向下测量...'
                    )
                    self.mp_daily_down = self._new_mp_down()
                    self.mp_h1_down = self._new_mp_down()
                    self._init_down_measurement(self.mp_daily_down, self.am_daily, '日线')
                    self._init_down_measurement(self.mp_h1_down, self.am_h1, '60分钟')
                    self._output_daily_summary(price)
        self.bm.updateTick(tick)

    def onBar(self, bar):
        """收到1分钟K线，转发给各合成器"""
        self._debug_bar_count += 1
        import datetime as _dt
        # 输出第1根K线的调试信息
        if self._debug_bar_count == 1:
            self.output(f'调试-第1根bar: datetime类型={type(bar.datetime).__name__}, 值={bar.datetime}, open={bar.open}, close={bar.close}')
        if self._debug_bar_count == 10:
            self.output(f'调试-第10根bar: datetime类型={type(bar.datetime).__name__}, 值={bar.datetime}')
        # 检查前20根K线的分钟数整除情况
        if self._debug_bar_count <= 20:
            import datetime as _dt2
            dt = bar.datetime
            if isinstance(dt, str):
                try: dt = _dt2.datetime.strptime(dt, "%Y-%m-%d %H:%M:%S")
                except: pass
            if hasattr(dt, 'hour'):
                mins = 60 * dt.hour + dt.minute
                self.output(f'调试-bar#{self._debug_bar_count}: {dt.strftime("%H:%M")}, mins={mins}, %5={mins%5}, %15={mins%15}, %60={mins%60}')
        # 确保 datetime 字段是 datetime 对象（历史数据可能是字符串）
        if isinstance(getattr(bar, 'datetime', None), str):
            try:
                bar.datetime = _dt.datetime.strptime(bar.datetime, "%Y-%m-%d %H:%M:%S")
            except Exception:
                try:
                    bar.datetime = _dt.datetime.strptime(bar.datetime, "%Y%m%d %H:%M:%S")
                except Exception:
                    pass
        self.bm_h1.updateBar(bar)
        self.bm_m15.updateBar(bar)
        self.bm_m5.updateBar(bar)

    def onBarM5_raw(self, bar):
        self._debug_m5_count += 1

    def onBarM5(self, bar):
        """收到5分钟K线"""
        if not self.am_m5.updateBar(bar):
            return
        self._check_m5_signal(bar)

    def onBarM15_raw(self, bar):
        pass

    def onBarM15(self, bar):
        """收到15分钟K线（桥梁层）"""
        if not self.am_m15.updateBar(bar):
            return
        if self.enable_m15:
            self._check_m15_signal(bar)

    def onBarH1_raw(self, bar):
        self._debug_h1_count += 1

    def onBarH1(self, bar):
        """收刀060分钟K线 (60分钟收刀)"""
        if not self.am_h1.updateBar(bar):
            return
        
        self._check_h1_signal(bar)

    def onDay(self, bar):
        """收到日线K线"""
        if not self.am_daily.updateBar(bar):
            return
        # 调试：打印日线数据的起止时间（只打印第1根和最后1根）
        if self.am_daily.count == 1:
            self.output(f'调试-日线第1根: {bar.datetime}, open={bar.open}, high={bar.high}, low={bar.low}, close={bar.close}')
        if self.am_daily.count == self.am_daily.size:
            self.output(f'调试-日线第{self.am_daily.size}根(加载完成): {bar.datetime}, open={bar.open}, high={bar.high}, low={bar.low}, close={bar.close}')
        
        # 实盘阶段：更新A/B点建立后的极值
        if not self._is_history and self.mp_daily['start'] is not None:
            if bar.high > self.highest_since_ab:
                self.highest_since_ab = bar.high
            if bar.low < self.lowest_since_ab:
                self.lowest_since_ab = bar.low
        
        # 只有在实盘阶段，才每根日线检查信号（历史回放阶段在onStart里统一初始化）
        if not self._is_history:
            self._check_daily_signal(bar)

        # ── K线图可视化：将日线 + 波神测量线位推送到图表 ─────────────
        # 无论历史回放还是实盘，只要图表存在，就将当前日线和线位推送进去
        if self.widget is not None and self.mp_daily['levels']:
            self._push_daily_kline_to_widget(bar)

    def onWeek(self, bar):
        """收到周线K线"""
        self.am_week.updateBar(bar)

    # ============================================================
    # K线图可视化辅助函数
    # ============================================================

    def _push_daily_kline_to_widget(self, bar):
        """
        将日线 K 线 + 波神测量线位推送到 KLWidget 图表。
        主图显示：向上测量 1~8 线、极线（line9）、向下测量关键线位。
        如果线位数据不存在，对应字段传 None（图表不会画线）。
        """
        try:
            levels = self.mp_daily['levels']     # 列表，共 8 个元素
            level9 = self.mp_daily['level9']     # 极线

            # 安全取值：如果 levels 长度不足 8 个，用 None 补齐
            def safe_level(lst, idx):
                return lst[idx] if lst and len(lst) > idx else None

            # 向下测量线位（回调目标）
            d_levels = self.mp_daily_down.get('levels', [])

            payload = {
                'bar':       bar,
                'sig':       0,
                # 向上测量 1~8 线
                'line1':     safe_level(levels, 0),
                'line2':     safe_level(levels, 1),
                'line3':     safe_level(levels, 2),
                'line4':     safe_level(levels, 3),
                'line5':     safe_level(levels, 4),
                'line6':     safe_level(levels, 5),
                'line7':     safe_level(levels, 6),
                'line8':     safe_level(levels, 7),
                'line9':     level9,
                # 向下测量关键线位（1线、中间线、目标线）
                'down_line1': safe_level(d_levels, 0),
                'down_line3': safe_level(d_levels, 2),
                'down_line5': safe_level(d_levels, 4),
            }
            self.widget.recv_kline(payload)
        except Exception as e:
            # 图表推送失败不影响策略主逻辑，静默处理
            pass

    # ============================================================
    # 核心分析逻辑
    # ============================================================

    def _output_daily_summary(self, realtime_price=None):
        """输出日线状态摘要（对交易有实际指导意义）
        realtime_price: 如果传入，优先使用实时价格；否则使用历史日线收盘价
        """
        if not self.mp_daily['levels']:
            return
        levels = self.mp_daily['levels']
        level9 = self.mp_daily['level9']
        start = self.mp_daily['start']
        end = self.mp_daily['end']
        trend = self.mp_daily['direction']
        zone_desc = self.daily_line_desc

        if realtime_price and realtime_price > 0:
            current_price = realtime_price
        elif self.am_daily.count > 0:
            current_price = self.am_daily.close[-1]
        else:
            return

        # 用当前实时价格重新计算线位描述（避免使用可能过时的 daily_line_desc）
        zone_desc, _, _, _ = self.get_line_zone(self.mp_daily, current_price)

        # 测量基准信息
        if trend == 1:
            measure_info = f'A={start:.2f}(最低价) B={end:.2f}(最高价) 向上测量'
        else:
            measure_info = f'A={start:.2f}(最高价) B={end:.2f}(最低价) 向下测量'

        # 全部线位
        all_lines = (
            f'1线={levels[0]:.2f} | 2线={levels[1]:.2f} | 3线={levels[2]:.2f} | '
            f'4线={levels[3]:.2f} | 5线={levels[4]:.2f} | 6线={levels[5]:.2f} | '
            f'7线={levels[6]:.2f} | 8线={levels[7]:.2f} | 9线(极线)={level9:.2f}'
        )

        # 分析历史走势（判断是回调还是上涨途中）
        # 使用实例变量记录的A/B点建立后极值
        recent_high = self.highest_since_ab if self.highest_since_ab > 0 else current_price
        recent_low = self.lowest_since_ab if self.lowest_since_ab < 999999.0 else current_price
        # 确保包含当前价格
        if current_price > recent_high:
            recent_high = current_price
        if current_price < recent_low:
            recent_low = current_price

        # 当前位置和操作建议
        # 容差：允许价格在线位的1%范围内也算曾到达该线位
        tol_pct = 0.01  # 1%容差
        is_pullback = False   # 初始化，避免作用域问题
        is_rebound = False    # 初始化
        waiting_pattern = False   # 新增：是否处于"等待形态确认"状态
        if trend == 1:
            dir_str = '上涨趋势'
            
            # 判断是否处于回调状态
            # 波神铁律：必须先到达关键线位（空间条件），再等待形态成立（确认条件）
            # 空间条件：最高价曾到达3线以上，且当前价格从最高点回落超过1%
            is_pullback = False
            pullback_from = ""
            pullback_from_price = 0.0
            # 回调深度：高点后的最低价（包含当前价格）
            pullback_low = min(self.lowest_since_high, current_price) if self.lowest_since_high < 999999.0 else current_price
            # 空间条件：曾到达3线以上，且高点后最低价低于高点的99%（即曾回落超过1%）
            space_condition_met = recent_high >= levels[2] * (1 - tol_pct) and pullback_low < recent_high * 0.99
            if space_condition_met:
                # 确定从哪条线开始回调
                if recent_high >= levels[7] * (1 - tol_pct):
                    pullback_from = "8线"
                    pullback_from_price = levels[7]
                elif recent_high >= levels[5] * (1 - tol_pct):
                    pullback_from = "6线"
                    pullback_from_price = levels[5]
                elif recent_high >= levels[4] * (1 - tol_pct):
                    pullback_from = "5线"
                    pullback_from_price = levels[4]
                elif recent_high >= levels[2] * (1 - tol_pct):
                    pullback_from = "3线"
                    pullback_from_price = levels[2]
                # 形态确认条件：实时形态检测（支持盘中 tick 实时触发，不需等待K线收盘）
                pattern_confirmed = self._check_pattern_realtime(direction=-1, realtime_price=current_price)
                if pattern_confirmed:
                    is_pullback = True
                else:
                    # 空间条件满足，但形态尚未成立 → 等待形态确认
                    waiting_pattern = True
            
            if is_pullback:
                # 判断当前价格在回调中的位置
                # 取向下测量的B点（影线或单体）作为关键阻力位
                down_b_price = 0.0
                if self.mp_daily_down and self.mp_daily_down.get('k_high'):
                    if self.mp_daily_down.get('use_shadow'):
                        down_b_price = self.mp_daily_down.get('shadow_end', 0.0)
                    else:
                        down_b_price = self.mp_daily_down.get('body_end', 0.0)
                
                # 判断是否反弹到B线附近（当前价格在B点的上下1%范围内）
                near_b_line = down_b_price > 0 and abs(current_price - down_b_price) / down_b_price < 0.01
                above_b_line = down_b_price > 0 and current_price >= down_b_price * 0.99
                
                if above_b_line:
                    # 价格已反弹到B线附近，是逐势做空的机会
                    b_desc = f'B线({down_b_price:.2f})附近' if near_b_line else f'B线({down_b_price:.2f})以上'
                    advice = (
                        f'曾涨至{pullback_from}附近(最高{recent_high:.2f})，回调后反弹至{b_desc}。'
                        f'等待60分钟/15分钟在B线附近出现向下形态，可逐势做空'
                    )
                else:
                    advice = (
                        f'曾涨至{pullback_from}附近(最高{recent_high:.2f})，现回调至{zone_desc}。'
                        f'如回调继续，关注共振支撑区做多；如反弹到B线({down_b_price:.2f})附近，等待60分钟向下形态做空'
                    )
            else:
                if current_price < levels[0]:
                    advice = f'当前在起测区间内，未到达1线，继续观察上涨'
                elif current_price < levels[2]:
                    advice = f'当前在{zone_desc}，上方预期3线={levels[2]:.2f}，如有回调到小时级线位可做多'
                elif current_price < levels[4]:
                    advice = f'当前在{zone_desc}，上方预期5线={levels[4]:.2f}，关注小时级是否出现回调形态'
                elif current_price < levels[5]:
                    advice = f'当前在{zone_desc}，上方预期6线={levels[5]:.2f}，接近关键压力位，小时级寻找出场机会'
                elif current_price < levels[7]:
                    advice = f'当前在{zone_desc}，上方预期8线={levels[7]:.2f}，小时级寻找出场信号'
                elif current_price < level9:
                    advice = f'当前在{zone_desc}，接近极线={level9:.2f}，小时级寻找出场信号，谨防反转'
                else:
                    advice = f'已超过极线={level9:.2f}，循环结束，等待新一轮测量'
        else:
            dir_str = '下跌趋势'
            
            # 判断是否处于反弹状态
            is_rebound = False
            rebound_from = ""
            rebound_from_price = 0.0
            # 反弹高度：低点后的最高价（包含当前价格）
            rebound_high = max(self.highest_since_low, current_price) if self.highest_since_low > 0 else current_price
            # 反弹条件：曾到达3线以下，且低点后最高价高于低点的101%（即曾反弹超过1%）
            space_condition_rebound = recent_low <= levels[2] * (1 + tol_pct) and rebound_high > recent_low * 1.01
            if space_condition_rebound:
                if recent_low <= levels[7] * (1 + tol_pct):
                    rebound_from = "8线"
                    rebound_from_price = levels[7]
                elif recent_low <= levels[5] * (1 + tol_pct):
                    rebound_from = "6线"
                    rebound_from_price = levels[5]
                elif recent_low <= levels[4] * (1 + tol_pct):
                    rebound_from = "5线"
                    rebound_from_price = levels[4]
                elif recent_low <= levels[2] * (1 + tol_pct):
                    rebound_from = "3线"
                    rebound_from_price = levels[2]
                # 形态确认条件：右边新K线的高点突破左边低点K线的高点（波神铁律）
                pattern_confirmed_rebound = self.check_pattern(self.am_daily, 1)
                if pattern_confirmed_rebound:
                    is_rebound = True
                else:
                    # 空间条件满足，但形态尚未成立 → 等待形态确认
                    waiting_pattern = True
                
            if is_rebound:
                # 取向下测量的B点作为关键支撑位
                up_b_price = 0.0
                if self.mp_daily_down and self.mp_daily_down.get('k_high'):
                    if self.mp_daily_down.get('use_shadow'):
                        up_b_price = self.mp_daily_down.get('shadow_end', 0.0)
                    else:
                        up_b_price = self.mp_daily_down.get('body_end', 0.0)
                near_b_line = up_b_price > 0 and abs(current_price - up_b_price) / up_b_price < 0.01
                below_b_line = up_b_price > 0 and current_price <= up_b_price * 1.01
                if below_b_line:
                    b_desc = f'B线({up_b_price:.2f})附近' if near_b_line else f'B线({up_b_price:.2f})以下'
                    advice = (
                        f'曾跌至{rebound_from}附近(最低{recent_low:.2f})，反弹至{b_desc}。'
                        f'等待60分钟/15分钟在B线附近出现向下形态，可逐势做空'
                    )
                else:
                    advice = (
                        f'曾跌至{rebound_from}附近(最低{recent_low:.2f})，现反弹至{zone_desc}。'
                        f'如反弹继续，关注共振阻力区做空；如回落到B线({up_b_price:.2f})附近，等待60分钟向上形态做多'
                    )
            else:
                if current_price > levels[0]:
                    advice = f'当前在起测区间内，未到达1线，继续观察下跌'
                elif current_price > levels[2]:
                    advice = f'当前在{zone_desc}，下方预期3线={levels[2]:.2f}，如有反弹到小时级线位可做空'
                elif current_price > levels[4]:
                    advice = f'当前在{zone_desc}，下方预期5线={levels[4]:.2f}，关注小时级是否出现反弹形态'
                elif current_price > levels[5]:
                    advice = f'当前在{zone_desc}，下方预期6线={levels[5]:.2f}，接近关键支撑位，小时级寻找出场机会'
                elif current_price > levels[7]:
                    advice = f'当前在{zone_desc}，下方预期8线={levels[7]:.2f}，小时级寻找出场信号'
                elif current_price > level9:
                    advice = f'当前在{zone_desc}，接近极线={level9:.2f}，小时级寻找出场信号，谨防反弹'
                else:
                    advice = f'已超过极线={level9:.2f}，循环结束，等待新一轮测量'

        # 收集回调/反弹分析数据
        pullback_data = None
        if (trend == 1 and is_pullback) or (trend == -1 and is_rebound):
            pullback_data = self._get_pullback_analysis_data(current_price)

        # 计算回调结束相关信息（上涨趋势）
        pullback_bottom = 0.0          # 回调最低点价格
        pullback_end_confirmed = False  # 回调是否已结束（形态确认）
        rebound_zone = ''              # 当前反弹到的线位描述
        rebound_pattern_confirmed = False  # 回调结束后，向上形态是否已确认
        rebound_high_price = 0.0           # 反弹高点（大阳线最高点）
        if trend == 1 and is_pullback:
            # 回调最低点：取 lowest_since_high
            pullback_bottom = self.lowest_since_high if self.lowest_since_high < 999999.0 else 0.0
            # 判断回调是否已结束：当前价格从最低点反弹超过 0.3%，即认为回调阶段性结束
            if pullback_bottom > 0 and current_price > pullback_bottom * 1.003:
                pullback_end_confirmed = True
                # 计算当前反弹到了哪个线位
                rebound_zone, _, _, _ = self.get_line_zone(self.mp_daily, current_price)
                # 反弹高点：回调结束后的最高价（highest_since_low 记录了从最低点以来的最高价）
                rebound_high_price = self.highest_since_low if self.highest_since_low > 0 else current_price
                # 判断向上形态是否成立：当前价格已明显高于反弹高点，且形态确认
                # 即：右边新K线的最高点 > 左边大阳线的最高点
                rebound_pattern_confirmed = self._check_pattern_realtime(direction=1, realtime_price=current_price)

        # 统一输出精简版操作指令
        self._print_action_summary(
            current_price=current_price,
            trend=trend,
            dir_str=dir_str,
            advice=advice,
            pullback_data=pullback_data,
            is_pullback=is_pullback,
            is_rebound=is_rebound,
            waiting_pattern=waiting_pattern,
            recent_high=recent_high,
            recent_low=recent_low,
            pullback_from=pullback_from if trend == 1 else (rebound_from if 'rebound_from' in dir() else ''),
            pullback_bottom=pullback_bottom,
            pullback_end_confirmed=pullback_end_confirmed,
            rebound_zone=rebound_zone,
            rebound_pattern_confirmed=rebound_pattern_confirmed,
            rebound_high_price=rebound_high_price
        )

    def _get_pullback_analysis_data(self, current_price):
        """
        获取回调/反弹分析数据：计算日线影线+单体60分钟影线+单体共四组线位，找重合区
        """
        data = {
            'resonance_zones': [],
            'closest_zone': None,
            'closest_diff': 9999
        }

        mp = self.mp_daily_down
        mp_h1 = self.mp_h1_down

        # 收集四组线位：日线影线、日线单体、60分钟影线、60分钟单体
        groups = []

        # 日线向下测量
        if mp['start'] is not None:
            d_high = mp['k_high']
                
            # 影线测量组
            if mp['shadow_levels']:
                sl = mp['shadow_levels']
                sl9 = mp['shadow_level9']
                groups.append(('日线影线', sl, sl9))
                # 检查是否切换单体
                self._check_down_phase_switch(mp, current_price, '日线')
            
            # 单体测量组（始终展示）
            if mp['levels']:
                bl = mp['levels']
                bl9 = mp['level9']
                groups.append(('日线单体', bl, bl9))
        else:
            pass
    
        # 60分钟向下测量（当前阶段仅验证日线，暂时屏蔽60分钟数据）
        # TODO: 日线验证通过后，再开放60分钟共振区分析
        pass
    
        # 多组线位重合区分析
        resonance_zones = []
        if len(groups) >= 2:
                    
            tolerance = 8.0  # 容差8点
            
            # 第一层：日线内部共振（日线影线 + 日线单体重合）
            d_shadow_group = next((g for g in groups if g[0] == '日线影线'), None)
            d_body_group   = next((g for g in groups if g[0] == '日线单体'), None)
            h1_shadow_group = next((g for g in groups if g[0] == '60分钟影线'), None)
            h1_body_group   = next((g for g in groups if g[0] == '60分钟单体'), None)
            
            # 日线内部共振区（影线 + 单体两组重合）
            daily_internal = []
            if d_shadow_group and d_body_group:
                d_sl, d_sl9 = d_shadow_group[1], d_shadow_group[2]
                d_bl, d_bl9 = d_body_group[1], d_body_group[2]
                d_shadow_pts = [(lv, '日线影线', i+1) for i, lv in enumerate(d_sl)]
                if d_sl9: d_shadow_pts.append((d_sl9, '日线影线', 9))
                d_body_pts   = [(lv, '日线单体', i+1) for i, lv in enumerate(d_bl)]
                if d_bl9: d_body_pts.append((d_bl9, '日线单体', 9))
                
                used_s = set()
                used_b = set()
                for si, (sp, sl, sn) in enumerate(d_shadow_pts):
                    if si in used_s:
                        continue
                    for bi, (bp, bl, bn) in enumerate(d_body_pts):
                        if bi in used_b:
                            continue
                        if abs(sp - bp) <= tolerance:
                            avg_p = (sp + bp) / 2
                            daily_internal.append((avg_p, [(sp, sl, sn), (bp, bl, bn)], 2))
                            used_s.add(si)
                            used_b.add(bi)
                            break
            
            # 第二层：日线内部共振区 + 60分钟匹配
            h1_groups = []
            if h1_shadow_group:
                h1_groups.append(h1_shadow_group)
            if h1_body_group:
                h1_groups.append(h1_body_group)
            
            # 对日线内部共振区，尝试匹配60分钟线位
            for avg_p, matched, cnt in daily_internal:
                best_h1_match = None
                best_diff = tolerance
                for h1_label, h1_levels, h1_level9 in h1_groups:
                    h1_pts = [(lv, h1_label, i+1) for i, lv in enumerate(h1_levels)]
                    if h1_level9: h1_pts.append((h1_level9, h1_label, 9))
                    for hp, hl, hn in h1_pts:
                        diff = abs(hp - avg_p)
                        if diff <= tolerance and diff < best_diff:
                            best_diff = diff
                            best_h1_match = (hp, hl, hn)
                
                if best_h1_match:
                    all_matched = matched + [best_h1_match]
                    new_avg = sum(m[0] for m in all_matched) / len(all_matched)
                    resonance_zones.append((new_avg, all_matched, 3))
                else:
                    resonance_zones.append((avg_p, matched, 2))
            
            # 如果日线只有一组（影线或单体），直接与60分钟匹配
            if not daily_internal:
                single_daily = d_shadow_group or d_body_group
                if single_daily and h1_groups:
                    d_pts = [(lv, single_daily[0], i+1) for i, lv in enumerate(single_daily[1])]
                    if single_daily[2]: d_pts.append((single_daily[2], single_daily[0], 9))
                    used_d = set()
                    for di, (dp, dl, dn) in enumerate(d_pts):
                        if di in used_d: continue
                        for h1_label, h1_levels, h1_level9 in h1_groups:
                            h1_pts = [(lv, h1_label, i+1) for i, lv in enumerate(h1_levels)]
                            if h1_level9: h1_pts.append((h1_level9, h1_label, 9))
                            for hp, hl, hn in h1_pts:
                                if abs(dp - hp) <= tolerance:
                                    avg_p = (dp + hp) / 2
                                    resonance_zones.append((avg_p, [(dp, dl, dn), (hp, hl, hn)], 2))
                                    used_d.add(di)
                                    break
            
            # 按共振组数（多的先）和价格（高的先）排序
            resonance_zones.sort(key=lambda x: (-x[2], -x[0]))
            
            if resonance_zones:
                for idx, (avg_p, matched, cnt) in enumerate(resonance_zones[:10]):
                    star = '*' * cnt
                    match_desc = ' + '.join(f'{l}{n}线' for p, l, n in matched)
                    data['resonance_zones'].append({
                        'price': avg_p,
                        'desc': f'{star} {avg_p:.0f}区 ({match_desc})',
                        'stars': cnt
                    })
                    
        # 下方最近共振区提示
        if resonance_zones if 'resonance_zones' in dir() else False:
            below = [(avg_p, matched, cnt) for avg_p, matched, cnt in resonance_zones if avg_p < current_price - 5]
            if below:
                avg_p, matched, cnt = below[0]
                diff = current_price - avg_p
                star = '*' * cnt
                match_desc = ' + '.join(f'{l}{n}线' for p, l, n in matched)
                data['closest_zone'] = f'{star} {avg_p:.0f}区 ({match_desc})'
                data['closest_diff'] = diff
                
        return data


    def _print_action_summary(self, current_price, trend, dir_str, advice, pullback_data, is_pullback, is_rebound,
                               waiting_pattern=False, recent_high=0.0, recent_low=0.0, pullback_from='',
                               pullback_bottom=0.0, pullback_end_confirmed=False, rebound_zone='',
                               rebound_pattern_confirmed=False, rebound_high_price=0.0):
        """输出用户要求的叙述式日线提示
        
        五种状态：
        1a. 回调已结束，且向上形态已确认：完整叙述+上涨目标位
        1b. 回调已结束，但向上形态尚未成立：叙述+等待向上形态确认
        1c. 回调形态已确认，但回调尚未结束：输出回调测量结论
        2.  等待形态：空间条件已满足但形态未成立，不输出目标位
        3.  顺势运行：输出当前线位+下一个目标线位
        """
        lines = []
        lines.append("--------------------------------")
        
        # 提取日线 A点和 B点
        start = self.mp_daily.get('start', 0.0)
        end = self.mp_daily.get('end', 0.0)
        mp_levels = self.mp_daily.get('levels', [])
        mp_level9 = self.mp_daily.get('level9', 0.0)
        
        # 基础信息
        lines.append(f"日线大方向：{dir_str}")
        lines.append(f"日线测量基准：A点是 {start:.2f}，B点是 {end:.2f}")
        
        # ============================================================
        # 状态 1a：回调已结束，当前正在反弹 —— 完整叙述行情故事
        # ============================================================
        if (is_pullback or is_rebound) and pullback_end_confirmed and pullback_bottom > 0:
            action_type = "回调" if is_pullback else "反弹"
            
            # 本波段高点
            if is_pullback and recent_high > 0:
                lines.append(f"本波段最高走到了 {pullback_from}区域，最高点位 {recent_high:.2f}")
            elif is_rebound and recent_low > 0:
                lines.append(f"本波段最低走到了 {pullback_from}区域，最低点位 {recent_low:.2f}")
            
            # 回调过程 + 回调目标位
            lines.append(f"然后从{pullback_from}开始向下{action_type}，{action_type}形态已得到确认。")
            # 输出回调测量结论（大概回调到哪）
            if pullback_data and pullback_data.get('closest_zone'):
                target = pullback_data['closest_zone']
                lines.append(f"大概{action_type}到：{target}。")
            
            # 回调结束位
            pullback_bottom_zone, _, _, _ = self.get_line_zone(self.mp_daily, pullback_bottom)
            lines.append(f"实际{action_type}结束位置：{pullback_bottom_zone}（{pullback_bottom:.2f}）。")
            
            # 当前反弹位置
            cur_zone, _, _, _ = self.get_line_zone(self.mp_daily, current_price)
            lines.append(f"目前已从最低点 {pullback_bottom:.2f} 上涨到了 {cur_zone}，当前点位 {current_price:.2f}。")
            
            # 判断向上形态是否已确认，决定是否输出上涨目标位
            if rebound_pattern_confirmed:
                # 状态 1a：向上形态已确认 —— 输出上涨目标位
                if mp_levels and trend == 1:
                    next_targets = []
                    key_lines = [3, 5, 6, 8]
                    for ln in key_lines:
                        lv = mp_levels[ln - 1]
                        if lv > current_price:
                            next_targets.append((ln, lv))
                    if mp_level9 and mp_level9 > current_price:
                        next_targets.append((9, mp_level9))
                    if next_targets:
                        ln, lv = next_targets[0]
                        lname = '极线(9线)' if ln == 9 else f'{ln}线'
                        lines.append(f"向上形态已确认，未来大概上涨到 {lname} = {lv:.2f} 位置。")
            else:
                # 状态 1b：向上形态尚未成立 —— 等待确认
                if rebound_high_price > 0:
                    rh_zone, _, _, _ = self.get_line_zone(self.mp_daily, rebound_high_price)
                    lines.append(f"目前反弹高点在 {rh_zone}（{rebound_high_price:.2f}）。")
                lines.append("向上形态尚未成立，等待新K线突破反弹高点后才能确认上涨。")
                lines.append("如果有K线跌破最低点，则下跌形态再次成立，需重新向下测量。")
        
        # ============================================================
        # 状态 1b：回调形态已确认，但回调尚未结束 —— 输出回调测量结论
        # ============================================================
        elif is_pullback or is_rebound:
            action_type = "回调" if is_pullback else "反弹"
            
            # 本波段高点
            if is_pullback and recent_high > 0:
                lines.append(f"本波段最高走到了 {pullback_from}区域，最高点位 {recent_high:.2f}")
            elif is_rebound and recent_low > 0:
                lines.append(f"本波段最低走到了 {pullback_from}区域，最低点位 {recent_low:.2f}")
            
            # 回调过程
            lines.append(f"然后从{pullback_from}开始向下{action_type}，{action_type}形态已得到确认。")
            
            # 当前回落位置
            cur_zone, _, _, _ = self.get_line_zone(self.mp_daily, current_price)
            lines.append(f"当前价格回落到了 {cur_zone}，当前点位 {current_price:.2f}")
            
            # 提取向下/向上测量的A点和B点
            down_a = 0.0
            down_b = 0.0
            if self.mp_daily_down and self.mp_daily_down.get('start'):
                down_a = self.mp_daily_down.get('start', 0.0)
                if self.mp_daily_down.get('use_shadow'):
                    down_b = self.mp_daily_down.get('shadow_end', 0.0)
                else:
                    down_b = self.mp_daily_down.get('body_end', 0.0)
            
            if down_a > 0 and down_b > 0:
                lines.append(f"日线{action_type}测量：A点是 {down_a:.2f}，B点是 {down_b:.2f}")
            
            # 结论：最近的共振支撑/阻力区
            if pullback_data and pullback_data.get('closest_zone'):
                target = pullback_data['closest_zone']
                lines.append(f"{action_type}得出的结论是：大概在 {target} 位置。")
            else:
                lines.append(f"{action_type}得出的结论是：等待进一步确认目标位。")
        
        # ============================================================
        # 状态 2：等待形态 —— 空间条件已满足，但形态尚未成立
        # 波神铁律：线位到了，形态不成立，坚决不入场
        # ============================================================
        elif waiting_pattern:
            action_type = "回调" if trend == 1 else "反弹"
            # 用当前实时价格重新计算线位（不用可能过时的 daily_line_desc）
            cur_zone, _, _, _ = self.get_line_zone(self.mp_daily, current_price)
            
            if trend == 1 and recent_high > 0:
                lines.append(f"本波段最高走到了 {pullback_from}区域，最高点位 {recent_high:.2f}")
            elif trend == -1 and recent_low > 0:
                lines.append(f"本波段最低走到了 {pullback_from}区域，最低点位 {recent_low:.2f}")
            
            lines.append(f"当前价格回落到了 {cur_zone}，当前点位 {current_price:.2f}")
            lines.append(f"目前正在{action_type}，但尚未形成向下的 K 线形态，无法确认{action_type}是否正式开始。")
            lines.append("继续等待，形态未成立前不做任何判断。")
        
        # ============================================================
        # 状态 3：顺势运行 —— 输出当前线位 + 下一个目标线位
        # ============================================================
        else:
            # 用当前实时价格重新计算线位
            cur_zone, cur_zone_num, _, _ = self.get_line_zone(self.mp_daily, current_price)
            lines.append(f"当前走到了 {cur_zone}，点位 {current_price:.2f}")
            
            # 输出下一个目标线位
            mp_levels = self.mp_daily.get('levels', [])
            mp_level9 = self.mp_daily.get('level9', 0.0)
            if mp_levels:
                if trend == 1:  # 上涨趋势：找上方最近的关键线位
                    next_targets = []
                    key_lines = [3, 5, 6, 8]  # 主要关注的线位
                    for ln in key_lines:
                        lv = mp_levels[ln - 1]
                        if lv > current_price:
                            next_targets.append((ln, lv))
                    if mp_level9 and mp_level9 > current_price:
                        next_targets.append((9, mp_level9))
                    if next_targets:
                        ln, lv = next_targets[0]
                        lname = '极线(9线)' if ln == 9 else f'{ln}线'
                        lines.append(f"日线大方向向上，上方最近目标线位：{lname} = {lv:.2f}")
                    else:
                        lines.append("已超过所有线位，待重新测量。")
                else:  # 下跌趋势：找下方最近的关键线位
                    next_targets = []
                    key_lines = [3, 5, 6, 8]
                    for ln in key_lines:
                        lv = mp_levels[ln - 1]
                        if lv < current_price:
                            next_targets.append((ln, lv))
                    if mp_level9 and mp_level9 < current_price:
                        next_targets.append((9, mp_level9))
                    if next_targets:
                        ln, lv = next_targets[0]
                        lname = '极线(9线)' if ln == 9 else f'{ln}线'
                        lines.append(f"日线大方向向下，下方最近目标线位：{lname} = {lv:.2f}")
                    else:
                        lines.append("已超过所有线位，徽重新测量。")
            
        lines.append("--------------------------------")
        
        summary_text = "\n".join(lines)
        
        # 防重复输出机制：如果内容和上次一样，就不输出
        if not hasattr(self, '_last_summary_text') or self._last_summary_text != summary_text:
            self.output("\n" + summary_text)
            self._last_summary_text = summary_text

    def _check_daily_signal(self, bar):
        """
        日线分析：历史回放时只更新状态，不输出；实盘时输出简洁结论。

        方向判断规则（波神讲座）：
        - 日线的作用是定大方向，不是每次都重新找高低点
        - 正确做法：找回看周期内最低点K线，向上测量，看当前处于几线
        - 在1-3线：大趋势向上延续，等待60分钟回调入场做多
        - 在6-8线：警惕反转，等待日线形态成立后方向反转
        - 超过八线（循环结束）：重新找最低点K线，继续向上测量
          若新一轮向上测量后价格已在高位，则改为找最高点K线向下测量
        """
        current_price = bar.close

        if len(self.am_daily.close) < 10:
            return

        # 横盘检测（仅实盘时触发输出）
        if self.scalp_mode_enabled:
            is_sideways, rh, rl = self.detect_sideways()
            if is_sideways:
                self.market_mode = '横盘'
                self.h1_range_high = rh
                self.h1_range_low = rl
                self.signal_stage = '横盘模式：5分钟波段'
                if not self._is_history:
                    self.output(f'【日线】横盘震荡，60分钟区间 {rl:.0f}~{rh:.0f}，切换5分钟波段模式')
                self.putEvent()
                return
            else:
                self.market_mode = '趋势'

        # -------------------------------------------------------
        # 核心方向判断：
        # 1. 首次初始化：找回看周期内最低点K线，向上测量
        # 2. 已有测量点：保持方向不变，只有超八线才重新寻找
        # 3. 超八线后重新测量：
        #    - 先尝试向上测量（找最低点K线）
        #    - 若当前价格已在新测量的3线以上，说明价格在高位，
        #      改为找最高点K线向下测量
        # -------------------------------------------------------
        if self.mp_daily['start'] is None:
            # 首次初始化：默认向上测量（找最低点K线）
            trend = 1
            
            # 如果有周线数据，用周线辅助定位大波段起点
            if self.am_week.inited and self.am_week.count > 0:
                # 找到周线最低点
                week_lows = self.am_week.low[-self.am_week.count:]
                min_week_idx = int(week_lows.argmin())
                # 根据周线最低点的位置，缩小日线的查找范围
                # 假设1根周线对应5根日线
                days_from_week_low = (self.am_week.count - 1 - min_week_idx) * 5
                # 在日线中，只在周线最低点附近（前后10天）找最低点
                search_start = max(0, self.am_daily.count - days_from_week_low - 10)
                search_end = min(self.am_daily.count, self.am_daily.count - days_from_week_low + 10)
                
                if search_end > search_start:
                    daily_lows_subset = self.am_daily.low[search_start:search_end]
                    daily_highs_subset = self.am_daily.high[search_start:search_end]
                    local_min_idx = int(daily_lows_subset.argmin())
                    
                    a_price = daily_lows_subset[local_min_idx]
                    b_price = daily_highs_subset[local_min_idx]
                    
                    self.mp_daily['start'] = a_price
                    self.mp_daily['end'] = b_price
                    self.mp_daily['direction'] = 1
                    self.mp_daily['levels'], self.mp_daily['level9'] = self.calculate_levels(a_price, b_price, 1)
                    self.output(f'【日线】通过周线辅助定位基准K线: A点(最低价)={a_price:.2f}, B点(最高价)={b_price:.2f}, 方向=向上')
                    
                    # 已经初始化完成，直接返回
                    if not self.mp_daily['levels']:
                        return
                    zone_desc, zone_num, is_key, is_extreme = self.get_line_zone(self.mp_daily, current_price)
                    self.daily_line_desc = zone_desc
                    return
            else:
                # 平台不支持周线，我们自己用日线合成周线逻辑来找大波段低点
                # 取最近半年（约120根日线）
                lookback = min(self.am_daily.count, 120)
                if lookback > 0:
                    daily_lows = self.am_daily.low[-lookback:]
                    daily_highs = self.am_daily.high[-lookback:]
                    
                    # 找到这半年内的绝对最低点
                    min_idx = int(daily_lows.argmin())
                    
                    # 检查这个最低点是不是太远了（比如在100天前），如果是，说明当前可能是一个新的小波段
                    # 我们需要找"最近这波"的低点。简单做法：找最近40天内的最低点
                    recent_lookback = min(self.am_daily.count, 40)
                    recent_lows = self.am_daily.low[-recent_lookback:]
                    recent_highs = self.am_daily.high[-recent_lookback:]
                    recent_min_idx = int(recent_lows.argmin())
                    
                    # 如果最近40天的最低点比半年最低点高不了多少（比如3%以内），或者半年最低点太远
                    # 我们优先使用最近40天的最低点作为A点（符合"最近这波"的逻辑）
                    a_price = recent_lows[recent_min_idx]
                    b_price = recent_highs[recent_min_idx]
                    
                    self.mp_daily['start'] = a_price
                    self.mp_daily['end'] = b_price
                    self.mp_daily['direction'] = 1
                    self.mp_daily['levels'], self.mp_daily['level9'] = self.calculate_levels(a_price, b_price, 1)
                    self.output(f'【日线】通过近期波段定位基准K线: A点(最低价)={a_price:.2f}, B点(最高价)={b_price:.2f}, 方向=向上')
                    
                    if not self.mp_daily['levels']:
                        return
                    zone_desc, zone_num, is_key, is_extreme = self.get_line_zone(self.mp_daily, current_price)
                    self.daily_line_desc = zone_desc
                    return
        else:
            # 已有测量点，保持当前方向
            trend = self.mp_daily['direction']

            # 检查是否超过八线（循环结束），需要重新判断方向
            if self.mp_daily['levels']:
                eight_line = self.mp_daily['levels'][7]
                cycle_ended = (
                    (trend == 1 and current_price > eight_line) or
                    (trend == -1 and current_price < eight_line)
                )
                if cycle_ended:
                    # 超八线后，先试向上测量，看当前价格在新测量的哪里
                    # 如果当前价格已经在新测量的3线以上，说明价格在高位
                    # 应该改为向下测量
                    highs_tmp = self.am_daily.high[-self.lookback_daily:]
                    lows_tmp = self.am_daily.low[-self.lookback_daily:]
                    low_idx = int(lows_tmp.argmin())
                    high_idx = int(highs_tmp.argmax())
                    a_up = lows_tmp[low_idx]
                    b_up = highs_tmp[low_idx]
                    if b_up > a_up:
                        test_levels_up, _ = self.calculate_levels(a_up, b_up, 1)
                        three_line_up = test_levels_up[2] if len(test_levels_up) > 2 else b_up
                    else:
                        three_line_up = b_up

                    if current_price > three_line_up:
                        # 价格在高位，向下测量
                        trend = -1
                    else:
                        # 价格在低位，继续向上测量
                        trend = 1

        # 更新测量点（内部判断是否需要重新寻找基准K线）
        self.update_measure_point(
            self.mp_daily, self.am_daily,
            self.lookback_daily, trend, '日线'
        )

        if not self.mp_daily['levels']:
            return

        # 区域化线位判断
        zone_desc, zone_num, is_key, is_extreme = self.get_line_zone(
            self.mp_daily, current_price
        )
        self.daily_line_desc = zone_desc

        levels = self.mp_daily['levels']
        level9 = self.mp_daily['level9']
        start = self.mp_daily['start']
        end = self.mp_daily['end']
        trend = self.mp_daily['direction']  # 取当前实际方向

        # 判断形态：在关键线位时，看是否有反向形态
        pattern_ok = self.check_pattern(self.am_daily, -trend) if (is_key or is_extreme) else False

        if (is_key or is_extreme) and pattern_ok:
            # 在关键线位且形态成立，日线方向反转
            self.daily_direction = -trend
            action = '上涨' if self.daily_direction == 1 else '下跌'
            self.signal_stage = f'日线{action}确认，等待60分钟'
        else:
            # 趋势还在继续
            self.daily_direction = trend
            action = '上涨' if trend == 1 else '下跌'
            self.signal_stage = f'日线{action}，等待60分钟机会'

        # 历史回放时不输出，只更新状态
        if self._is_history:
            self.putEvent()
            return

        # 实盘时输出简洁结论（优先使用实时tick价格）
        tick_price = self.tick.lastPrice if self.tick and self.tick.lastPrice > 0 else None
        self._output_daily_summary(tick_price)
        self.putEvent()

    def _check_h1_signal(self, bar):
        """
        60分钟分析（V2升级）：
        1. 日线方向已确认
        2. 区域化判断60分钟线位
        3. 顺势到标准线位时，提示切换到15分钟/5分钟看出场
        4. 逆势回调到关键线位，等形态做顺势单
        5. 提前入场：60分钟刚到1线，5分钟才在2线，直接提示入场
        """
        # 用户要求：先只显示日线的提示，其他的不要显示了
        return

        if self.daily_direction == 0:
            return

        # 横盘模式下不做60分钟分析
        if self.market_mode == '横盘':
            return

        current_price = bar.close
        daily_dir = self.daily_direction

        if len(self.am_h1.close) < 5:
            return

        recent_h1 = self.am_h1.close[-5:]
        h1_trend = 1 if recent_h1[-1] > recent_h1[0] else -1
        self.h1_direction = h1_trend

        self.update_measure_point(
            self.mp_h1, self.am_h1,
            self.lookback_h1, h1_trend, '60分钟'
        )

        if not self.mp_h1['levels']:
            return

        zone_desc, zone_num, is_key, is_extreme = self.get_line_zone(
            self.mp_h1, current_price
        )
        self.h1_line_desc = zone_desc

        # 每根60分钟K线必输出一条状态信息
        dir_str = '上涨' if self.daily_direction == 1 else '下跌'
        h1_dir_str = '顺势上涨' if h1_trend == 1 else '逆势回调' if self.daily_direction == 1 else \
                     ('顺势下跌' if h1_trend == -1 else '逆势反弹')
        self.output(
            f'[实时] 日线{dir_str} | 60分钟{h1_dir_str} | '
            f'当前价格:{current_price:.1f} | 线位:{zone_desc} | '
            f'关键线:{is_key} | 模式:{self.signal_stage}'
        )

        # ---- 情形1：日线向上，60分钟顺势上涨 ----
        if daily_dir == 1 and h1_trend == 1:
            if is_extreme or zone_num >= 8:
                # 60分钟顺势到极线区域，提示切换小时区看出场
                self.output(
                    f'【60分钟提示-出场预警】60分钟顺势上涨到{zone_desc}({current_price:.1f})，'
                    f'接近极线！切换到{"15分钟" if self.enable_m15 else "5分钟"}看是否出现向下形态，'
                    f'提前判断是否回调'
                )
                self.signal_stage = '60分钟极线区域：监控出场信号'
            elif is_key and zone_num >= 5:
                # 60分钟顺势到5-8线关键区域
                self.output(
                    f'【60分钟提示-出场预警】60分钟顺势上涨到{zone_desc}({current_price:.1f})，'
                    f'关键线位！切换到{"15分钟" if self.enable_m15 else "5分钟"}看是否有回调形态'
                )
                self.signal_stage = '60分钟关键线位：监控出场信号'
            else:
                self.output(
                    f'【60分钟状态】日线向上，60分钟顺势上涨，'
                    f'当前在{zone_desc}({current_price:.1f})，继续持有'
                )

        # ---- 情形2：日线向上，60分钟逆势回调 ----
        elif daily_dir == 1 and h1_trend == -1:
            if is_extreme:
                # 回调到极线，强烈做多信号
                pattern_ok = self.check_pattern(self.am_h1, 1)
                if pattern_ok:
                    self.output(
                        f'\n{"="*50}\n'
                        f'【60分钟强信号】日线向上，60分钟回调到{zone_desc}({current_price:.1f})，\n'
                        f'极线区域+向上形态成立！强烈做多信号！\n'
                        f'止损：60分钟低点下方+1个点\n'
                        f'{"="*50}\n'
                    )
                    self.signal_stage = '60分钟做多信号，等待5分钟入场'
                else:
                    self.output(
                        f'【60分钟观察】回调到{zone_desc}({current_price:.1f})，等待向上形态...'
                    )
            elif is_key:
                pattern_ok = self.check_pattern(self.am_h1, 1)
                if pattern_ok:
                    self.output(
                        f'\n{"="*50}\n'
                        f'【60分钟信号】日线向上，60分钟回调到{zone_desc}({current_price:.1f})，\n'
                        f'向上反转形态成立！准备做多！\n'
                        f'止损：60分钟低点下方+1个点\n'
                        f'{"="*50}\n'
                    )
                    self.signal_stage = '60分钟做多信号，等待5分钟入场'
                else:
                    self.output(
                        f'【60分钟观察】回调到{zone_desc}({current_price:.1f})，等待向上形态...'
                    )
            elif zone_num <= 1.5:
                # 【提前入场逻辑】60分钟回调到1线区域，5分钟可能才在2线
                # 不等5分钟形态，直接提示入场
                if self.early_entry_enabled:
                    self.output(
                        f'\n{"="*50}\n'
                        f'【提前入场提示】60分钟回调到{zone_desc}({current_price:.1f})，\n'
                        f'行情刚刚启动，无需等5分钟形态，可直接入场做多！\n'
                        f'止损：60分钟A点（起涨点）下方+1个点\n'
                        f'注意：切换到5分钟确认当前5分钟线位，\n'
                        f'若5分钟已在5线以上则等回调再入\n'
                        f'{"="*50}\n'
                    )
                    self.signal_stage = '提前入场：60分钟1线，可直接做多'
            else:
                self.output(
                    f'【60分钟状态】日线向上，60分钟回调中，'
                    f'当前在{zone_desc}({current_price:.1f})，等待到达关键线位'
                )

        # ---- 情形3：日线向下，60分钟顺势下跌 ----
        elif daily_dir == -1 and h1_trend == -1:
            if is_extreme or zone_num >= 8:
                self.output(
                    f'【60分钟提示-出场预警】60分钟顺势下跌到{zone_desc}({current_price:.1f})，'
                    f'接近极线！切换到{"15分钟" if self.enable_m15 else "5分钟"}看是否出现向上形态'
                )
                self.signal_stage = '60分钟极线区域：监控出场信号'
            elif is_key and zone_num >= 5:
                self.output(
                    f'【60分钟提示-出场预警】60分钟顺势下跌到{zone_desc}({current_price:.1f})，'
                    f'关键线位！切换到{"15分钟" if self.enable_m15 else "5分钟"}看是否有反弹形态'
                )
                self.signal_stage = '60分钟关键线位：监控出场信号'
            else:
                self.output(
                    f'【60分钟状态】日线向下，60分钟顺势下跌，'
                    f'当前在{zone_desc}({current_price:.1f})，继续持有'
                )

        # ---- 情形4：日线向下，60分钟逆势反弹 ----
        elif daily_dir == -1 and h1_trend == 1:
            if is_extreme:
                pattern_ok = self.check_pattern(self.am_h1, -1)
                if pattern_ok:
                    self.output(
                        f'\n{"="*50}\n'
                        f'【60分钟强信号】日线向下，60分钟反弹到{zone_desc}({current_price:.1f})，\n'
                        f'极线区域+向下形态成立！强烈做空信号！\n'
                        f'止损：60分钟高点上方+1个点\n'
                        f'{"="*50}\n'
                    )
                    self.signal_stage = '60分钟做空信号，等待5分钟入场'
                else:
                    self.output(
                        f'【60分钟观察】反弹到{zone_desc}({current_price:.1f})，等待向下形态...'
                    )
            elif is_key:
                pattern_ok = self.check_pattern(self.am_h1, -1)
                if pattern_ok:
                    self.output(
                        f'\n{"="*50}\n'
                        f'【60分钟信号】日线向下，60分钟反弹到{zone_desc}({current_price:.1f})，\n'
                        f'向下反转形态成立！准备做空！\n'
                        f'止损：60分钟高点上方+1个点\n'
                        f'{"="*50}\n'
                    )
                    self.signal_stage = '60分钟做空信号，等待5分钟入场'
                else:
                    self.output(
                        f'【60分钟观察】反弹到{zone_desc}({current_price:.1f})，等待向下形态...'
                    )
            elif zone_num <= 1.5:
                if self.early_entry_enabled:
                    self.output(
                        f'\n{"="*50}\n'
                        f'【提前入场提示】60分钟反弹到{zone_desc}({current_price:.1f})，\n'
                        f'行情刚刚启动，无需等5分钟形态，可直接入场做空！\n'
                        f'止损：60分钟A点（起跌点）上方+1个点\n'
                        f'{"="*50}\n'
                    )
                    self.signal_stage = '提前入场：60分钟1线，可直接做空'
            else:
                self.output(
                    f'【60分钟状态】日线向下，60分钟反弹中，'
                    f'当前在{zone_desc}({current_price:.1f})，等待到达关键线位'
                )

        self.putEvent()

    def _check_m15_signal(self, bar):
        """
        15分钟分析（桥梁层）：
        当60分钟处于关键线位监控状态时，
        用15分钟判断是否已经出现回调/反转形态，
        提前于60分钟形态成立，给出出场预警
        """
        # 用户要求：先只显示日线的提示，其他的不要显示了
        return

        if '监控出场信号' not in self.signal_stage and '等待5分钟入场' not in self.signal_stage:
            return

        current_price = bar.close

        # 判断15分钟当前方向
        if len(self.am_m15.close) < 5:
            return

        m15_trend = 1 if self.am_m15.close[-1] > self.am_m15.close[-5] else -1

        self.update_measure_point(
            self.mp_m15, self.am_m15,
            self.lookback_m15, m15_trend, '15分钟'
        )

        if not self.mp_m15['levels']:
            return

        zone_desc, zone_num, is_key, is_extreme = self.get_line_zone(
            self.mp_m15, current_price
        )
        self.m15_line_desc = zone_desc

        # 出场预警：60分钟在关键线位，15分钟走到极线区域
        if '监控出场信号' in self.signal_stage:
            if is_extreme or zone_num >= 8:
                # 15分钟也到极线，共振信号
                pattern_ok = self.check_pattern(self.am_m15, -m15_trend)
                if pattern_ok:
                    self.output(
                        f'\n{"★"*20}\n'
                        f'【出场预警-多时区共振】\n'
                        f'60分钟在关键线位，15分钟也到{zone_desc}({current_price:.1f})，\n'
                        f'15分钟形态成立！强烈建议出场或减仓！\n'
                        f'切换到5分钟找精确出场点\n'
                        f'{"★"*20}\n'
                    )
                    self.signal_stage = '出场信号：15分钟+60分钟共振，切换5分钟出场'
                else:
                    self.output(
                        f'【15分钟观察】到达{zone_desc}({current_price:.1f})，'
                        f'等待形态确认出场...'
                    )

        self.putEvent()

    def _check_m5_signal(self, bar):
        """
        5分钟分析（V2升级）：
        1. 常规入场：60分钟信号触发后，5分钟关键线位+形态+KDJ
        2. 出场信号：5分钟到极线+形态，提前判断60分钟回调
        3. 横盘波段：横盘模式下，5分钟做1-8线波段
        4. 提前入场确认：60分钟1线信号时，确认5分钟当前线位
        """
        # 用户要求：先只显示日线的提示，其他的不要显示了
        return

        current_price = bar.close

        # ---- 横盘波段模式 ----
        if self.market_mode == '横盘' and self.scalp_mode_enabled:
            self._check_scalp_signal(bar)
            return

        # ---- 出场信号监控 ----
        if '监控出场信号' in self.signal_stage or '出场信号' in self.signal_stage:
            self._check_m5_exit_signal(bar)
            return

        # ---- 提前入场确认 ----
        if '提前入场' in self.signal_stage:
            self._check_early_entry(bar)
            return

        # ---- 常规入场信号 ----
        if '等待5分钟入场' not in self.signal_stage:
            return

        if '做多' in self.signal_stage:
            op_dir = 1
        elif '做空' in self.signal_stage:
            op_dir = -1
        else:
            return

        m5_trend = op_dir
        self.update_measure_point(
            self.mp_m5, self.am_m5,
            self.lookback_m5, m5_trend, '5分钟'
        )

        if not self.mp_m5['levels']:
            return

        zone_desc, zone_num, is_key, is_extreme = self.get_line_zone(
            self.mp_m5, current_price
        )

        if is_key or is_extreme:
            pattern_ok = self.check_pattern(self.am_m5, op_dir)
            k, d, j = self.calc_kdj(self.am_m5)
            self.kdj_k, self.kdj_d, self.kdj_j = k, d, j

            kdj_ok = (k > d) if op_dir == 1 else (k < d)

            if pattern_ok and kdj_ok:
                now = bar.datetime
                if self.last_signal_time != now:
                    self.last_signal_time = now
                    action = '做多' if op_dir == 1 else '做空'
                    stop_loss = current_price - 10 if op_dir == 1 else current_price + 10

                    self.output(
                        f'\n{"★"*20}\n'
                        f'【入场信号】{action}！\n'
                        f'当前价格: {current_price:.1f}\n'
                        f'5分钟在{zone_desc}，形态成立\n'
                        f'KDJ: K={k:.1f}, D={d:.1f}, J={j:.1f}（{"金叉" if op_dir==1 else "死叉"}）\n'
                        f'参考止损: {stop_loss:.1f}\n'
                        f'{"★"*20}\n'
                    )
                    self.signal_stage = f'{action}信号已发出，等待下一次机会'

            elif pattern_ok and not kdj_ok:
                self.output(
                    f'【5分钟观察】形态成立但KDJ未配合(K={k:.1f}, D={d:.1f})，继续等待...'
                )
            elif not pattern_ok:
                self.output(
                    f'【5分钟观察】在{zone_desc}({current_price:.1f})，等待形态成立...'
                )

        self.putEvent()

    def _check_m5_exit_signal(self, bar):
        """
        5分钟出场信号检测：
        当60分钟在关键线位时，5分钟走到极线+形态 = 提前出场信号
        这是"小时区判断大时区出场"的核心逻辑
        """
        current_price = bar.close

        if len(self.am_m5.close) < 5:
            return

        m5_trend = 1 if self.am_m5.close[-1] > self.am_m5.close[-5] else -1

        self.update_measure_point(
            self.mp_m5, self.am_m5,
            self.lookback_m5, m5_trend, '5分钟'
        )

        if not self.mp_m5['levels']:
            return

        zone_desc, zone_num, is_key, is_extreme = self.get_line_zone(
            self.mp_m5, current_price
        )

        if is_extreme or zone_num >= 8.5:
            # 5分钟走到极线区域
            pattern_ok = self.check_pattern(self.am_m5, -m5_trend)
            k, d, j = self.calc_kdj(self.am_m5)

            if pattern_ok:
                now = bar.datetime
                if self.last_exit_signal_time != now:
                    self.last_exit_signal_time = now
                    self.output(
                        f'\n{"★"*20}\n'
                        f'【出场信号-5分钟极线形态】\n'
                        f'5分钟走到{zone_desc}({current_price:.1f})，形态成立！\n'
                        f'这将带动60分钟开始回调，建议出场或减仓！\n'
                        f'KDJ: K={k:.1f}, D={d:.1f}, J={j:.1f}\n'
                        f'{"★"*20}\n'
                    )
                    self.signal_stage = '出场信号已发出：5分钟极线形态'
            else:
                self.output(
                    f'【5分钟出场观察】到达{zone_desc}({current_price:.1f})，等待形态确认...'
                )

    def _check_early_entry(self, bar):
        """
        提前入场确认：
        60分钟刚到1线，检查5分钟当前线位
        如果5分钟在2线以下，直接确认入场
        如果5分钟已经在5线以上，等回调再入
        """
        current_price = bar.close

        if len(self.am_m5.close) < 5:
            return

        m5_trend = 1 if self.am_m5.close[-1] > self.am_m5.close[-5] else -1

        self.update_measure_point(
            self.mp_m5, self.am_m5,
            self.lookback_m5, m5_trend, '5分钟'
        )

        if not self.mp_m5['levels']:
            return

        zone_desc, zone_num, is_key, is_extreme = self.get_line_zone(
            self.mp_m5, current_price
        )

        op_dir = 1 if '做多' in self.signal_stage else -1

        if zone_num <= 2.5:
            # 5分钟才在2线半以下，确认提前入场
            now = bar.datetime
            if self.last_signal_time != now:
                self.last_signal_time = now
                action = '做多' if op_dir == 1 else '做空'
                self.output(
                    f'\n{"★"*20}\n'
                    f'【提前入场确认】{action}！\n'
                    f'60分钟刚到1线，5分钟当前在{zone_desc}({current_price:.1f})，\n'
                    f'行情空间充足，可直接入场！\n'
                    f'止损：60分钟A点（起涨/起跌点）{"下方" if op_dir==1 else "上方"}+1个点\n'
                    f'{"★"*20}\n'
                )
                self.signal_stage = f'{action}提前入场信号已发出'
        elif zone_num >= 5:
            # 5分钟已经涨到5线以上，等回调
            self.output(
                f'【提前入场提示】5分钟已在{zone_desc}({current_price:.1f})，'
                f'空间有限，等5分钟回调后再入场'
            )
            self.signal_stage = '等待5分钟回调后入场'
        else:
            self.output(
                f'【提前入场观察】5分钟当前在{zone_desc}({current_price:.1f})，'
                f'继续观察...'
            )

    def _check_scalp_signal(self, bar):
        """
        横盘波段模式：
        在5分钟图里做1-8线波段，到8线/极线果断出场
        一旦发现波段幅度变大（可能变盘），立刻停手
        """
        current_price = bar.close

        if len(self.am_m5.close) < 5:
            return

        m5_trend = 1 if self.am_m5.close[-1] > self.am_m5.close[-5] else -1

        self.update_measure_point(
            self.mp_m5, self.am_m5,
            self.lookback_m5, m5_trend, '5分钟'
        )

        if not self.mp_m5['levels']:
            return

        zone_desc, zone_num, is_key, is_extreme = self.get_line_zone(
            self.mp_m5, current_price
        )

        # 检测是否已经突破横盘区间（变盘信号）
        if self.h1_range_high > 0 and self.h1_range_low > 0:
            if current_price > self.h1_range_high * 1.005 or \
               current_price < self.h1_range_low * 0.995:
                self.output(
                    f'【横盘波段-警告】价格突破横盘区间！'
                    f'可能变盘，停止波段操作，切换回趋势模式'
                )
                self.market_mode = '趋势'
                self.signal_stage = '等待日线确认新趋势'
                return

        # 到8线/极线，果断出场
        if is_extreme or zone_num >= 8:
            pattern_ok = self.check_pattern(self.am_m5, -m5_trend)
            if pattern_ok:
                now = bar.datetime
                if self.last_exit_signal_time != now:
                    self.last_exit_signal_time = now
                    action = '平多出场' if m5_trend == 1 else '平空出场'
                    self.output(
                        f'\n{"★"*20}\n'
                        f'【横盘波段-出场信号】{action}！\n'
                        f'5分钟到{zone_desc}({current_price:.1f})，形态成立\n'
                        f'横盘波段到位，果断出场！\n'
                        f'{"★"*20}\n'
                    )
                    self.scalp_direction = 0

        # 到1线，考虑反向入场
        elif zone_num <= 1.5:
            pattern_ok = self.check_pattern(self.am_m5, -m5_trend)
            if pattern_ok:
                now = bar.datetime
                if self.last_signal_time != now:
                    self.last_signal_time = now
                    # 在横盘底部做多，顶部做空
                    if m5_trend == -1:  # 下跌到1线，做多
                        self.output(
                            f'\n{"★"*20}\n'
                            f'【横盘波段-入场信号】做多！\n'
                            f'5分钟下跌到{zone_desc}({current_price:.1f})，形态成立\n'
                            f'横盘波段入场，目标：5分钟8线\n'
                            f'止损：1线下方5个点\n'
                            f'{"★"*20}\n'
                        )
                        self.scalp_direction = 1
                    else:
                        self.output(
                            f'【横盘波段-观察】5分钟在{zone_desc}({current_price:.1f})，'
                            f'等待形态确认...'
                        )

        self.putEvent()

    def onStop(self):
        """策略停止"""
        self.output('波神凯线策略已停止')
        super().onStop()


# ============================================================
# PythonLAB 回测入口
# 说明：
#   - 在无限易客户端中加载时，此段代码不会执行（因为不是直接运行该文件）
#   - 在命令行或 VS Code 中直接运行该文件时，会触发历史回测
#   - 回测使用量投 QuantFair 数据服务（Tick 级别历史数据）
#   - 需要先在 QuantFair 个人中心申请免费数据额度（1000次）
#
# 使用方法：
#   1. 打开命令行，切换到 pyStrategy 目录
#      cd C:\Users\Administrator\AppData\Roaming\InfiniTrader_SimulationBetaX64\pyStrategy
#   2. 运行（使用无限易自带的 Python）：
#      python strategy\boshen_strategy.py
#   3. 观察输出的日线提示信息，验证策略逻辑是否正确
#
# 回测参数说明（修改下方 BACKTEST_CONFIG 来调整）：
#   - instrument_id : 合约代码，如 rb2005（螺纹钢2005合约）
#   - exchange      : 交易所，SHFE=上期所，DCE=大商所，CZCE=郑商所
#   - start_date    : 回测开始日期（格式 YYYY-MM-DD）
#   - end_date      : 回测结束日期（不包含当天，格式 YYYY-MM-DD）
#   - initial_capital: 初始资金（默认100万）
# ============================================================

if __name__ == '__main__':
    # ---- 回测参数，按需修改 ----
    BACKTEST_CONFIG = {
        'instrument_id': 'rb2005',   # 合约代码（螺纹钢2005，历史数据完整）
        'exchange': 'SHFE',          # 交易所
        'start_date': '2019-11-01',  # 回测开始日期（rb2005 上市约在2019年）
        'end_date': '2020-05-15',    # 回测结束日期（2020年5月交割前）
        'initial_capital': 100_0000, # 初始资金：100万
    }

    # ---- QuantFair 数据服务密钥（你的账号密钥，勿泄露）----
    ACCESS_KEY = 'FbEZcnXyM169skDYRQJEhi'
    ACCESS_SECRET = 'BZg9fT+MNGa5YM7VA5vaNYOfx/UzoVPFRhAGNbqUJ8k='

    # ---- 以下代码无需修改 ----
    import sys
    import os

    # 确保 pythongo 框架可以被找到（pyStrategy 目录下）
    _this_dir = os.path.dirname(os.path.abspath(__file__))
    _parent_dir = os.path.dirname(_this_dir)  # pyStrategy 目录
    if _parent_dir not in sys.path:
        sys.path.insert(0, _parent_dir)
    if _this_dir not in sys.path:
        sys.path.insert(0, _this_dir)

    try:
        from pythongo.backtesting.engine import run
        from pythongo.backtesting.models import Config
    except ImportError as e:
        print('=' * 60)
        print('【错误】无法导入回测模块，请确认：')
        print('  1. 当前目录在 pyStrategy 目录下（或其子目录）')
        print('  2. pythongo 框架目录存在于 pyStrategy 下')
        print('  3. 使用的是无限易自带的 Python（非系统 Python）')
        print(f'  错误详情：{e}')
        print('=' * 60)
        sys.exit(1)

    # 构建策略参数（对应类属性中的默认参数）
    class Params:
        """回测用参数映射类"""
        def __init__(self):
            self.vtSymbol = BACKTEST_CONFIG['instrument_id']
            self.exchange = BACKTEST_CONFIG['exchange']
            self.investor = ''
            self.lookback_daily = 100
            self.lookback_h1 = 40
            self.lookback_m15 = 30
            self.lookback_m5 = 30
            self.tolerance_pct = 0.8
            self.enable_m15 = True
            self.enable_range_band = True
            self.early_entry_enabled = True
            self.scalp_mode_enabled = True

    params = Params()

    backtesting_config = Config(
        access_key=ACCESS_KEY,
        access_secret=ACCESS_SECRET,
    )

    print('=' * 60)
    print('【波神凯线策略 - PythonLAB 历史回测】')
    print(f'  合约：{BACKTEST_CONFIG["exchange"]} {BACKTEST_CONFIG["instrument_id"]}')
    print(f'  区间：{BACKTEST_CONFIG["start_date"]} ~ {BACKTEST_CONFIG["end_date"]}')
    print(f'  初始资金：{BACKTEST_CONFIG["initial_capital"]:,.0f} 元')
    print('  注意：回测使用 Tick 数据，每个交易日消耗 1 次数据额度')
    print('=' * 60)

    run(
        config=backtesting_config,
        strategy_cls=boshen_strategy(),
        strategy_params=params,
        start_date=BACKTEST_CONFIG['start_date'],
        end_date=BACKTEST_CONFIG['end_date'],
        initial_capital=BACKTEST_CONFIG['initial_capital'],
    )
