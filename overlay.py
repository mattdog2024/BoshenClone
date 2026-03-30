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

    def auto_measure(self, pos):
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

            # 4. robust scan with gap tolerance and width scanning
            # We scan a small window around x to catch wicks.
            # For thin wicks (1-2px wide), we need to scan a wider band to guarantee
            # we hit the wick column even if the user clicks slightly off-center.
            scan_radius = 6
            x_start = max(0, x - scan_radius)
            x_end = min(image.width(), x + scan_radius + 1)
            
            global_top_y = float('inf')
            global_bottom_y = float('-inf')
            found_any_candle = False
            
            # First, determine TARGET COLOR from the click point (to avoid axis lines)
            # Search nearby pixels for the first non-BG color
            target_color = None
            
            # Search nearby pixels for the first non-BG color
            # Use a small box search to find color near click
            # Limit radius to 10 (21x21 box) to avoid finding far-away neighbors
            search_radius = 10
            for r in range(0, search_radius):
                # Simple box search around click for target color
                found = False
                # We check the box of size (2r+1).
                # To be efficient we *could* just check the perimeter, but for small r, full box is fine.
                for dy in range(-r, r + 1):
                    for dx in range(-r, r + 1):
                        # Optimize: only check outer shell if r > 0? No, small enough to verify all.
                        # Check bounds
                        cx = x + dx
                        cy = click_y + dy
                        if cx >= 0 and cx < image.width() and cy >= 0 and cy < height:
                            c = image.pixelColor(cx, cy)
                            if color_distance(c, bg_color) > 30:
                                target_color = c
                                found = True
                                break
                    if found: break
                if found: break
            
            if not target_color:
                logging.warning("Could not find any candle color near click.")
                target_color = QColor(Qt.black) # Fallback? likely fail
            else:
                logging.info(f"Target Candle Color: {target_color.name()}")

                # Pre-check if target is black
                tr, tg, tb = target_color.red(), target_color.green(), target_color.blue()
                # Relaxed threshold for target black detection
                target_is_black = (max(tr,tg,tb) - min(tr,tg,tb) < 30) and (max(tr,tg,tb) < 100)

                def matches_target(c):
                    rgb = c.red(), c.green(), c.blue()
                    saturation = max(rgb) - min(rgb)
                    brightness = max(rgb)
                    bg_r, bg_g, bg_b = bg_color.red(), bg_color.green(), bg_color.blue()

                    # Must be meaningfully different from background first.
                    # If the pixel is very close to background color, reject it
                    # regardless of brightness — this prevents the chart's bottom
                    # axis bar (which is slightly darker gray) from being accepted.
                    dist_from_bg = color_distance(c, bg_color)
                    if dist_from_bg < 30:
                        return False

                    # Rule 1: WICK pixels — dark AND clearly different from background.
                    # Wicks on light-theme charts are rendered as near-black lines.
                    # Accept if brightness is low AND the pixel is far from background.
                    if brightness < 120 and dist_from_bg > 50:
                        return True

                    # Rule 2: Grayscale mid-tone rejection (grid lines / text).
                    # Chart grid lines are typically RGB=(192,192,192): saturation=0,
                    # brightness=192.  The old threshold was 160, which MISSED these!
                    # A pixel that is gray (saturation<15) and not very dark (brightness>120)
                    # is almost certainly a grid line or axis label, NOT a candle.
                    # Raise threshold to 220 to cover all common grid line shades.
                    # (True wick pixels have brightness < 120, handled by Rule 1 above.)
                    if saturation < 15 and brightness >= 120:
                        return target_is_black

                    # Rule 3: Dominant-channel match for colored candle bodies.
                    tr, tg, tb = target_color.red(), target_color.green(), target_color.blue()
                    cr, cg, cb = c.red(), c.green(), c.blue()

                    t_dom = 'r' if tr > tg and tr > tb else ('g' if tg > tr and tg > tb else 'b')
                    c_dom = 'r' if cr > cg and cr > cb else ('g' if cg > cr and cg > cb else 'b')

                    if t_dom == c_dom:
                        return True

                    return False


            # gap_tolerance: how many consecutive background pixels to tolerate
            # before deciding the candle has ended.
            #
            # History of bugs caused by wrong values:
            #   - Too small (e.g. 8):  long wicks get cut off mid-wick.
            #   - Too large (e.g. 400): after the candle body ends, the scan keeps
            #     going through 400px of pure background all the way to the screen
            #     edge, and sets col_bottom_y = screen_bottom.  This makes the
            #     measured range span the entire chart height → A and B lines are
            #     placed at the wrong positions.
            #
            # Correct value: wicks are 1-2px wide, continuous, with NO background
            # gaps inside them.  The only gap we need to tolerate is the transition
            # from body to wick (a few anti-aliased pixels).  8px is sufficient.
            # For the rare case of a very long wick that has a 1-pixel gap due to
            # sub-pixel rendering, we use 12px as a safe margin.
            gap_tolerance = 12  # pixels of background gap before scan stops
            
            for scan_x in range(x_start, x_end):
                # Helper for this column
                def is_valid_pixel(y):
                     if y < 0 or y >= height: return False
                     c = image.pixelColor(scan_x, y)
                     # Must be non-bg AND match target color
                     is_non_bg = color_distance(c, bg_color) > 30
                     if not is_non_bg: return False
                     return matches_target(c)

                # Find Top
                col_top_y = click_y
                current_gap = 0
                for y in range(click_y, max(0, click_y - 800), -1):
                    if is_valid_pixel(y):
                        col_top_y = y
                        current_gap = 0
                    else:
                        current_gap += 1
                        if current_gap > gap_tolerance:
                            break

                # TOOLBAR BRIDGE SCAN (向上跨越工具栏)
                # Problem: When a candle's upper wick extends behind the app toolbar
                # (e.g. y=94~158), there is a ~65px gap of non-candle pixels that
                # exceeds gap_tolerance=12.  The scan stops at the toolbar bottom,
                # missing the wick tip above the toolbar.
                #
                # Fix: After Phase-1 top scan, if col_top_y is within the upper
                # portion of the screen (< 250px from top), do a second upward pass
                # with a much larger gap_tolerance (120px) to bridge the toolbar.
                #
                # CRITICAL: We must NOT use is_valid_pixel here because Rule 1 of
                # matches_target accepts ANY dark pixel (brightness<120, dist>50),
                # which includes the Windows title bar (RGB≈16,16,16).  The title
                # bar is always near y=0 and would be accepted as a wick pixel,
                # pulling top_y all the way to y=2.
                #
                # Safe bridge pixel check: the pixel must share the same dominant
                # color channel as target_color.  This rejects black/blue title-bar
                # pixels when the candle is green or red.
                def is_bridge_pixel(y):
                    """Like is_valid_pixel but also requires dominant-channel match
                    with target_color to avoid accepting title-bar / toolbar pixels."""
                    if not is_valid_pixel(y): return False
                    c = image.pixelColor(scan_x, y)
                    cr, cg, cb = c.red(), c.green(), c.blue()
                    tr2, tg2, tb2 = target_color.red(), target_color.green(), target_color.blue()
                    # Dominant channel of candidate pixel
                    c_dom = 'r' if cr > cg and cr > cb else ('g' if cg > cr and cg > cb else 'b')
                    t_dom = 'r' if tr2 > tg2 and tr2 > tb2 else ('g' if tg2 > tr2 and tg2 > tb2 else 'b')
                    # For black-wick candles (target_is_black), accept any dark pixel
                    # that is clearly non-background — same as is_valid_pixel.
                    if target_is_black:
                        return True
                    # For colored candles, dominant channel must match.
                    return c_dom == t_dom

                # Only trigger bridge scan if Phase-1 actually found a candle
                # top above the click point (col_top_y < click_y).  If Phase-1
                # found nothing (col_top_y == click_y), the click was in empty
                # chart space — bridging upward would only find toolbar icons.
                # Threshold: toolbar bottom is at y≈100.  Only bridge if the
                # candle top is within 10px of the toolbar (col_top_y < 110).
                # Using 250 was too aggressive — it triggered for any candle
                # in the upper quarter of the screen, causing toolbar icons
                # (e.g. the green 'daily' button at y≈72) to be mistaken for
                # wick pixels.
                if col_top_y < click_y and col_top_y < bridge_threshold:
                    bridge_top_y = col_top_y
                    bridge_gap = 0
                    bridge_gap_tolerance = 120  # large enough to span any toolbar
                    for y in range(col_top_y, max(0, col_top_y - 300), -1):
                        if is_bridge_pixel(y):
                            bridge_top_y = y
                            bridge_gap = 0
                        else:
                            bridge_gap += 1
                            if bridge_gap > bridge_gap_tolerance:
                                break
                    if bridge_top_y < col_top_y:
                        logging.debug(f"  col x={scan_x}: toolbar bridge: top {col_top_y} -> {bridge_top_y}")
                        col_top_y = bridge_top_y

                # Find Bottom
                col_bottom_y = click_y
                current_gap = 0
                last_valid_y = click_y  # track the last valid pixel seen
                scan_bottom_end = min(height, click_y + 800)
                for y in range(click_y, scan_bottom_end):
                    if is_valid_pixel(y):
                        col_bottom_y = y
                        last_valid_y = y
                        current_gap = 0
                    else:
                        current_gap += 1
                        if current_gap > gap_tolerance:
                            break
                # Only accept this column's result if it actually found valid candle pixels.
                # IMPORTANT: Do NOT use a fallback that scans with is_candle_pixel (which
                # ignores color-matching) — that fallback was the root cause of Bottom=828
                # because it accepted the chart's bottom axis bar as a candle pixel.
                col_found = (col_top_y != click_y or is_valid_pixel(col_top_y)) and \
                            (col_bottom_y != click_y or is_valid_pixel(col_bottom_y))

                if is_valid_pixel(col_top_y) or is_valid_pixel(col_bottom_y):
                    found_any_candle = True
                    if col_top_y < global_top_y: global_top_y = col_top_y
                    if col_bottom_y > global_bottom_y: global_bottom_y = col_bottom_y
                    logging.debug(f"  col x={scan_x}: top={col_top_y}, bottom={col_bottom_y}")

            if not found_any_candle:
                 logging.warning("No candle found in scan width.")
                 self.setVisible(True)
                 return

            top_y = global_top_y
            bottom_y = global_bottom_y

            # --- PHASE 2: WICK EXTENSION SCAN ---
            # DESIGN PRINCIPLES (learned from many debugging sessions):
            #
            # UPPER WICK (上影线): May be horizontally offset from the body by ~50px.
            #   Solution: scan a wide range (±wick_search_radius) UPWARD only.
            #   Connectivity check: column must have a pixel near current top_y.
            #   Toolbar bridge: if scan stalls near toolbar, allow one bridge.
            #
            # LOWER WICK (下影线): Always within the body's x-range (x_start~x_end).
            #   Phase-1 already covers x_start~x_end but uses gap_tolerance=12.
            #   If the wick has a small gap (e.g. 1px), Phase-1 may stop early.
            #   Solution: re-scan x_start~x_end DOWNWARD with larger gap_tolerance=25.
            #   NEVER scan outside x_start~x_end downward — adjacent candles of the
            #   same color would be picked up and bottom_y would be wrong.
            #
            # Problem: The candle wick (影线) is only 1-2px wide and may be horizontally
            # offset from the candle body by up to ~50px (especially on daily charts with
            # wide bodies).  Phase 1 only scans click_x ± scan_radius (13 columns), which
            # may miss the wick entirely.
            #
            # Strategy:
            #   1. Find the horizontal center of the candle body found in Phase 1.
            #   2. From that center, scan ±wick_search_radius columns.
            #   3. For each column, extend top_y upward and bottom_y downward using the
            #      same gap_tolerance, starting from the current top_y / bottom_y.
            #
            # This correctly handles:
            #   - Long upper wicks (上影线) that are offset left of the body
            #   - Long lower wicks (下影线) that are offset left of the body
            #   - Thin wicks (1-2px wide) that Phase 1 missed
            wick_search_radius = 40  # search ±40px from body center for wick columns

            # Compute body center from Phase 1 results
            body_center_x = x  # default to click x
            # Find the horizontal span of valid pixels at the body's vertical midpoint
            body_mid_y = (top_y + bottom_y) // 2
            body_xs = []
            for bx in range(x_start, x_end):
                c = image.pixelColor(bx, body_mid_y)
                if color_distance(c, bg_color) > 30 and matches_target(c):
                    body_xs.append(bx)
            if body_xs:
                body_center_x = (min(body_xs) + max(body_xs)) // 2
                logging.debug(f"Phase2: body center x={body_center_x} (from xs={min(body_xs)}~{max(body_xs)})")
            else:
                logging.debug(f"Phase2: body center x={body_center_x} (fallback to click x)")

            wick_x_start = max(0, body_center_x - wick_search_radius)
            wick_x_end = min(image.width(), body_center_x + wick_search_radius + 1)

            def is_valid_pixel_at(sx, y):
                """Check if pixel at (sx, y) is a valid candle pixel."""
                if y < 0 or y >= height: return False
                c = image.pixelColor(sx, y)
                if color_distance(c, bg_color) <= 30: return False
                return matches_target(c)

            # ================================================================
            # PHASE 2A: UPPER WICK SCAN (wide horizontal range, upward only)
            # ================================================================
            # Upper wicks can be offset left/right of the body by up to ~50px.
            # We scan +-wick_search_radius columns, but ONLY upward.
            # Downward scan is excluded here to avoid picking up adjacent candles.
            for wick_x in range(wick_x_start, wick_x_end):
                # Skip columns already covered by Phase 1
                if x_start <= wick_x < x_end:
                    continue
                # Connectivity check: column must have a pixel VERY CLOSE to current top_y
                # Use strict +-3px window to avoid picking up adjacent candles' wicks
                connected_top = False
                for probe_y in range(max(0, top_y - 3), min(height, top_y + 4)):
                    if is_valid_pixel_at(wick_x, probe_y):
                        connected_top = True
                        break
                if not connected_top:
                    continue  # not connected to target candle, skip entirely
                # Scan upward with toolbar bridge support
                col_top_y2 = top_y
                current_gap2 = 0
                bridged2 = False
                for y in range(top_y, max(0, top_y - 600), -1):
                    if is_valid_pixel_at(wick_x, y):
                        col_top_y2 = y
                        current_gap2 = 0
                        bridged2 = False
                    else:
                        current_gap2 += 1
                        if current_gap2 > gap_tolerance:
                            if not bridged2 and y < bridge_threshold:
                                bridged2 = True
                                continue
                            break
                if col_top_y2 < top_y:
                    logging.debug(f"  Phase2A upper wick x={wick_x}: top {top_y}->{col_top_y2}")
                    top_y = col_top_y2

            # ================================================================
            # PHASE 2B: LOWER WICK RE-SCAN (body x-range only, larger gap)
            # ================================================================
            # Lower wicks are always within the body x-range (x_start~x_end).
            # Phase-1 uses gap_tolerance=12 which may stop early if the wick
            # has a small gap. Re-scan the same columns with gap_tolerance=25.
            # CRITICAL: Do NOT scan outside x_start~x_end here. Adjacent candles
            # of the same color would extend bottom_y incorrectly.
            # Keep gap_tolerance small (same as Phase-1) to avoid jumping over
            # price-axis horizontal lines or chart bottom borders.
            lower_gap_tolerance = 8
            for rescan_x in range(x_start, x_end):
                col_bottom_y2 = bottom_y
                current_gap2 = 0
                for y in range(bottom_y, min(height, bottom_y + 600)):
                    if is_valid_pixel_at(rescan_x, y):
                        col_bottom_y2 = y
                        current_gap2 = 0
                    else:
                        current_gap2 += 1
                        if current_gap2 > lower_gap_tolerance:
                            break
                if col_bottom_y2 > bottom_y:
                    logging.debug(f"  Phase2B lower wick x={rescan_x}: bottom {bottom_y}->{col_bottom_y2}")
                    bottom_y = col_bottom_y2

            logging.info(f"After Phase2 wick scan: Top={top_y}, Bottom={bottom_y}")
            # --- END PHASE 2 ---

            # --- SMART SNAP HEURISTIC ---
            # Purpose: when the user accidentally clicks a price-axis label or grid line
            # (a tiny object, H < 20px), snap to the nearest real candle.
            #
            # IMPORTANT: Smart Snap must NOT fire when the user clicked a valid wick.
            # A wick is a thin (1-2px wide) but TALL vertical line.  The original scan
            # (scan_radius=6 columns) already captured the full wick height in global_top_y
            # / global_bottom_y.  So object_h already reflects the true candle height.
            # We must only snap when object_h is genuinely tiny (< 20px) — i.e. the user
            # clicked something that is NOT a candle at all.
            object_h = abs(bottom_y - top_y)
            should_try_snap = (object_h < 20)

            if should_try_snap:
                 logging.info(f"Tiny object (H={object_h}) — attempting Smart Snap to nearest candle...")
                 print("Attempting Smart Snap to Candle...")

                 def is_candidate_pixel(c):
                     dist = color_distance(c, bg_color)
                     if dist < 30: return False
                     rgb = c.red(), c.green(), c.blue()
                     sat = max(rgb) - min(rgb)
                     val = max(rgb)
                     if sat < 20 and val < 150:
                         return False
                     return True

                 # Search ±200px vertically so we can find the candle body even when
                 # the user clicked the wick tip far from the body.
                 scan_range_v = 200
                 gap_tol_v = 40

                 v_scan_start = max(0, click_y - scan_range_v)
                 v_scan_end = min(height - 1, click_y + scan_range_v)

                 best_segment = None
                 best_score = -1

                 snap_scan_radius = 6
                 snap_x_start = max(0, x - snap_scan_radius)
                 snap_x_end = min(image.width(), x + snap_scan_radius + 1)

                 for snap_x in range(snap_x_start, snap_x_end):
                     in_segment = False
                     seg_start = -1
                     gap_count = 0

                     for y in range(v_scan_start, v_scan_end):
                         c = image.pixelColor(snap_x, y)
                         valid = is_candidate_pixel(c)

                         if valid:
                             if not in_segment:
                                 in_segment = True
                                 seg_start = y
                             gap_count = 0
                         else:
                             if in_segment:
                                 gap_count += 1
                                 if gap_count > gap_tol_v:
                                     in_segment = False
                                     seg_end = y - gap_count
                                     seg_h = seg_end - seg_start
                                     if seg_h > 20:
                                         mid_y = (seg_start + seg_end) / 2
                                         d = abs(mid_y - click_y)
                                         score = seg_h - (d * 1.5)
                                         if score > best_score:
                                             best_score = score
                                             best_segment = (seg_start, seg_end)

                     if in_segment:
                         seg_end = v_scan_end - 1
                         seg_h = seg_end - seg_start
                         if seg_h > 20:
                             mid_y = (seg_start + seg_end) / 2
                             d = abs(mid_y - click_y)
                             score = seg_h - (d * 1.5)
                             if score > best_score:
                                 best_score = score
                                 best_segment = (seg_start, seg_end)

                 if best_segment:
                     found_h = best_segment[1] - best_segment[0]
                     if found_h > object_h:
                         top_y = best_segment[0]
                         bottom_y = best_segment[1]
                         logging.info(f"Smart Snap: Switched to Candle H={found_h} ({top_y}, {bottom_y})")
                         print(f"Smart Snap: Found Candle H={found_h}")
                 else:
                     logging.info("Smart Snap: No better object found. Keeping original.")
            
            
            logging.info(f"Final Candle Limit: Top={top_y}, Bottom={bottom_y}")
            print(f"Candle detected: Top={top_y}, Bottom={bottom_y}")
            
            # FINAL GUARD: if top_y >= bottom_y the whole scan collapsed to a point.
            # This can still happen when the candle is very short OR when the bottom
            # wick is cut off by the chart's bottom axis bar (a solid horizontal band
            # of background-like color that interrupts the gap-tolerance scan).
            # Strategy: walk downward from top_y using the broadest possible check
            # (is_candle_pixel, which only requires "not background") all the way to
            # the screen edge, and take the last hit as the true bottom.
            if top_y >= bottom_y:
                logging.warning("top_y >= bottom_y — running edge-extension fallback.")
                extended_bottom = top_y
                for y in range(top_y, height):
                    if is_candle_pixel(y):
                        extended_bottom = y
                bottom_y = extended_bottom
                if top_y >= bottom_y:
                    logging.warning("Edge-extension failed — aborting draw to avoid zero-height line.")
                    self.setVisible(True)
                    return

            logging.info(f"Candle detected: Top={top_y}, Bottom={bottom_y}")
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
            
            # Create global QPoints
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
