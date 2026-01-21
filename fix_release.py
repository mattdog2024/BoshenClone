
import re

new_method = """    def mouseReleaseEvent(self, event):
        print(f"DEBUG: MouseRelease - is_drawing: {self.is_drawing}")
        if self.is_drawing and self.start_point and self.end_point:
            self.is_drawing = False
            
            try:
                # Commit drawing
                if self.current_tool == "单": 
                     new_drawing = {
                         'type': 'boshen_single',
                         'start': self.start_point,
                         'end': self.end_point
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
"""

with open('overlay.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Find start
start_idx = -1
for i, line in enumerate(lines):
    if "def mouseReleaseEvent(self, event):" in line:
        start_idx = i
        break

if start_idx != -1:
    print(f"Found method at line {start_idx+1}")
    
    # We replace until the next method or end of file
    # The next method is `draw_item` or similar.
    # Just look for unindented 'def ' or end of class indentation?
    # Or just replace the block we know. The method logic is long.
    
    end_idx = -1
    for i in range(start_idx+1, len(lines)):
         if "def " in lines[i] and not lines[i].startswith("        "): # Top level or class level def
             # check indentation level. mouseReleaseEvent is class method, so 4 spaces.
             # Next method should have 4 spaces.
             if lines[i].startswith("    def "):
                 end_idx = i
                 break
    
    if end_idx == -1:
        end_idx = len(lines)
        
    print(f"Replacing up to line {end_idx}")
    
    final_lines = lines[:start_idx] + [new_method] + lines[end_idx:]
    
    with open('overlay.py', 'w', encoding='utf-8') as f:
        f.writelines(final_lines)
    print("Successfully updated mouseReleaseEvent.")
else:
    print("Method not found.")
