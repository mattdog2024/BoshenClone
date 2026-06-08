from PySide6.QtWidgets import QWidget, QApplication, QInputDialog, QMenu, QMessageBox, QDialog, QVBoxLayout, QLabel, QPushButton, QScrollArea
from PySide6.QtCore import Qt, QPoint, QRect, QTimer, QThread, Signal, QObject
from PySide6.QtGui import QPainter, QPen, QColor, QFont, QCursor, QGuiApplication, QAction
from algorithms import BoshenAlgorithms
from preset_manager import PresetManager
from ocr_helper import BoshenOCR
from region_manager import RegionManager
from typing import List, Dict

class CalibrationWorker(QThread):
    finished = Signal(dict) # Emits success result (or None)
    
    def __init__(self, ocr_helper, image_paths: List[str]):
        super().__init__()
        self.ocr_helper = ocr_helper
        self.image_paths = image_paths
        
    def run(self):
        import logging
        best_result = None
        best_gap = -1
        
        # Original logic from auto_calibrate_axis, but adapted for threaded execution
        for path in self.image_paths:
            logging.info(f"Worker analyzing {path}...")
            result = self.ocr_helper.analyze_axis(path)
            
            if result:
                logging.info(f"Worker: Success for {path}, Gap={result['avg_gap']}")
                if result['avg_gap'] > best_gap:
                    best_gap = result['avg_gap']
                    best_result = result
            else:
                logging.warning(f"Worker: Failed for {path}")
                
        # Emit the best result found (or None)
        self.finished.emit(best_result)

class Overlay(QWidget):
    """
    Transparent full-screen overlay for drawing.
    """
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)  # Default to transparent
        
        # State
        self.current_tool = None
        self.start_point = None
        self.end_point = None
        self.is_drawing = False
        self.drawings = [] # List of dicts: {'type':Str, 'data':Tuple}
        self.fast_mode = False # Fast measurement mode
        self.preset_manager = PresetManager()
        self.global_calibration = None # {'scale': float, 'ref_y': int, 'ref_price': float}
        self.ocr_helper = BoshenOCR()
        
        # Free Drawing State
        self.free_drawings = [] # List of list of QPoints: [[p1, p2, ...], [p1, p2...]]
        self.current_free_drawing = [] # Current stroke
        self.free_draw_color = QColor(Qt.red)
        self.free_draw_width = 2

        # ==============================================================
        # 多周期窗格（Region）支持
        # 用户的新交易软件是一个屏幕显示 6 个周期的网格界面。
        # 我们让用户先“框选”出每个周期窗格的矩形区域，
        # 之后每条测量线都会带上 region_id 标签，归属到对应的周期窗格。
        # 所有窗格的测量线会“同时显示并保持”，互不影响。
        # ==============================================================
        self.region_manager = RegionManager()
        # 是否正在框选一个新的周期区域（由工具栏“框选周期”按钮触发）
        self.defining_region = False

        # Strategy UI Removed per user request
        self.current_timeframe = "日线" # Default to daily or None
        self.analysis_data = {
            '日线': [],
            '4小时': [],
            '1小时': []
        }
        self.load_analysis_data()
        # 从文件恢复各周期窗格的历史测量线
        self.load_region_drawings()
        
        # Interaction state
        self.dragging_handle = None 
        self.hover_handle = None

        # Style Config
        self.styles = {
            'default': {'color': QColor(255, 0, 0), 'width': 1, 'style': Qt.DotLine}, 
            'highlight': {'color': QColor(255, 0, 127), 'width': 3, 'style': Qt.SolidLine}, 
            'measurement': {'color': QColor(255, 0, 0), 'width': 1, 'style': Qt.SolidLine},
            'handle_fill': QColor(255, 0, 0, 100)
        }

        # ----------------------------------------------------------
        # 关键初始化（原代码误把这段放进了 apply_calibration 内部，
        # 导致 poll_timer 等从未执行、悬停编辑失效，这里修复并移回 __init__）
        # ----------------------------------------------------------
        # Screen geometry
        self.setGeometry(QApplication.primaryScreen().geometry())
        # Transparent background
        self.setStyleSheet("background-color: transparent;")
        # Mouse tracking
        self.setMouseTracking(True)
        # Polling timer for interaction when transparent
        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self.check_mouse_hover)
        self.poll_timer.start(50)  # Check every 50ms

    def toggle_fast_mode(self, enabled):
        self.fast_mode = enabled
        print(f"Fast Mode set to: {enabled}")

    def set_timeframe(self, timeframe_name):
        # 1. Capture current drawings to preserve them across switch
        # User wants to "carry over" lines if they forgot to switch beforehand.
        current_drawings_preservation = self.drawings.copy()
        
        self.current_timeframe = timeframe_name
        print(f"Switching Timeframe to: {self.current_timeframe}")
        
        # Load data for this timeframe
        data = self.analysis_data.get(timeframe_name)
        
        # Reset current state
        self.drawings = []
        self.global_calibration = None
        
        if not data:
            self.update()
            return
            
        # Check format
        calibration_data = None
        drawings_data = []
        
        if isinstance(data, list):
            # Legacy format (List of dicts)
            drawings_data = data
        elif isinstance(data, dict):
            # New format
            calibration_data = data.get('calibration')
            drawings_data = data.get('drawings', [])
            
        # Restore Calibration
        if calibration_data:
            self.global_calibration = calibration_data
            print(f"Restored Calibration: {self.global_calibration}")
            
        # Restore Drawings
        for d in drawings_data:
            try:
                # If we have saved coords, use them
                if 'start_x' in d and 'start_y' in d:
                    start_p = QPoint(d['start_x'], d['start_y'])
                    end_p = QPoint(d['end_x'], d['end_y'])
                else:
                    # Fallback if we only have prices and calibration (Legacy restore attempt)
                    if self.global_calibration and 'price_a' in d:
                        scale = self.global_calibration['scale']
                        ref_y = self.global_calibration['ref_y']
                        ref_price = self.global_calibration['ref_price']
                        
                        ya = ref_y + (d['price_a'] - ref_price) / scale
                        yb = ref_y + (d['price_b'] - ref_price) / scale
                        
                        # Use center of screen for X
                        cx = self.width() // 2
                        start_p = QPoint(cx, int(ya))
                        end_p = QPoint(cx, int(yb))
                    else:
                        continue # Cannot restore
                        
                new_d = {
                    'type': 'boshen_single',
                    'start': start_p,
                    'end': end_p,
                    'price_a': d.get('price_a', 0.0),
                    'price_b': d.get('price_b', 0.0),
                    'timeframe': timeframe_name,
                    'scale': self.global_calibration['scale'] if self.global_calibration else 1.0
                }
                self.drawings.append(new_d)
            except Exception as e:
                print(f"Error restoring drawing: {e}")

        # Restore preserved drawings (merging them in)
        if current_drawings_preservation:
             # Only add if not already present? 
             # For now, just add them. They will be visually distinct or user can delete.
             # Ideally we check for duplicates but 'merging' is safer to ensure nothing is lost.
             for d in current_drawings_preservation:
                 d['timeframe'] = self.current_timeframe 
                 self.drawings.append(d)
                 
        self.update()

    def auto_measure(self, pos, wick_mode=False):
        """
        Captures screen, analyzes column at pos.x(), finds High/Low.
        """
        import logging
        # Overwrite log each time (filemode='w') so the file always reflects the latest run.
        # Use DEBUG level to capture per-column scan details for diagnosing recognition bugs.
        for handler in logging.root.handlers[:]:
            logging.root.removeHandler(handler)
        logging.basicConfig(
            filename='debug_boshen.log',
            filemode='w',
            level=logging.DEBUG,
            format='%(asctime)s %(levelname)s %(message)s'
        )
        
        logging.info(f"Auto measure triggered at logical pos: {pos}")
        
        # 1. Hide overlay to capture underlying screen
        self.setVisible(False)
        QApplication.processEvents() # Process existing events
        import time
        time.sleep(0.1) # Wait for DWM/Compositor to update screen
        
        try:
            # 2. Capture Screen
            screen = QGuiApplication.primaryScreen()
            if not screen:
                logging.error("No primary screen found")
                self.setVisible(True)
                return
                
            # Grab window 0 (desktop)
            pixmap = screen.grabWindow(0)
            image = pixmap.toImage()
            
            # High DPI Scaling Calculation
            dpr = screen.devicePixelRatio()
            logging.info(f"Device Pixel Ratio: {dpr}")
            
            # Scale coordinates to physical pixels
            # IMPORTANT: pos is now GLOBAL position
            # We assume grabWindow(0) returns the full desktop at dpr scale.
            
            # Map global pos to device pixels
            x_global = int(pos.x() * dpr)
            y_global = int(pos.y() * dpr)
            
            click_y = y_global
            x = x_global
            
            # Boundary check
            if x >= image.width() or click_y >= image.height():
                logging.error(f"Click out of bounds. Image: {image.width()}x{image.height()}, ClickGlobal: {x},{click_y}")
                self.setVisible(True)
                return

            height = image.height()
            logging.info(f"Analyzing column at physical x={x}, y={click_y}, image height={height}")

            # 3. Analyze Column
            # Heuristic: Find background color (most common color in the strip)
            # KEY FIX: When the K-line is near the bottom of the screen, the original
            # sampling window (click_y ± 300px) spills into the chart's bottom toolbar
            # area.  That toolbar is often a solid dark/gray band whose color becomes
            # the "most common" color and is mistakenly identified as the background.
            # Once the chart background is wrong, every candle pixel looks like
            # background → the scan finds nothing → top == bottom → red line.
            #
            # Fix: always bias the sampling window UPWARD so it stays inside the
            # chart area.  We take 500px above the click and only 100px below.
            # 背景色检测：在图像中间区域多列多行采样，取出现最多的颜色作为背景色
            # 不用单列采样，避免采样点落在K线上导致误判
            color_counts = {}
            img_w = image.width()
            img_h = image.height()
            # 采样区域：水平方向取图像中间 1/4~3/4，垂直方向取 toolbar 以下到底部
            sample_x_list = range(img_w // 4, img_w * 3 // 4, max(1, img_w // 40))
            sample_y_list = range(80, img_h - 50, max(1, img_h // 30))
            for sy in sample_y_list:
                for sx in sample_x_list:
                    pixel = image.pixelColor(sx, sy).rgb()
                    color_counts[pixel] = color_counts.get(pixel, 0) + 1

            if not color_counts:
                logging.error("No pixels scanned")
                self.setVisible(True)
                return

            bg_color_rgb = max(color_counts, key=color_counts.get)
            bg_color = QColor(bg_color_rgb)
            logging.info(f"Detected BG Color: {bg_color.name()} (count={color_counts[bg_color_rgb]})")

            # --- SMART TOOLBAR DETECTION ---
            # Scan downward from the top of the screen to find where the chart
            # area begins (i.e. the toolbar bottom).  We look for the first row
            # where the majority of pixels in the chart column range match the
            # background color.  This works regardless of screen resolution,
            # DPI scaling, or how many toolbar rows the software shows.
            toolbar_bottom_y = 0  # default: no toolbar
            bg_r = QColor(bg_color).red()
            bg_g = QColor(bg_color).green()
            bg_b = QColor(bg_color).blue()
            # Sample a horizontal band in the middle of the chart (avoid side panels)
            sample_x_start = max(100, image.width() // 6)
            sample_x_end   = min(image.width() - 200, image.width() * 5 // 6)
            sample_width   = max(1, sample_x_end - sample_x_start)
            consecutive_bg_rows = 0
            for ty in range(0, min(300, height)):
                bg_count = 0
                for tx in range(sample_x_start, sample_x_end, 4):  # step 4 for speed
                    tc = image.pixelColor(tx, ty)
                    dr = abs(tc.red()   - bg_r)
                    dg = abs(tc.green() - bg_g)
                    db = abs(tc.blue()  - bg_b)
                    if max(dr, dg, db) < 25:
                        bg_count += 1
                sampled = (sample_x_end - sample_x_start) // 4
                if sampled > 0 and bg_count / sampled > 0.80:
                    consecutive_bg_rows += 1
                    if consecutive_bg_rows >= 3:
                        toolbar_bottom_y = ty - 2  # first row of chart area
                        break
                else:
                    consecutive_bg_rows = 0
            logging.info(f"Smart toolbar_bottom_y={toolbar_bottom_y}")
            # bridge_threshold: if Phase-1/Phase-2 finds a candle top within
            # this many pixels of the toolbar bottom, we allow a large gap scan
            # to bridge the toolbar and find the true wick top above it.
            bridge_threshold = toolbar_bottom_y + 80
            # --- END SMART TOOLBAR DETECTION ---

            def color_distance(c1, c2):
                # Euclidean distance in RGB space
                r_diff = c1.red() - c2.red()
                g_diff = c1.green() - c2.green()
                b_diff = c1.blue() - c2.blue()
                return (r_diff**2 + g_diff**2 + b_diff**2) ** 0.5
            
            def is_candle_pixel(y):
                if y < 0 or y >= height: return False
                pixel_color = image.pixelColor(x, y)
                # If color is close to BG, it IS background (so NOT candle)
                # Tolerance of 30 covers minor noise/compression artifacts
                return color_distance(pixel_color, bg_color) > 30

            logging.info(f"Pixel at click ({x}, {click_y}) is candle? {is_candle_pixel(click_y)} (Color: {QColor(image.pixelColor(x, click_y).rgb()).name()})")

            # ================================================================
            # 4.0 干扰行检测（关键改进）
            # 用户图上有大量"水平贯穿"的干扰元素：买入线、止损线、
            # 红/蓝/黑水平测量线、"3163 多单40手 -1400元"等文字标注线。
            # 这些都会被"非背景色"判定误当成 K 线像素，污染顶/底识别。
            #
            # 区分关键：K 线实体在水平方向最多十几像素宽；而横线/文字
            # 标注在水平方向"贯穿"很长（几十~几百像素）。
            # 因此：对每一行，统计点击列两侧较宽范围内的非背景像素数，
            # 若该行水平方向"连得太长"（> H_LINE_MAX_WIDTH），判定为干扰行，
            # 后续按列扫描 K 线段时把这些行当作背景跳过。
            # ================================================================
            # 横向检测范围：以点击列为中心取一个宽窗口（覆盖单根 K 线宽度的数倍）
            h_probe_half = 90  # 左右各探测 90 物理像素
            h_probe_start = max(0, x - h_probe_half)
            h_probe_end = min(image.width(), x + h_probe_half + 1)
            h_probe_total = h_probe_end - h_probe_start
            # 一根 K 线（含影线列）的最大像素宽度估计。超过它一大截即视为横线/文字。
            H_LINE_MAX_WIDTH = 40  # 物理像素：K线最多约这么宽，横线远超此值
            interference_rows = set()
            # 只在可能涉及的 y 区间做检测，省时间
            y_scan_lo = max(0, (toolbar_bottom_y if toolbar_bottom_y > 0 else 0))
            y_scan_hi = min(height, click_y + 800)
            # ----------------------------------------------------------------
            # 关键修正（避免误伤密集 K 线）：
            # 横线/文字标注的本质是「水平方向连续贯穿很长」；而多根挨得很近的
            # K 线，虽然非背景像素总数也可能很大，但它们之间有背景缝隙——不是
            # 一条连续的横线。
            # 因此判定干扰行时，不看「非背景像素总数」，而看
            # 「最长的一段连续非背景像素」(max_run)。只有当某一行存在一段
            # 远超单根 K 线宽度的连续非背景像素时，才判定为横线/文字干扰行。
            # 这样：
            #   · 真横线/文字带（连续几十~几百像素）→ 命中，剔除；
            #   · 密集 K 线（各自≤十几像素、彼此有缝）→ 不命中，K线实体安全保留。
            # ----------------------------------------------------------------
            step = 2
            run_thresh = max(1, H_LINE_MAX_WIDTH // step)  # 连续采样点数阈值
            for yy in range(y_scan_lo, y_scan_hi):
                cur_run = 0
                max_run = 0
                gap = 0
                for xx in range(h_probe_start, h_probe_end, step):
                    c = image.pixelColor(xx, yy)
                    if color_distance(c, bg_color) > 30:
                        cur_run += 1
                        gap = 0
                        if cur_run > max_run:
                            max_run = cur_run
                    else:
                        # 容忍横线内 1 个采样点的微小断裂（抗锯齿/虚线），
                        # 但 K 线之间的真实缝隙(≥2采样点≈4px)会真正断开连续段。
                        gap += 1
                        if gap >= 2:
                            cur_run = 0
                # max_run 是「最长连续段」的采样点数，超过阈值才算横线/文字
                if max_run > run_thresh:
                    interference_rows.add(yy)
            logging.info(f"Detected {len(interference_rows)} interference (horizontal line/text) rows "
                         f"in probe window x=[{h_probe_start},{h_probe_end}] (max_run thresh={run_thresh})")

            def is_candle_pixel_clean(col_x, y):
                """非背景 且 不在干扰行内，才算 K 线像素。"""
                if y < 0 or y >= height:
                    return False
                if y in interference_rows:
                    return False
                c = image.pixelColor(col_x, y)
                return color_distance(c, bg_color) > 30

            # ================================================================
            # 4. 连续像素段检测（不依赖颜色匹配，深色/浅色主题通用）
            # 先定位目标 K 线中心列，再在该 K 线列簇内逐列扫描连续段：
            #   最长的段 = K线实体，整体范围顶底 = 完整K线（含影线）
            # 干扰横线/文字行已在 find_segments_in_col 中通过
            # is_candle_pixel_clean 排除，不会污染顶/底判定。
            # ================================================================
            
            def find_segments_in_col(col_x, y_start, y_end, gap_tol=8):
                """
                在指定列的 y_start~y_end 范围内，找出所有连续非背景色像素段。

                关键改进（修复"长下影线被横线截断、最高点丢失"的 BUG）：
                ─────────────────────────────────────────────────────────
                干扰行（买入线/止损线/各种水平测量线/文字标注）会把它覆盖到
                的 K 线像素"遮住"。这些行既不是背景、也不是可信的 K 线，
                而是"未知/被遮挡"区域。

                如果像之前那样把干扰行简单当成背景，那么当一条（或几条叠加的）
                横线/文字带跨在 K 线上、其高度超过 gap_tol 时，整根 K 线会被
                "切断"成上下两段：
                    上段 = 实体 + 上影（含最高点 3152）
                    下段 = 下影（含最低点 3136）
                用户点击在下影附近 → 只选中下段 → 最高点 3152(B点) 丢失。
                这正是用户截图反映的现象。

                修复：把"干扰行"视为"可穿透/桥接"，它不计入 gap 预算。
                只有"真背景行"才累加 gap，gap 超过 gap_tol 才真正断段。
                这样横线/文字无论多高都不会把同一根 K 线切断，
                同时仍然不让横线本身的像素污染顶/底（顶底只取真实 K 线像素）。
                返回列表：[(seg_top, seg_bottom), ...]
                """
                segments = []
                in_seg = False
                seg_start = 0
                gap_count = 0
                last_valid = -1
                for y in range(y_start, y_end):
                    # 真实 K 线像素（非背景 且 不在干扰行）
                    is_candle = is_candle_pixel_clean(col_x, y)
                    if is_candle:
                        if not in_seg:
                            in_seg = True
                            seg_start = y
                        last_valid = y
                        gap_count = 0
                        continue
                    # 不是 K 线像素：要区分"干扰行(被遮挡)"还是"真背景"
                    if y in interference_rows:
                        # 干扰行：被横线/文字遮挡，视为可穿透。
                        # 不累加 gap，也不结束当前段——让 K 线跨过横线继续连接。
                        # （注意：不更新 last_valid，避免把横线像素当成 K 线顶/底）
                        continue
                    # 真背景行
                    if in_seg:
                        gap_count += 1
                        if gap_count > gap_tol:
                            segments.append((seg_start, last_valid))
                            in_seg = False
                            gap_count = 0
                if in_seg:
                    segments.append((seg_start, last_valid))
                return segments

            # 工具栏底部 y 坐标（扫描不超过工具栏）
            scan_top_limit = toolbar_bottom_y if toolbar_bottom_y > 0 else 0
            scan_y_end = min(height, click_y + 800)

            # ============================================================
            # 第1步：定位"目标 K 线"的水平中心列
            # 用户点击位置可能略偏（落在相邻小K线/间隙上），所以不能死守点击列。
            # 在点击列左右一个较大半径内，找出"在点击 y 附近有 K 线像素"的列，
            # 取离点击列最近的一簇连续列，其中心即为目标 K 线中心列。
            # ============================================================
            locate_radius = 25  # 物理像素：左右各找 25 列定位 K 线
            loc_start = max(0, x - locate_radius)
            loc_end = min(image.width(), x + locate_radius + 1)
            # 点击点附近的容差：判断某列在点击 y 上下是否有 K 线像素
            cand_cols = []  # 含点击点附近 K 线段的列
            for cx in range(loc_start, loc_end):
                segs = find_segments_in_col(cx, scan_top_limit, scan_y_end, gap_tol=12)
                for seg in segs:
                    if seg[0] - 25 <= click_y <= seg[1] + 25:
                        cand_cols.append(cx)
                        break

            if cand_cols:
                # 把 cand_cols 按连续性聚簇，选包含/最接近点击列 x 的那一簇
                clusters = []
                cur = [cand_cols[0]]
                for c in cand_cols[1:]:
                    if c - cur[-1] <= 3:  # 允许 ≤3px 小缝隙
                        cur.append(c)
                    else:
                        clusters.append(cur)
                        cur = [c]
                clusters.append(cur)
                # 选离点击列 x 最近的簇
                def cluster_dist(cl):
                    if cl[0] <= x <= cl[-1]:
                        return 0
                    return min(abs(cl[0] - x), abs(cl[-1] - x))
                best_cluster = min(clusters, key=cluster_dist)
                candle_center_x = (best_cluster[0] + best_cluster[-1]) // 2
                candle_left = best_cluster[0]
                candle_right = best_cluster[-1]
                logging.info(f"Located candle cluster cols [{candle_left},{candle_right}], center={candle_center_x}")
            else:
                # 退回到点击列
                candle_center_x = x
                candle_left = max(0, x - 6)
                candle_right = min(image.width() - 1, x + 6)
                logging.info("No candle cluster located, fallback to click column.")

            # ============================================================
            # 第2步：以目标 K 线为范围扫描完整高低点
            # 扫描列严格限制在该 K 线的列簇内（再各扩 2px 容噪），
            # 这样不会吃到相邻 K 线，也不会被干扰横线污染（已在段检测中排除）。
            # full 范围 = 含影线的整根K线；body 范围 = 各列最长段（实体）
            # ============================================================
            seg_x_start = max(0, candle_left - 2)
            seg_x_end = min(image.width(), candle_right + 2 + 1)

            all_full_top = None
            all_full_bottom = None
            all_body_top = None
            all_body_bottom = None
            found_any_candle = False

            for scan_x in range(seg_x_start, seg_x_end):
                segs = find_segments_in_col(scan_x, scan_top_limit, scan_y_end, gap_tol=12)
                if not segs:
                    continue

                # 取包含点击点的段（容差 ±25px）
                target_seg = None
                for seg in segs:
                    if seg[0] - 25 <= click_y <= seg[1] + 25:
                        target_seg = seg
                        break
                if target_seg is None:
                    # 该列没有命中点击点的段：可能是只含影线的列，
                    # 取与已知 K 线范围重叠最大的段作为补充（不强制）
                    continue

                longest_seg = max(segs, key=lambda s: s[1] - s[0])
                if abs(longest_seg[0] - target_seg[0]) > 200:
                    longest_seg = target_seg

                col_full_top = target_seg[0]
                col_full_bottom = target_seg[1]

                found_any_candle = True
                if all_full_top is None or col_full_top < all_full_top:
                    all_full_top = col_full_top
                if all_full_bottom is None or col_full_bottom > all_full_bottom:
                    all_full_bottom = col_full_bottom
                if all_body_top is None or longest_seg[0] < all_body_top:
                    all_body_top = longest_seg[0]
                if all_body_bottom is None or longest_seg[1] > all_body_bottom:
                    all_body_bottom = longest_seg[1]

                logging.debug(f"  col x={scan_x}: target={col_full_top}~{col_full_bottom}, body={longest_seg[0]}~{longest_seg[1]}")

            if not found_any_candle:
                logging.warning("No candle found in scan width.")
                self.setVisible(True)
                return

            # 用 K 线中心列作为测量竖线 x（更准），后面 global point 用它
            x = candle_center_x

            top_y    = all_full_top
            bottom_y = all_full_bottom
            body_top_y    = all_body_top
            body_bottom_y = all_body_bottom

            logging.info(f"Segment scan: full=({top_y},{bottom_y}), body=({body_top_y},{body_bottom_y})")

            # 安全检查
            if top_y >= bottom_y:
                logging.warning("top_y >= bottom_y after segment scan, aborting.")
                self.setVisible(True)
                return
            if body_top_y >= body_bottom_y:
                body_top_y = top_y
                body_bottom_y = bottom_y

            logging.info(f"Final Candle Limit: Top={top_y}, Bottom={bottom_y}")
            print(f"Candle detected: Top={top_y}, Bottom={bottom_y}")

            if top_y == bottom_y:
                logging.warning("Top == Bottom, possible failure.")
                 
            # --- VISUAL DEBUGGING ---
            # Save the captured image with markings to verify what we saw
            debug_painter = QPainter(pixmap)
            debug_painter.setPen(QPen(QColor(255, 0, 0), 2)) # Red for click column
            debug_painter.drawLine(x, 0, x, height)
            
            debug_painter.setPen(QPen(QColor(0, 255, 0), 5)) # Green for Click
            debug_painter.drawPoint(x, click_y)
            
            debug_painter.setPen(QPen(QColor(255, 255, 0), 5)) # Yellow for Top
            debug_painter.drawPoint(x, top_y)
            
            debug_painter.setPen(QPen(QColor(0, 255, 255), 5)) # Cyan for Bottom
            debug_painter.drawPoint(x, bottom_y)
            
            debug_painter.end()
            pixmap.save("debug_last_capture.png")
            logging.info("Saved debug_last_capture.png")
            # ------------------------

            # 6. Determine A and B
            # Convert back to logical coordinates for drawing
            top_y_logical = top_y / dpr
            bottom_y_logical = bottom_y / dpr
            
            # Logic: If click is closer to Bottom, Bottom is A.
            # Use raw click_y (scaled) for distance comparison
            dist_to_top = abs(click_y - top_y)
            dist_to_bottom = abs(click_y - bottom_y)
            
            # These are GLOBAL logical Y coordinates
            # We must map them to LOCAL coordinates for drawing on the overlay
            
            # ================================================================
            # wick_mode 专用：水平宽度扫描，精确区分实体和影线
            # 原理：实体是宽的（多列有像素），影线是细的（1~2列有像素）
            # ================================================================
            if wick_mode and top_y < bottom_y:
                # 对 top_y~bottom_y 每一行，统计该 K 线列簇内有多少列是 K 线像素
                # （使用 is_candle_pixel_clean 排除干扰横线/文字行）
                row_widths = []
                for ry in range(top_y, bottom_y + 1):
                    w = 0
                    for rx in range(seg_x_start, seg_x_end):
                        if is_candle_pixel_clean(rx, ry):
                            w += 1
                    row_widths.append((ry, w))
                # 实体行：宽度达到列簇宽度的一定比例（实体占满，影线只有中间1~2列）
                cluster_w = max(1, seg_x_end - seg_x_start)
                body_threshold = max(2, int(cluster_w * 0.5))
                body_rows = [ry for (ry, w) in row_widths if w >= body_threshold]
                if body_rows:
                    body_top_y    = body_rows[0]
                    body_bottom_y = body_rows[-1]
                    logging.info(f"Wick horizontal scan: body=({body_top_y},{body_bottom_y}), full=({top_y},{bottom_y}), thr={body_threshold}/{cluster_w}")
                else:
                    # 如果没有达标的实体行，回退到全范围
                    body_top_y    = top_y
                    body_bottom_y = bottom_y
                    logging.info(f"Wick horizontal scan: no body rows found, fallback to full")

            # 测量竖线 x：用识别到的 K 线中心列（物理）转回逻辑坐标，
            # 这样即使用户点偏了，竖线也精准落在目标 K 线上。
            line_x_logical = int(candle_center_x / dpr)

            # Create global QPoints
            if wick_mode:
                # ============================================================
                # 影模式 A/B 点逻辑：
                #   A = 影线端点（最高价或最低价）
                #   B = K 线实体端点（收盘价）
                #   根据点击位置判断方向：靠近上方→上影；靠近下方→下影
                # 直接使用新算法已计算的 body_top_y/body_bottom_y
                # ============================================================
                body_mid_y2 = (body_top_y + body_bottom_y) / 2
                if click_y <= body_mid_y2:
                    wick_a_y = top_y / dpr
                    wick_b_y = body_top_y / dpr
                    logging.info(f"Wick mode UP: A=wick_top({wick_a_y}), B=body_top({wick_b_y})")
                else:
                    wick_a_y = bottom_y / dpr
                    wick_b_y = body_bottom_y / dpr
                    logging.info(f"Wick mode DOWN: A=wick_bottom({wick_a_y}), B=body_bottom({wick_b_y})")
                global_start_p = QPoint(line_x_logical, int(wick_a_y))
                global_end_p   = QPoint(line_x_logical, int(wick_b_y))
            else:
                # K线模式：A/B = 整根K线最高/最低，根据点击位置判断哪端是A
                global_start_p = QPoint(line_x_logical, int(bottom_y_logical if dist_to_bottom < dist_to_top else top_y_logical))
                global_end_p = QPoint(line_x_logical, int(top_y_logical if dist_to_bottom < dist_to_top else bottom_y_logical))
            
            # Map to local
            start_p = self.mapFromGlobal(global_start_p)
            end_p = self.mapFromGlobal(global_end_p)
            
            # 7. Add Drawing
            new_drawing = {
                'type': 'boshen_single',
                'start': start_p,
                'end': end_p,
                # 'timeframe': ... we don't strictly need to tag them anymore if we use snapshots, 
                # but might be good for visual debugging.
                'timeframe': self.current_timeframe 
            }

            # ----------------------------------------------------------
            # 绑定到所属周期窗格(Region)
            # 优先用“点击位置落在哪个区域”，其次用当前激活区域。
            # 这样无论用户先框选哪个窗格，测量线都会归到正确的周期，
            # 并随该窗格一起锁定保持。
            # ----------------------------------------------------------
            local_click = self.mapFromGlobal(pos)
            region = self.region_manager.region_at(local_click)
            if region is None:
                region = self.region_manager.get_active_region()
            if region is not None:
                new_drawing['region_id'] = region.id
                # 测量后把该窗格设为激活，方便“删除本周期”定位
                self.region_manager.set_active(region.id)

            # Apply global calibration if available
            self.apply_calibration(new_drawing)
            
            self.drawings.append(new_drawing)

            # 若归属某个周期窗格，持久化以便重启后保持
            if new_drawing.get('region_id'):
                self.save_region_drawings()

            self.set_tool(None)
            
        except Exception as e:
            logging.error(f"Auto measure error: {e}", exc_info=True)
            print(f"Auto measure error: {e}")
        finally:
            self.setVisible(True)
            self.update()

    def save_snapshot(self):
        """
        Saves the CURRENT drawings AND Calibration to the Analysis Bucket.
        """
        current_tf = self.current_timeframe
        
        snapshot = []
        for d in self.drawings:
            # Save prices AND coords
            new_d = {
                'price_a': d.get('price_a', 0.0),
                'price_b': d.get('price_b', 0.0),
                'timeframe': current_tf,
                'start_x': d['start'].x(),
                'start_y': d['start'].y(),
                'end_x': d['end'].x(),
                'end_y': d['end'].y()
            }
            snapshot.append(new_d)
            
        data_packet = {
            'drawings': snapshot,
            'calibration': self.global_calibration
        }

        self.analysis_data[current_tf] = data_packet
        self.save_analysis_data_to_file()
        
        # Feedback
        print(f"SNAPSHOT SAVED: {len(snapshot)} drawings + Calibration saved to '{current_tf}' slot.")

    def load_analysis_data(self):
        import json
        import os
        try:
            if os.path.exists("analysis_data.json"):
                with open("analysis_data.json", "r", encoding='utf-8') as f:
                    data = json.load(f)
                    # Merge with default structure to ensure all keys exist
                    for key in self.analysis_data:
                        if key in data:
                            self.analysis_data[key] = data[key]
                    print("Loaded analysis data from file.")
        except Exception as e:
            print(f"Error loading analysis data: {e}")

    def save_analysis_data_to_file(self):
        import json
        try:
            with open("analysis_data.json", "w", encoding='utf-8') as f:
                json.dump(self.analysis_data, f, indent=4, ensure_ascii=False)
            print("Saved analysis data to file.")
        except Exception as e:
             print(f"Error saving analysis data: {e}")

    def set_line_color(self, color):
        """
        Updates the global line colors for this session.
        Respects style (dotted/solid) but overrides color.
        """
        self.styles['default']['color'] = color
        self.styles['highlight']['color'] = color
        self.styles['measurement']['color'] = color
        # All lines means ALL lines, including the vertical measure and A/B horizontal lines
        self.update()

    def set_tool(self, tool_name):
        print(f"Overlay.set_tool called with: {tool_name}")
        self.current_tool = tool_name
        self.dragging_handle = None 
        
        # User requested to REMOVE auto-calibration on K-line selection to avoid lag used AND BAD DATA.
        # Calibration should only happen via "Auto" button (ocr_selection).
        # User requested Auto-Calibration for "Fluency".
        # Enable for K-Line and Single tools.
        if tool_name in ["k线", "单", "ocr_selection"]:
             self.auto_calibrate_axis()

        # 框选周期模式不需要做价格轴校准
        if tool_name:
            self.setCursor(Qt.CrossCursor)
            self.setAttribute(Qt.WA_TransparentForMouseEvents, False)
            print("Overlay active.")
            self.raise_()
            self.activateWindow()
        else:
            self.setCursor(Qt.ArrowCursor)
            # When idle, default to transparent so user can click through
            # The poll_timer will handle enabling events if over a handle
            self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            self.update()
        self.update()
        self.repaint() 
        print(f"Debug: Overlay Geometry: {self.geometry()}")

    def auto_calibrate_axis(self):
        """
        Automatically captures the Left AND Right strips of the screen
        and picks the one that looks like a valid Price Axis (consistent, sparse).
        NON-BLOCKING: Uses CalibrationWorker (QThread).
        """
        import logging
        try:
             screen = QGuiApplication.primaryScreen()
             if not screen: return
             
             geo = screen.geometry()
             w_total = geo.width()
             h_total = geo.height()
             
             # Define scan candidates
             # Prioritize Left as user reported data is there.
             candidates = [
                 {'name': 'Left', 'x': 0, 'y': 0, 'w': 150, 'h': h_total},
                 {'name': 'Right', 'x': w_total - 200, 'y': 0, 'w': 200, 'h': h_total}
             ]
             
             was_visible = self.isVisible()
             if was_visible:
                 self.setVisible(False)
                 # FORCE UI update to ensure overlay is gone before grab
                 QApplication.processEvents() 
             
             captured_paths = []
             
             for cand in candidates:
                 # Capture on Main Thread (Required)
                 pixmap = screen.grabWindow(0, cand['x'], cand['y'], cand['w'], cand['h'])
                 temp_path = f"temp_ocr_{cand['name']}.png"
                 pixmap.save(temp_path)
                 captured_paths.append(temp_path)
                     
             if was_visible:
                 self.setVisible(True)
                 QApplication.processEvents() # Restore UI immediately

             # Start Async Worker
             # Cleanup existing worker if running
             if hasattr(self, 'calib_worker') and self.calib_worker is not None:
                 if self.calib_worker.isRunning():
                     logging.info("Terminating previous calibration worker...")
                     self.calib_worker.terminate()
                     self.calib_worker.wait()
                 self.calib_worker.deleteLater()
             
             self.calib_worker = CalibrationWorker(self.ocr_helper, captured_paths)
             self.calib_worker.finished.connect(self.on_calibration_finished)
             self.calib_worker.start()
             
             logging.info("Started Calibration Worker in background...")
             
        except Exception as e:
             logging.error(f"Auto-Calibration Error: {e}", exc_info=True)

    def on_calibration_finished(self, result):
        import logging
        if result:
             screen = QGuiApplication.primaryScreen()
             dpr = screen.devicePixelRatio()
             
             # result['ref_y_local'] is physical Y relative to top of screen (since we crop from y=0)
             ref_y_local_physical = result['ref_y_local']
             ref_y_logical = ref_y_local_physical / dpr
             
             self.global_calibration = {
                 'scale': result['scale'] * dpr, 
                 'ref_y': ref_y_logical,
                 'ref_price': result['ref_price']
             }
             logging.info(f"ASYNC AUTO-CALIBRATION SUCCESS: {self.global_calibration}")
             print(f"Async Calibration Complete. Scale={self.global_calibration['scale']:.4f}")
        else:
             logging.warning("Async Auto-Calibration returned no result (Dense or No Text).")
        
        # Cleanup worker
        self.calib_worker.deleteLater()
        self.calib_worker = None

    def check_mouse_hover(self):
        # If we are drawing or have an active tool, don't interfere
        if self.current_tool:
            return
            
        # If we are currently dragging, we must ensure we catch events
        if self.dragging_handle:
             self.setAttribute(Qt.WA_TransparentForMouseEvents, False)
             return
        

        # Poll cursor position
        global_pos = QCursor.pos()
        local_pos = self.mapFromGlobal(global_pos)
        
        hit = self.hit_test(local_pos)
        
        if hit:
            # We are over a handle. Enable mouse events to allow clicking.
            if self.testAttribute(Qt.WA_TransparentForMouseEvents):
                 self.setAttribute(Qt.WA_TransparentForMouseEvents, False)
                 self.setCursor(Qt.SizeVerCursor)
                 self.hover_handle = hit
                 self.update()
        else:
            # We are NOT over a handle. Make transparent to clicks.
            if not self.testAttribute(Qt.WA_TransparentForMouseEvents):
                 self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
                 self.setCursor(Qt.ArrowCursor)
                 self.hover_handle = None
                 self.update()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
             print("Emergency Escape Triggered")
             self.set_tool(None)
             self.update()

    def mousePressEvent(self, event):
        if self.current_tool and event.button() == Qt.RightButton:
            # Right click cancels the tool
            self.start_point = None
            self.end_point = None
            self.is_drawing = False
            self.set_tool(None)
            self.update()
            return

        # IDLE MODE (Editing)
        if not self.current_tool:
            if event.button() == Qt.LeftButton:
                hit = self.hit_test(event.pos())
                if hit:
                    self.dragging_handle = hit
                    self.setAttribute(Qt.WA_TransparentForMouseEvents, False) # Lock focus
                    self.update()
            elif event.button() == Qt.RightButton:
                # Context menu for existing drawings
                hit = self.hit_test(event.pos())
                if hit:
                    print(f"Right clicked handle: {hit}")
                    self.context_menu(hit[0], event.globalPosition().toPoint())
                    
            return
            
        # DRAWING MODE
        if event.button() == Qt.LeftButton:
            print(f"DEBUG: MousePress - Tool: {self.current_tool}, FastMode: {self.fast_mode}, Pos: {event.pos()}")

            # 框选周期窗格：开始拖一个矩形
            if self.current_tool == "define_region":
                self.start_point = event.pos()
                self.end_point = event.pos()
                self.is_drawing = True
                return

            # 选周期：点击某个已框选的窗格，把它设为当前周期
            if self.current_tool == "select_region":
                region = self.region_manager.set_active_by_point(event.pos())
                if region:
                    print(f"[Region] 已选中当前周期: {region.name}")
                self.set_tool(None)
                self.update()
                return

            # K 线工具：点击 K 线即自动识别最高/最低点并测量
            # 用户的核心流程是“点 K 按钮 -> 点 K 线 -> 自动测量”，
            # 不再要求先打开“快”模式，因此这里只要工具是 k线就触发自动测量。
            if self.current_tool == "k线":
                print("DEBUG: Triggering auto_measure on K-Line tool")
                # Use global position for screen analysis to avoid local coord issues
                self.auto_measure(event.globalPosition().toPoint())
                return
            # 影线模式：点击任意 K 线自动识别最高点(A)和最低点(B)
            if self.current_tool == "影":
                print("DEBUG: Triggering auto_measure in wick_mode")
                self.auto_measure(event.globalPosition().toPoint(), wick_mode=True)
                return

            self.start_point = event.pos()
            self.end_point = event.pos()
            self.end_point = event.pos()
            self.is_drawing = True
            
            if self.current_tool == "free_draw":
                 self.current_free_drawing = [event.pos()]


    def context_menu(self, drawing_index, pos):
        MENU_STYLE = """
            QMenu {
                background-color: #F0F0F0; /* Light gray/white standard Windows look */
                border: 1px solid #A0A0A0;
                color: black;
            }
            QMenu::item {
                padding: 5px 20px;
                background-color: transparent;
            }
            QMenu::item:selected {
                background-color: #90C8F6; /* Standard highlight blue */
                color: black;
            }
        """
        
        menu = QMenu(self)
        menu.setStyleSheet(MENU_STYLE)
        
        set_price_action = QAction("设置价格 (Set Prices)", self)
        set_price_action.triggered.connect(lambda: self.input_prices(drawing_index))
        menu.addAction(set_price_action)

        # Presets Submenu
        presets_menu = QMenu("价格预设 (Presets)", self)
        presets_menu.setStyleSheet(MENU_STYLE) # Explicitly apply style to submenu
        menu.addMenu(presets_menu)

        # Save Action
        save_preset_action = QAction("保存当前为预设... (Save as Preset)", self)
        save_preset_action.triggered.connect(lambda: self.save_current_as_preset(drawing_index))
        presets_menu.addAction(save_preset_action)
        
        presets_menu.addSeparator()

        # Load Actions
        presets = self.preset_manager.get_presets()
        if not presets:
            no_presets_action = QAction("(无预设 No Presets)", self)
            no_presets_action.setEnabled(False)
            presets_menu.addAction(no_presets_action)
        else:
            # Overwrite Section
            overwrite_menu = QMenu("覆盖预设 (Overwrite Existing)", self)
            overwrite_menu.setStyleSheet(MENU_STYLE)
            presets_menu.addMenu(overwrite_menu)
            
            for name in presets.keys():
                ov_action = QAction(f"覆盖: {name} (Overwrite)", self)
                ov_action.triggered.connect(lambda checked=False, n=name: self.overwrite_preset(drawing_index, n))
                overwrite_menu.addAction(ov_action)
                
            presets_menu.addSeparator()

            # Load Actions
            for name, data in presets.items():
                load_action = QAction(f"加载: {name}", self)
                # Capture name and data in lambda
                load_action.triggered.connect(lambda checked=False, n=name, d=data: self.load_preset(drawing_index, n, d))
                presets_menu.addAction(load_action)
        
        # Add delete action for convenience
        delete_action = QAction("删除 (Delete)", self)
        delete_action.triggered.connect(lambda: self.delete_drawing(drawing_index))
        menu.addAction(delete_action)
        
        menu.exec_(pos)

    def save_current_as_preset(self, index):
        drawing = self.drawings[index]
        price_a = drawing.get('price_a', 0.0)
        price_b = drawing.get('price_b', 0.0)
        
        if price_a == 0 and price_b == 0:
             # Maybe warn? But user might want to save placeholders.
             pass

        name, ok = QInputDialog.getText(self, "保存预设", "预设名称 (Preset Name):")
        if ok and name:
            # Save calibration too!
            self.preset_manager.save_preset(name, price_a, price_b, self.global_calibration)
            print(f"Saved preset: {name} (with calibration)")

    def overwrite_preset(self, index, name):
        """
        Directly overwrites the preset 'name' with current drawing values, skipping the dialog.
        """
        drawing = self.drawings[index]
        price_a = drawing.get('price_a', 0.0)
        price_b = drawing.get('price_b', 0.0)
        
        self.preset_manager.save_preset(name, price_a, price_b, self.global_calibration)
        print(f"OVERWRITE SUCCESS: Preset '{name}' updated with A={price_a}, B={price_b}")

    def load_preset(self, index, name, data):
        import logging
        drawing = self.drawings[index]
        drawing['price_a'] = data['a']
        drawing['price_b'] = data['b']
        
        # Restore Calibration if exists
        preset_cal = data.get('calibration')
        
        if preset_cal:
             self.global_calibration = preset_cal
             logging.info(f"Restored Global Calibration from Preset '{name}': {self.global_calibration}")
             
             # Update Visual Position of the line (Start/End Y) to match new Prices 
             # using the valid calibration.
             if self.global_calibration:
                 try:
                     scale = self.global_calibration['scale']
                     ref_y = self.global_calibration['ref_y']
                     ref_price = self.global_calibration['ref_price']
                     
                     # Y = RefY + (Price - RefPrice) / Scale
                     new_start_y = ref_y + (drawing['price_a'] - ref_price) / scale
                     new_end_y = ref_y + (drawing['price_b'] - ref_price) / scale
                     
                     # Update points (Keep X, update Y)
                     drawing['start'].setY(int(new_start_y))
                     drawing['end'].setY(int(new_end_y))
                     logging.info(f"Realigned drawing to Y: {int(new_start_y)}, {int(new_end_y)}")
                 except Exception as e:
                     logging.error(f"Error realigning drawing: {e}")
                     print(f"Error realigning drawing: {e}")
        else:
             # Legacy/Price-Only Preset
             # The user is applying Prices to an EXISTING line.
             # We should use this line to ESTABLISH the calibration.
             logging.info("Preset has no calibration. keying off existing line geometry.")
             self.update_price_scale(drawing)
        
        self.update()
        print(f"Loaded preset: {name}")

    def delete_drawing(self, index):
        if 0 <= index < len(self.drawings):
            had_region = bool(self.drawings[index].get('region_id'))
            del self.drawings[index]
            if had_region:
                self.save_region_drawings()
            self.update()

    def input_prices(self, index):
        drawing = self.drawings[index]
        
        # Get Price A
        price_a, ok1 = QInputDialog.getDouble(self, "输入价格", "A线价格 (Price A):", 
                                             value=drawing.get('price_a', 0.0), decimals=2)
        if not ok1: return
        
        # Get Price B
        price_b, ok2 = QInputDialog.getDouble(self, "输入价格", "B线价格 (Price B):", 
                                             value=drawing.get('price_b', 0.0), decimals=2)
        if not ok2: return
        
        drawing['price_a'] = price_a
        drawing['price_b'] = price_b
        self.update()



    def mouseMoveEvent(self, event):
        # DRAWING MODE
        if self.is_drawing:
            self.end_point = event.pos()

            # 框选周期窗格：实时显示矩形
            if self.current_tool == "define_region":
                self.update()
                return

            if self.current_tool == "free_draw":
                self.current_free_drawing.append(event.pos())
                self.update()
                return

            if self.current_tool == "单":
                self.end_point.setX(self.start_point.x())
            self.update()
            return

        # IDLE MODE (Editing)
        if not self.current_tool:
            if self.dragging_handle:
                idx, htype = self.dragging_handle
                drawing = self.drawings[idx]
                
                new_pos = event.pos()
                
                # Dynamic Price Update
                scale = drawing.get('scale', 0.0)
                if scale != 0:
                    old_y = drawing[htype].y()
                    new_y = new_pos.y()
                    dy = new_y - old_y
                    
                    if htype == 'start':
                         drawing['price_a'] += dy * scale
                    elif htype == 'end':
                         drawing['price_b'] += dy * scale
                
                if drawing['type'] == 'boshen_single':
                    other_handle_type = 'end' if htype == 'start' else 'start'
                    other_pos = drawing[other_handle_type]
                    new_pos.setX(other_pos.x())
                
                drawing[htype] = new_pos
                self.update()
                return

    def mouseReleaseEvent(self, event):
        print(f"DEBUG: MouseRelease - is_drawing: {self.is_drawing}")

        # 拖动调整某条测量线结束：若它属于某个周期窗格，保存最新位置
        if self.dragging_handle and not self.current_tool:
            idx, _ = self.dragging_handle
            self.dragging_handle = None
            if 0 <= idx < len(self.drawings) and self.drawings[idx].get('region_id'):
                self.save_region_drawings()
            self.update()
            return

        if self.current_tool == "free_draw" and self.is_drawing:
            if self.current_free_drawing:
                # Store a copy of the points
                self.free_drawings.append(list(self.current_free_drawing))
                self.current_free_drawing = []
            
            self.is_drawing = False
            self.start_point = None
            self.end_point = None
            self.update()
            # Do NOT reset tool to None. Keep drawing.
            return

        # 框选周期窗格：松开鼠标即完成区域定义
        if self.current_tool == "define_region" and self.is_drawing:
            self.is_drawing = False
            if self.start_point and self.end_point:
                rect = QRect(self.start_point, self.end_point).normalized()
                self.start_point = None
                self.end_point = None
                self.finish_define_region(rect)
            else:
                self.start_point = None
                self.end_point = None
                self.set_tool(None)
            self.update()
            return

        if self.is_drawing and self.start_point and self.end_point:
            self.is_drawing = False
            
            try:
                # Commit drawing
                if self.current_tool == "单": 
                     new_drawing = {
                         'type': 'boshen_single',
                         'start': self.start_point,
                         'end': self.end_point,
                         'timeframe': self.current_timeframe # Tag
                     }
                     # Apply global calibration
                     self.apply_calibration(new_drawing)
                     
                     self.drawings.append(new_drawing)

                # OCR Selection Mode
                elif self.current_tool == "ocr_selection":
                    # 1. Normalize Rect
                    x = min(self.start_point.x(), self.end_point.x())
                    y = min(self.start_point.y(), self.end_point.y())
                    w = abs(self.end_point.x() - self.start_point.x())
                    h = abs(self.end_point.y() - self.start_point.y())
                    
                    if w > 10 and h > 10:
                        # 2. Capture Screen Area
                        self.setVisible(False)
                        QApplication.processEvents()
                        
                        screen = QGuiApplication.primaryScreen()
                        dpr = screen.devicePixelRatio()
                        
                        # capture pixels (physical)
                        pixmap = screen.grabWindow(0, int(x*dpr), int(y*dpr), int(w*dpr), int(h*dpr))
                        
                        self.setVisible(True)
                        
                        # 3. Save temp for OCR
                        temp_path = "temp_ocr_axis.png"
                        pixmap.save(temp_path)
                        
                        # 4. Analyze
                        result = self.ocr_helper.analyze_axis(temp_path)
                        
                        if result:
                             ref_y_local_physical = result['ref_y_local']
                             ref_y_global_physical = (y * dpr) + ref_y_local_physical
                             
                             # Convert back to logical for our QT app
                             ref_y_logical = ref_y_global_physical / dpr
                             
                             self.global_calibration = {
                                 'scale': result['scale'] * dpr,
                                 'ref_y': ref_y_logical,
                                 'ref_price': result['ref_price']
                             }
                             
                             print(f"AUTO CALIBRATION SUCCESS: {self.global_calibration}")
                             QApplication.setOverrideCursor(Qt.WaitCursor) 
                             # Maybe flash a message?
                             QApplication.restoreOverrideCursor()
                        else:
                            print("Auto Calibration Failed.")

            except Exception as e:
                logging.error(f"Error during drawing commit: {e}", exc_info=True)
                print(f"Error during drawing commit: {e}")
            finally:
                # IMPORTANT: Always exit tool mode to prevent screen lock
                if self.current_tool in ["单", "ocr_selection"]:
                    self.set_tool(None)
            
            self.start_point = None
            self.end_point = None
            self.update()
            return
    def hit_test(self, pos):
        """
        Returns (index, handle_type) if hit, else None.
        """
        threshold = 15 # Slightly larger radius for easier hitting
        for i, d in enumerate(self.drawings):
            # 带 region_id 的测量线永远可命中编辑（多周期同时存在）；
            # 没有 region_id 的旧临时线仍按 current_timeframe 过滤。
            if not d.get('region_id'):
                if d.get('timeframe') and d.get('timeframe') != self.current_timeframe:
                    continue

            # Check Start
            if (d['start'] - pos).manhattanLength() < threshold:
                return (i, 'start')
            # Check End
            if (d['end'] - pos).manhattanLength() < threshold:
                return (i, 'end')
        return None

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        
        # Only block screen visually if using a tool
        if self.current_tool:
             painter.fillRect(self.rect(), QColor(255, 255, 255, 1))

        # 先画各周期窗格的边框 + 名字（让用户看清楚有哪些窗格、哪个是激活的）
        self.draw_regions(painter)

        # Draw all committed drawings
        # 规则：
        #  - 带 region_id 的测量线 => 永远显示（多周期同时锁定保持），
        #    并且 A/B 线与波神线只在所属窗格的矩形宽度内绘制，互不干扰。
        #  - 没有 region_id 的旧临时线 => 仍按 current_timeframe 过滤（向后兼容）。
        for i, d in enumerate(self.drawings):
            region_id = d.get('region_id')
            if region_id:
                region = self.region_manager.get_region(region_id)
                if region is None:
                    continue  # 区域已被删除，跳过
                self.draw_item(painter, d,
                               is_hovering=(self.hover_handle and self.hover_handle[0] == i),
                               clip_region=region)
            else:
                tf = d.get('timeframe', self.current_timeframe)
                if tf == self.current_timeframe:
                    self.draw_item(painter, d,
                                   is_hovering=(self.hover_handle and self.hover_handle[0] == i))

        # 框选周期窗格时，实时显示蓝色矩形
        if self.current_tool == "define_region" and self.is_drawing and self.start_point and self.end_point:
            rect = QRect(self.start_point, self.end_point).normalized()
            painter.setPen(QPen(QColor(0, 120, 255), 2, Qt.DashLine))
            painter.setBrush(QColor(0, 120, 255, 40))
            painter.drawRect(rect)
            painter.setBrush(Qt.NoBrush)

        if self.is_drawing and self.start_point and self.end_point and self.current_tool not in ("free_draw", "define_region"):
            if self.current_tool == "ocr_selection":
                 # Draw Selection Rect
                 rect = QRect(self.start_point, self.end_point).normalized()
                 painter.setPen(QPen(QColor(0, 120, 255), 2, Qt.DashLine))
                 painter.setBrush(QColor(0, 120, 255, 50))
                 painter.drawRect(rect)
            else:
                temp_drawing = {
                    'type': 'boshen_single' if self.current_tool == "单" else 'line',
                    'start': self.start_point,
                    'end': self.end_point
                }
                self.draw_item(painter, temp_drawing)

        # Draw Free Drawings
        painter.setPen(QPen(self.free_draw_color, self.free_draw_width, Qt.SolidLine))
        for stroke in self.free_drawings:
            if len(stroke) > 1:
                painter.drawPolyline(stroke)
        
        # Draw current free drawing
        if self.current_free_drawing and len(self.current_free_drawing) > 1:
             painter.drawPolyline(self.current_free_drawing)


        # Draw Instruction Text for OCR Mode
        if self.current_tool == "ocr_selection" and not self.is_drawing:
             font = QFont("SimHei", 20, QFont.Bold)
             painter.setFont(font)
             painter.setPen(QColor(255, 0, 0))
             text = "请按住鼠标左键，框选【价格坐标轴】的数字区域\nSelect the Price Axis Numbers"
             
             # Draw text in center-top
             rect = self.rect()
             painter.drawText(rect, Qt.AlignCenter, text)

    def draw_regions(self, painter):
        """绘制每个周期窗格的边框与名字标签，激活窗格高亮。"""
        active_id = self.region_manager.active_region_id
        for region in self.region_manager.regions:
            rect = region.rect
            is_active = (region.id == active_id)
            if is_active:
                # 激活窗格：醒目的蓝色实线边框
                pen = QPen(QColor(0, 120, 255), 2, Qt.SolidLine)
            else:
                # 其它窗格：淡灰色虚线边框
                pen = QPen(QColor(120, 120, 120, 160), 1, Qt.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(rect)

            # 名字标签（左上角小标牌）
            label = region.name + ("  [当前]" if is_active else "")
            painter.setFont(QFont("SimHei", 10, QFont.Bold))
            metrics = painter.fontMetrics()
            tw = metrics.horizontalAdvance(label) + 10
            th = metrics.height() + 4
            tag_rect = QRect(rect.left(), rect.top(), tw, th)
            bg = QColor(0, 120, 255, 200) if is_active else QColor(120, 120, 120, 160)
            painter.fillRect(tag_rect, bg)
            painter.setPen(QColor(255, 255, 255))
            painter.drawText(tag_rect, Qt.AlignCenter, label)

    def draw_item(self, painter, data, is_hovering=False, clip_region=None):
        start = data['start']
        end = data['end']
        dtype = data['type']

        # 若指定了所属窗格，则水平线只在窗格矩形宽度内绘制，
        # 这样不同周期的测量线不会横穿整个屏幕、互相干扰。
        if clip_region is not None:
            line_x_left = clip_region.rect.left()
            line_x_right = clip_region.rect.right()
        else:
            line_x_left = 0
            line_x_right = self.width()

        if dtype == 'boshen_single':
            # Draw the measurement line
            painter.setPen(QPen(self.styles['measurement']['color'], self.styles['measurement']['width'], self.styles['measurement']['style']))
            painter.drawLine(start, end)
            
            # --- Draw A/B horizontal lines ---
            screen_width = self.width()
            
            # Line A
            painter.drawLine(line_x_left, start.y(), line_x_right, start.y())
            
            label_a = "1 (a)"
            if 'price_a' in data:
                label_a += f"  {data['price_a']:.2f}"
            painter.drawText(start.x() + 10, start.y() - 5, label_a)
            
            # Line B
            painter.drawLine(line_x_left, end.y(), line_x_right, end.y())
            
            label_b = "1 (b)"
            if 'price_b' in data:
                label_b += f"  {data['price_b']:.2f}"
            painter.drawText(end.x() + 10, end.y() - 5, label_b)
            
            # Draw Handles
            handle_size = 10 if is_hovering else 8
            r_start = QRect(start.x() - handle_size//2, start.y() - handle_size//2, handle_size, handle_size)
            r_end = QRect(end.x() - handle_size//2, end.y() - handle_size//2, handle_size, handle_size)
            
            painter.fillRect(r_start, self.styles['handle_fill'])
            painter.fillRect(r_end, self.styles['handle_fill'])
            painter.drawRect(r_start)
            painter.drawRect(r_end)

            # Calculate levels
            levels = BoshenAlgorithms.calculate_levels(start.y(), end.y())
            
            # Calculate Price Levels if available
            price_levels = None
            if 'price_a' in data and 'price_b' in data:
                price_levels = BoshenAlgorithms.calculate_levels(data['price_a'], data['price_b'])

            highlight_indices = [2, 5, 7] # 3, 6, 8 lines (0-based: 2, 5, 7)
            
            for i, (ratio, y_pos) in enumerate(levels):
                y = int(y_pos)
                
                if i in highlight_indices:
                    s = self.styles['highlight']
                    painter.setPen(QPen(s['color'], s['width'], s['style']))
                else:
                    s = self.styles['default']
                    painter.setPen(QPen(s['color'], s['width'], s['style']))
                
                painter.drawLine(line_x_left, y, line_x_right, y)
                label = f"-1 ({i+1})"
                
                if price_levels:
                    # price_levels should correspond index-wise since ratios are same
                    # But verifying list length match is safer, though they come from same config
                    if i < len(price_levels):
                        p_ratio, price_val = price_levels[i]
                        label += f"  {price_val:.2f}"
                        
                painter.drawText(end.x() + 5, y - 2, label)
                
        elif dtype == 'line':
            painter.setPen(QPen(Qt.black, 1))
            painter.drawLine(start, end)

    def update_price_scale(self, drawing):
        """
        Updates the global calibration based on the prices and coordinates of the given drawing.
        """
        import logging
        if 'price_a' not in drawing or 'price_b' not in drawing:
            return
            
        p_a = drawing.get('price_a', 0.0)
        p_b = drawing.get('price_b', 0.0)
        
        if p_a == 0 and p_b == 0:
            return
            
        y_a = drawing['start'].y()
        y_b = drawing['end'].y()
        
        dy = y_b - y_a
        dp = p_b - p_a
        
        if abs(dy) < 5 or abs(dp) < 0.0001:
            return
            
        # Scale = Price / Pixels
        scale = dp / dy
        
        # Update drawing specific scale
        drawing['scale'] = scale
        
        # Update GLOBAL calibration
        # We use point A as reference
        # ref_y is LOGICAL Y
        self.global_calibration = {
            'scale': scale,
            'ref_y': y_a,
            'ref_price': p_a
        }
        logging.info(f"Updated Global Calibration from Manual Input: {self.global_calibration}")
        print(f"Global Calibration Updated: Scale={scale:.6f}, Ref={p_a}@{y_a}")
        
    def apply_calibration(self, drawing):
        """
        Applies global calibration to a NEW drawing to auto-calculate prices.
        """
        import logging
        if not self.global_calibration:
            return
            
        scale = self.global_calibration['scale']
        ref_y = self.global_calibration['ref_y']
        ref_price = self.global_calibration['ref_price']
        
        y_a = drawing['start'].y()
        y_b = drawing['end'].y()
        
        # Calculate Price = RefPrice + (Y - RefY) * Scale
        # Note: Y increases downwards.
        # If Scale is negative (Normal price axis), Price decreases as Y increases.
        
        price_a = ref_price + (y_a - ref_y) * scale
        price_b = ref_price + (y_b - ref_y) * scale
        
        drawing['price_a'] = price_a
        drawing['price_b'] = price_b
        drawing['scale'] = scale
        
        logging.info(f"Applied calibration to new drawing. PA={price_a}, PB={price_b}")
        print(f"DEBUG: Applied calibration to new drawing. PA={price_a}, PB={price_b}")

    def clear_all(self):
        """旧的“Dx”按钮：清空当前屏幕上的全部临时绘制（不含已锁定的周期测量）。

        为了向后兼容，这里仍然清掉 self.drawings 里没有 region_id 的临时线
        以及自由绘制；带 region_id 的周期测量线请用 clear_all_regions() 删除。
        """
        # 只清掉“未归属任何周期窗格”的临时线，保留各周期锁定的测量
        self.drawings = [d for d in self.drawings if d.get('region_id')]
        self.free_drawings.clear() # Clear free drawings too
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.update()

    # ==================================================================
    # 多周期窗格（Region）相关方法
    # ==================================================================
    def start_define_region(self):
        """进入“框选周期”模式：用户拖一个矩形框出某个周期窗格。"""
        self.defining_region = True
        self.set_tool("define_region")

    def finish_define_region(self, rect):
        """框选结束，新增一个区域并设为激活区域。"""
        from PySide6.QtWidgets import QInputDialog
        # 防止误触：太小的框忽略
        if rect.width() < 30 or rect.height() < 30:
            self.defining_region = False
            self.set_tool(None)
            return

        # 让用户给这个周期窗格起个名字（可留空自动命名）
        default_name = f"周期{len(self.region_manager.regions) + 1}"
        name, ok = QInputDialog.getText(
            self, "命名周期窗格",
            "请输入该窗格的周期名称（如 周线/日线/2小时/30分/5分/3分）：",
            text=default_name
        )
        if not ok:
            self.defining_region = False
            self.set_tool(None)
            return

        region = self.region_manager.add_region(rect, name=name.strip() or default_name)
        print(f"[Region] 新增周期窗格: {region.name} @ {region.rect}")
        self.defining_region = False
        self.set_tool(None)
        self.update()

    def get_active_region(self):
        return self.region_manager.get_active_region()

    def set_active_region_by_point(self, global_point):
        """根据屏幕坐标激活对应的周期窗格。"""
        local = self.mapFromGlobal(global_point)
        region = self.region_manager.set_active_by_point(local)
        if region:
            print(f"[Region] 激活周期窗格: {region.name}")
        self.update()
        return region

    def clear_active_region_measurements(self):
        """删除“当前激活周期窗格”里的全部测量线，可重新测量。"""
        active = self.region_manager.get_active_region()
        if not active:
            print("[Region] 没有激活的周期窗格，无法删除当前周期测量")
            return
        before = len(self.drawings)
        self.drawings = [d for d in self.drawings if d.get('region_id') != active.id]
        removed = before - len(self.drawings)
        print(f"[Region] 删除周期 '{active.name}' 的测量线 {removed} 条")
        self.save_region_drawings()
        self.update()

    def clear_all_region_measurements(self):
        """删除所有周期窗格里的全部测量线。"""
        before = len(self.drawings)
        self.drawings = [d for d in self.drawings if not d.get('region_id')]
        removed = before - len(self.drawings)
        print(f"[Region] 删除全部周期测量线 {removed} 条")
        self.save_region_drawings()
        self.update()

    def clear_all_regions(self):
        """彻底删除所有周期窗格定义 + 其测量线（重置多周期布局）。"""
        self.region_manager.clear_regions()
        self.drawings = [d for d in self.drawings if not d.get('region_id')]
        self.save_region_drawings()
        self.update()

    # ---- 周期测量线的持久化 ----
    def save_region_drawings(self, filename="region_drawings.json"):
        """把带 region_id 的测量线保存到文件，重启后可恢复。"""
        import json
        out = []
        for d in self.drawings:
            if not d.get('region_id'):
                continue
            out.append({
                'region_id': d['region_id'],
                'type': d.get('type', 'boshen_single'),
                'start_x': d['start'].x(),
                'start_y': d['start'].y(),
                'end_x': d['end'].x(),
                'end_y': d['end'].y(),
                'price_a': d.get('price_a', 0.0),
                'price_b': d.get('price_b', 0.0),
                'scale': d.get('scale', 0.0),
            })
        try:
            with open(filename, "w", encoding="utf-8") as f:
                json.dump(out, f, ensure_ascii=False, indent=4)
        except Exception as e:
            print(f"[Region] save_region_drawings error: {e}")

    def load_region_drawings(self, filename="region_drawings.json"):
        """启动时从文件恢复各周期窗格的测量线。"""
        import json
        import os
        if not os.path.exists(filename):
            return
        try:
            with open(filename, "r", encoding="utf-8") as f:
                items = json.load(f)
            valid_ids = {r.id for r in self.region_manager.regions}
            for it in items:
                rid = it.get('region_id')
                # 只恢复仍然存在的区域的测量线
                if rid not in valid_ids:
                    continue
                d = {
                    'type': it.get('type', 'boshen_single'),
                    'start': QPoint(int(it['start_x']), int(it['start_y'])),
                    'end': QPoint(int(it['end_x']), int(it['end_y'])),
                    'price_a': it.get('price_a', 0.0),
                    'price_b': it.get('price_b', 0.0),
                    'scale': it.get('scale', 0.0),
                    'region_id': rid,
                }
                self.drawings.append(d)
            print(f"[Region] 恢复周期测量线 {len(self.drawings)} 条")
        except Exception as e:
            print(f"[Region] load_region_drawings error: {e}")
