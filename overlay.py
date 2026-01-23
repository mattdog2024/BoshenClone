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
        self.current_timeframe = timeframe_name
        print(f"Timeframe set to: {self.current_timeframe}")

    def auto_measure(self, pos):
        """
        Captures screen, analyzes column at pos.x(), finds High/Low.
        """
        import logging
        logging.basicConfig(filename='debug_boshen.log', level=logging.INFO, format='%(asctime)s - %(message)s')
        
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
            # Sample central strip to find dominant color
            color_counts = {}
            scan_height = 600 # Analyze +/- 300 pixels
            start_scan = max(0, click_y - 300)
            end_scan = min(height, click_y + 300)
            
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
            # We scan a small window around x to catch wicks
            # Reduced radius to 2 for zoomed-out accuracy (prevent hitting neighbors)
            # Increased radius to 4 for better wick detection (9px width)
            scan_radius = 4 
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
                    
                    # 1. Strict Black/Gray Check (For Text Rejection)
                    # If pixel is very clearly Grayscale (Saturation < 10) AND Dark (Brightness < 100)
                    if saturation < 10 and brightness < 100:
                        # Only accept if the Target Candle itself was determined to be Black/Gray.
                        # This filters out black text labels when clicking a colored candle.
                        return target_is_black

                    # 2. Dominant Channel Check
                    # If target is RED dominant (R > G, R > B), candidate must be too.
                    # If target is GREEN dominant (G > R, G > B), candidate must be too.
                    
                    tr, tg, tb = target_color.red(), target_color.green(), target_color.blue()
                    cr, cg, cb = c.red(), c.green(), c.blue()
                    
                    # What is target's dominant channel?
                    t_dom = 'r' if tr > tg and tr > tb else ('g' if tg > tr and tg > tb else 'b')
                    c_dom = 'r' if cr > cg and cr > cb else ('g' if cg > cr and cg > cb else 'b')
                    
                    # If dominant channels match, it's the same "hue" family
                    if t_dom == c_dom:
                        return True
                        
                    return False


            gap_tolerance = 60 # Reduced from 200. Hollow bodies are handled if we hit color again.
            
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

                # Find Bottom
                col_bottom_y = click_y
                current_gap = 0
                for y in range(click_y, min(height, click_y + 800)):
                    if is_valid_pixel(y):
                        col_bottom_y = y
                        current_gap = 0
                    else:
                        current_gap += 1
                        if current_gap > gap_tolerance:
                            break
                            
                # Check if this column actually found something meaningful
                # If col_top_y == click_y and we started at BG, it might be invalid.
                # But we just track min/max
                
                # If we found valid pixels in this column?
                if is_valid_pixel(col_top_y) or is_valid_pixel(col_bottom_y):
                    found_any_candle = True
                    if col_top_y < global_top_y: global_top_y = col_top_y
                    if col_bottom_y > global_bottom_y: global_bottom_y = col_bottom_y

            if not found_any_candle:
                 logging.warning("No candle found in scan width.")
                 self.setVisible(True)
                 return

            top_y = global_top_y
            bottom_y = global_bottom_y
            
            # --- SMART SNAP HEURISTIC (Improved) ---
            # Issue: User clicks text label (e.g. "3031"). Previous check was too strict.
            # Fix: If object is small (< 50px), assume it's noise/label and look for the REAL candle.
            object_h = abs(bottom_y - top_y)
            
            # Trigger if small object found OR if we explicitly detected a black target (label)
            should_try_snap = (object_h < 50) or target_is_black
            
            if should_try_snap:
                 logging.info(f"Potential label/noise detected (H={object_h}). Attempting Smart Snap...")
                 print("Attempting Smart Snap to Candle...")
                 
                 found_vibrant = False
                 v_top = float('inf')
                 v_bottom = float('-inf')
                 
                 # Define "Candle-like": 
                 # 1. Not Background
                 # 2. significant difference from BG
                 def is_candidate_pixel(c):
                     dist = color_distance(c, bg_color)
                     if dist < 30: return False # Too close to BG
                     
                     # If we have a 'strict' target (e.g. user clicked a Red candle), match it?
                     # No, here we assume user clicked 'Black Text' so we don't know the candle color.
                     # We just accept "Visible" things.
                     # But we want to avoid black text again.
                     
                     rgb = c.red(), c.green(), c.blue()
                     sat = max(rgb) - min(rgb)
                     val = max(rgb)
                     
                     # Reject purely black/gray things (likely other text/grid lines)
                     # Unless they are very bright (white text?)
                     if sat < 20 and val < 150: 
                         return False
                         
                     return True

                 scan_range_v = 80 # Reduced from 400 to avoid snapping to Volume bars at bottom
                 gap_tol_v = 40    # Reduced gap tolerance
                 
                 v_scan_start = max(0, click_y - scan_range_v)
                 v_scan_end = min(height, click_y + scan_range_v)
                 
                 # Identify the longest vibrant segment
                 best_segment = None 
                 best_score = -1 
                 
                 # Scan width for smart snap too!
                 snap_scan_radius = 4
                 snap_x_start = max(0, x - snap_scan_radius)
                 snap_x_end = min(image.width(), x + snap_scan_radius + 1)
                 
                 for snap_x in range(snap_x_start, snap_x_end):
                     in_segment = False
                     seg_start = -1
                     gap_count = 0
                     
                     # Check this column
                     for y in range(v_scan_start, v_scan_end):
                         c = image.pixelColor(snap_x, y)
                         valid = is_candidate_pixel(c)
                         
                         if valid:
                             if not in_segment:
                                 in_segment = True
                                 seg_start = y
                             gap_count = 0 # Reset gap
                         else:
                             if in_segment:
                                 gap_count += 1
                                 if gap_count > gap_tol_v:
                                     # Segment ended
                                     in_segment = False
                                     seg_end = y - gap_count
                                     h = seg_end - seg_start
                                     
                                     if h > 20: # Min candle height
                                         # Score = Height - Distance_from_click * 2
                                         # We prefer Taller objects, but penalize being far away
                                         mid_y = (seg_start + seg_end) / 2
                                         dist = abs(mid_y - click_y)
                                         score = h - (dist * 1.5) 
                                         
                                         if score > best_score:
                                             best_score = score
                                             best_segment = (seg_start, seg_end)
                     
                     # Check last segment
                     if in_segment:
                         seg_end = v_scan_end - 1
                         h = seg_end - seg_start
                         if h > 20:
                             mid_y = (seg_start + seg_end) / 2
                             dist = abs(mid_y - click_y)
                             score = h - (dist * 1.5)
                             if score > best_score:
                                 best_score = score
                                 best_segment = (seg_start, seg_end)

                 # Only override if we found something significantly better
                 if best_segment: 
                     # Check if result is "better" than original small object?
                     # Since we are using Score, assume if Score > 0 it's decent.
                     # Original object_h is likely small (<50).
                     # Only switch if the found segment is Taller than the original "Noise"
                     
                     found_h = best_segment[1] - best_segment[0]
                     
                     if found_h > object_h:
                         top_y = best_segment[0]
                         bottom_y = best_segment[1]
                         logging.info(f"Smart Snap Success: Switched to Candle H={found_h} ({top_y}, {bottom_y})")
                         print(f"Smart Snap: Found Better Candle H={found_h}")
                 else:
                     logging.info("Smart Snap: No better object found. Keeping original.")
            
            
            logging.info(f"Final Candle Limit: Top={top_y}, Bottom={bottom_y}")
            print(f"Candle detected: Top={top_y}, Bottom={bottom_y}")
            
            if top_y >= bottom_y:
                 logging.warning("Top >= Bottom, invalid.")

            
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
        Saves the CURRENT drawings to the Analysis Bucket for the current timeframe.
        Overwrites existing data for that timeframe.
        """
        import copy
        current_tf = self.current_timeframe
        
        snapshot = []
        for d in self.drawings:
            # We copy specific fields to be safe and independent
            new_d = {
                'price_a': d.get('price_a', 0.0),
                'price_b': d.get('price_b', 0.0),
                'timeframe': current_tf, # Force tag to the slot we are saving into
            }
            snapshot.append(new_d)
            
        self.analysis_data[current_tf] = snapshot
        self.save_analysis_data_to_file()
        
        # Feedback
        print(f"SNAPSHOT SAVED: {len(snapshot)} drawings saved to '{current_tf}' slot.")
        # Removed QMessageBox to avoid crash.

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
        
        # User requested to REMOVE auto-calibration on K-line selection to avoid lag.
        # Calibration should only happen via "Auto" button (ocr_selection).
        # if tool_name == "k线": ... [Removed]
             
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
             candidates = [
                 {'name': 'Left', 'x': 0, 'y': 0, 'w': 150, 'h': h_total},
                 {'name': 'Right', 'x': w_total - 200, 'y': 0, 'w': 200, 'h': h_total}
             ]
             
             was_visible = self.isVisible()
             self.setVisible(False)
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

             # Start Async Worker
             # We create a new worker each time (simplest approach, though recycling could works)
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
            self.is_drawing = True

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

    def load_preset(self, index, name, data):
        import logging
        drawing = self.drawings[index]
        drawing['price_a'] = data['a']
        drawing['price_b'] = data['b']
        
        # Restore Calibration if exists
        if 'calibration' in data and data['calibration']:
             self.global_calibration = data['calibration']
             logging.info(f"Restored Global Calibration from Preset '{name}': {self.global_calibration}")
             
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

        if self.is_drawing and self.start_point and self.end_point:
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
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.update()
