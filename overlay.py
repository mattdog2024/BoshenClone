from PySide6.QtWidgets import QWidget, QApplication, QInputDialog, QMenu, QMessageBox, QDialog, QVBoxLayout, QLabel, QPushButton, QScrollArea
from PySide6.QtCore import Qt, QPoint, QRect, QTimer, QThread, Signal, QObject
from PySide6.QtGui import QPainter, QPen, QColor, QFont, QCursor, QGuiApplication, QAction
from algorithms import BoshenAlgorithms
from preset_manager import PresetManager
from ocr_helper import BoshenOCR
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

        
        # Strategy UI Removed per user request
        self.current_timeframe = "日线" # Default to daily or None
        self.analysis_data = {
            '日线': [],
            '4小时': [],
            '1小时': []
        }
        self.load_analysis_data()
        
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
            color_counts = {}
            start_scan = max(0, click_y - 500)
            end_scan   = min(height, click_y + 100)

            # Sanity check scan range
            if start_scan >= end_scan:
                 start_scan = 0
                 end_scan = height
            
            # Step size 5
            for y in range(start_scan, end_scan, 5):
                pixel = image.pixelColor(x, y).rgb()
                color_counts[pixel] = color_counts.get(pixel, 0) + 1
            
            if not color_counts:
                logging.error("No pixels scanned")
                self.setVisible(True)
                return

            bg_color_rgb = max(color_counts, key=color_counts.get)
            bg_color = QColor(bg_color_rgb)
            logging.info(f"Detected BG Color: {bg_color.name()}")

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
            # 4. 新算法：连续像素段检测（不依赖颜色匹配，深色/浅色主题通用）
            # 原理：对点击列附近 ±scan_radius 列逐列扫描，
            #   将每列的非背景色像素分成连续段，
            #   最长的段 = K线实体，整体范围顶底 = 完整K线（含影线）
            # ================================================================
            scan_radius = 12
            x_start = max(0, x - scan_radius)
            x_end = min(image.width(), x + scan_radius + 1)
            
            # ================================================================
            # 新算法：连续像素段检测
            # 不依赖颜色匹配，深色/浅色主题通用
            # ================================================================
            
            def find_segments_in_col(col_x, y_start, y_end, gap_tol=8):
                """
                在指定列的 y_start~y_end 范围内，找出所有连续非背景色像素段。
                返回列表：[(seg_top, seg_bottom), ...]
                """
                segments = []
                in_seg = False
                seg_start = 0
                gap_count = 0
                last_valid = -1
                for y in range(y_start, y_end):
                    c = image.pixelColor(col_x, y)
                    is_non_bg = color_distance(c, bg_color) > 30
                    if is_non_bg:
                        if not in_seg:
                            in_seg = True
                            seg_start = y
                        last_valid = y
                        gap_count = 0
                    else:
                        if in_seg:
                            gap_count += 1
                            if gap_count > gap_tol:
                                segments.append((seg_start, last_valid))
                                in_seg = False
                                gap_count = 0
                if in_seg:
                    segments.append((seg_start, last_valid))
                return segments

            # 在 ±scan_radius 列范围内，对每列找连续段
            # 并找到包含点击点的段（或最靠近点击点的段）
            # 全局 top/bottom = 所有列中包含点击点的段的联合范围
            # 实体 top/bottom = 各列中最长段的联合范围

            all_full_top = None    # 完整K线范围（含影线）
            all_full_bottom = None
            all_body_top = None    # 实体范围（最长段）
            all_body_bottom = None
            found_any_candle = False

            # 工具栏底部 y 坐标（扫描不超过工具栏）
            scan_top_limit = toolbar_bottom_y if toolbar_bottom_y > 0 else 0

            for scan_x in range(x_start, x_end):
                segs = find_segments_in_col(
                    scan_x,
                    y_start=scan_top_limit,
                    y_end=min(height, click_y + 800),
                    gap_tol=12
                )
                if not segs:
                    continue

                # 只取包含点击点的段（允许 ±30px 偏差）
                # 不用所有段的联合，避免工具栏/价格轴等非K线像素混入
                target_seg = None
                for seg in segs:
                    if seg[0] - 20 <= click_y <= seg[1] + 20:
                        target_seg = seg
                        break
                # 如果没有包含点击点的段，跳过这列（不用最近段，避免误判）
                if target_seg is None:
                    continue

                # 实体：该列所有段中最长的段
                longest_seg = max(segs, key=lambda s: s[1] - s[0])
                # 但实体必须与 target_seg 相邻或重叠，否则用 target_seg 本身
                if abs(longest_seg[0] - target_seg[0]) > 200:
                    longest_seg = target_seg

                # full 范围 = target_seg（就是包含点击点的那根K线段）
                col_full_top    = target_seg[0]
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
                # 对 top_y~bottom_y 每一行，统计 x_start~x_end 内有多少列有非背景色像素
                row_widths = []
                for ry in range(top_y, bottom_y + 1):
                    w = 0
                    for rx in range(x_start, x_end):
                        c = image.pixelColor(rx, ry)
                        if color_distance(c, bg_color) > 30:
                            w += 1
                    row_widths.append((ry, w))
                # 找宽度 >= 2 的行（实体行）
                body_rows = [ry for (ry, w) in row_widths if w >= 2]
                if body_rows:
                    body_top_y    = body_rows[0]
                    body_bottom_y = body_rows[-1]
                    logging.info(f"Wick horizontal scan: body=({body_top_y},{body_bottom_y}), full=({top_y},{bottom_y})")
                else:
                    # 如果没有宽度>=2的行，回退到全范围
                    body_top_y    = top_y
                    body_bottom_y = bottom_y
                    logging.info(f"Wick horizontal scan: no body rows found, fallback to full")

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
                global_start_p = QPoint(pos.x(), int(wick_a_y))
                global_end_p   = QPoint(pos.x(), int(wick_b_y))
            else:
                # K线模式：A/B = 整根K线最高/最低，根据点击位置判断哪端是A
                global_start_p = QPoint(pos.x(), int(bottom_y_logical if dist_to_bottom < dist_to_top else top_y_logical))
                global_end_p = QPoint(pos.x(), int(top_y_logical if dist_to_bottom < dist_to_top else bottom_y_logical))
            
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
            
            # Apply global calibration if available
            self.apply_calibration(new_drawing)
            
            self.drawings.append(new_drawing)
            
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

    def apply_calibration(self, drawing):
        """
        Applies global calibration to a new drawing to auto-calculate prices.
        """
        if not self.global_calibration:
            return
            
        scale = self.global_calibration['scale']
        ref_y = self.global_calibration['ref_y']
        ref_price = self.global_calibration['ref_price']
        
        # Calculate Price A
        y_a = drawing['start'].y()
        drawing['price_a'] = ref_price + (y_a - ref_y) * scale
        
        # Calculate Price B
        y_b = drawing['end'].y()
        drawing['price_b'] = ref_price + (y_b - ref_y) * scale
        
        # Store scale
        drawing['scale'] = scale
        print(f"DEBUG: Applied calibration to new drawing. PA={drawing['price_a']}, PB={drawing['price_b']}")

        
        # Interaction state for editing
        self.dragging_handle = None # (drawing_index, handle_type) handle_type: 'start' or 'end'
        self.hover_handle = None    # (drawing_index, handle_type)

        # Style Config
        # Style Config
        self.styles = {
            'default': {'color': QColor(255, 0, 0), 'width': 1, 'style': Qt.DotLine}, 
            'highlight': {'color': QColor(255, 0, 127), 'width': 3, 'style': Qt.SolidLine}, # Solid, thick magenta
            'measurement': {'color': QColor(255, 0, 0), 'width': 1, 'style': Qt.SolidLine},
            'handle_fill': QColor(255, 0, 0, 100)
        }

        # Screen geometry
        self.setGeometry(QApplication.primaryScreen().geometry())
        
        # Transparent background
        self.setStyleSheet("background-color: transparent;")
        
        # Mouse tracking
        self.setMouseTracking(True)
        
        # Polling timer for interaction when transparent
        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self.check_mouse_hover)
        self.poll_timer.start(50) # Check every 50ms

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
            
            # Auto-measure moved to "k线" tool (when Fast Mode is ON)
            if self.current_tool == "k线" and self.fast_mode:
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
            del self.drawings[index]
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
        threshold = 15 # Slightly larger radius for easier hitting
        for i, d in enumerate(self.drawings):
            # Only hit-test visible drawings!
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

        # Draw all committed drawings
        for i, d in enumerate(self.drawings):
            # FILTER: Only draw if matches current timeframe (or if drawing has no timeframe for legacy)
            # Default to showing if no tag (backward compatibility)
            tf = d.get('timeframe', self.current_timeframe) 
            if tf == self.current_timeframe:
                 self.draw_item(painter, d, is_hovering=(self.hover_handle and self.hover_handle[0] == i))

        if self.is_drawing and self.start_point and self.end_point and self.current_tool != "free_draw":
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

    def draw_item(self, painter, data, is_hovering=False):
        start = data['start']
        end = data['end']
        dtype = data['type']

        if dtype == 'boshen_single':
            # Draw the measurement line
            painter.setPen(QPen(self.styles['measurement']['color'], self.styles['measurement']['width'], self.styles['measurement']['style']))
            painter.drawLine(start, end)
            
            # --- Draw A/B horizontal lines ---
            screen_width = self.width()
            
            # Line A
            painter.drawLine(0, start.y(), screen_width, start.y())
            
            label_a = "1 (a)"
            if 'price_a' in data:
                label_a += f"  {data['price_a']:.2f}"
            painter.drawText(start.x() + 10, start.y() - 5, label_a)
            
            # Line B
            painter.drawLine(0, end.y(), screen_width, end.y())
            
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
                
                painter.drawLine(0, y, screen_width, y)
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
        self.drawings.clear()
        self.free_drawings.clear() # Clear free drawings too
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.update()
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.update()
