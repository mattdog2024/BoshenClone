
import logging
import re
try:
    from rapidocr_onnxruntime import RapidOCR
except ImportError:
    RapidOCR = None

class BoshenOCR:
    def __init__(self):
        self.ocr = None
        if RapidOCR:
            self.ocr = RapidOCR()
        else:
            print("RapidOCR not installed or failed to import.")

    def is_available(self):
        return self.ocr is not None

    def analyze_axis(self, image_path):
        """
        Analyzes an image of the price axis.
        Returns:
            dict or None: {'scale': float, 'ref_y': int, 'ref_price': float}
        """
        if not self.ocr:
            return None

        result, elapse = self.ocr(image_path)
        
        if not result:
            logging.warning("OCR found nothing.")
            return None

        # Filter for numbers
        valid_items = []
        for item in result:
            # item: [[x1,y1], [x2,y2], ...], text, score
            box, text, score = item
            
            # Clean text (remove currency symbols, commas)
            clean_text = re.sub(r'[^\d\.-]', '', text)
            
            try:
                val = float(clean_text)
                # Calculate center Y
                # box is usually 4 points: tl, tr, br, bl
                ys = [p[1] for p in box]
                center_y = sum(ys) / len(ys)
                
                valid_items.append({'val': val, 'y': center_y, 'raw_box': box})
            except ValueError:
                continue

        if len(valid_items) < 2:
            logging.warning(f"Not enough numbers found. Found: {len(valid_items)}")
            return None

        # Sort by Y (Top to Bottom)
        # Note: In screen coords, Y increases downwards.
        # So Top item has Smallest Y.
        valid_items.sort(key=lambda x: x['y'])

        # Filter for Monotonic Decreasing Sequence
        # Prices MUST decrease as Y increases (Top to Bottom).
        # We want to find the longest subsequence where val[i] > val[i+1]
        
        if not valid_items:
             return None

        # Simple greedy approach or LIS (Longest Increasing Subsequence) variant?
        # Since noise might be interspersed, we should try to find the "Axis" column.
        # But first, let's just try to find a consistent price column.
        
        # 1. Bin by X-coord to separate "Price" column from "Volume" column if they exist side-by-side
        # (If order book: [Label] [Price] [Volume])
        x_bins = {}
        for item in valid_items:
             # Use center X
             xs = [p[0] for p in item['raw_box']]
             center_x = sum(xs) / len(xs)
             
             # Bin width 20px
             bin_key = int(center_x // 20) * 20
             if bin_key not in x_bins: x_bins[bin_key] = []
             x_bins[bin_key].append(item)
             
        # Find bin with most items
        if not x_bins: return None
        best_bin = max(x_bins, key=lambda k: len(x_bins[k]))
        column_items = x_bins[best_bin]
        
        # Sort column items by Y again just in case
        column_items.sort(key=lambda x: x['y'])
        
        # 2. Extract Longest Decreasing Subsequence (Prices decrease down)
        # We allow small errors/noise but mainly want the dominant trend.
        # Actually, for an axis, *every* number should be decreasing.
        # But we might have OCR errors. 
        # Let's clean outliers.
        
        if len(column_items) < 2:
             logging.warning("Not enough numbers in best column.")
             return None

        cleaned_items = [column_items[0]]
        for i in range(1, len(column_items)):
             curr = column_items[i]
             prev = cleaned_items[-1]
             
             # Check if consistent
             # We expect curr['val'] < prev['val'] (Price decreases down)
             # And curr['y'] > prev['y'] (Y increases down) - already sorted by Y
             
             if curr['val'] < prev['val']:
                 cleaned_items.append(curr)
             else:
                 # Violation. Is 'curr' the outlier or 'prev'?
                 # If we have a chain, usually the new one is the outlier or the chain broke.
                 # Let's skip 'curr' for now (greedy cleaning).
                 logging.debug(f"Skipping non-decreasing value: {curr['val']} (Prev: {prev['val']})")
                 pass
        
        if len(cleaned_items) < 2:
             logging.warning("No monotonic decreasing sequence found.")
             return None
             
        # Pick Top and Bottom from the Cleaned list
        top_item = cleaned_items[0]
        bottom_item = cleaned_items[-1]

        # Calculate Scale
        # Scale = (P_bottom - P_top) / (Y_bottom - Y_top)
        # Usually Price decreases as Y increases (downwards).
        # So P_bottom < P_top.
        # Scale should be negative.
        
        p_top = top_item['val']
        p_bottom = bottom_item['val']
        y_top = top_item['y']
        y_bottom = bottom_item['y']
        
        dy = y_bottom - y_top
        dp = p_bottom - p_top
        
        if abs(dy) < 5: # Too close
             logging.warning("Numbers too close vertically.")
             return None
             
        if abs(dp) < 0.0001: # Price difference is zero
             logging.warning(f"Zero price difference detected. (Top={p_top}, Bottom={p_bottom})")
             logging.warning("Calibration Failed: All detected numbers are identical or zero.")
             return None
             
        scale = dp / dy
        
        # Calculate Average Gap
        total_gap = 0
        gap_count = 0
        for i in range(len(cleaned_items) - 1):
            gap = cleaned_items[i+1]['y'] - cleaned_items[i]['y']
            total_gap += gap
            gap_count += 1
            
        avg_gap = total_gap / gap_count if gap_count > 0 else 0
        
        # DENSITY CHECK: Filter out Order Book
        # Order Book usually has gaps < 25px. Price Axis usually > 40px.
        # We set threshold at 30px.
        if avg_gap < 30:
             logging.warning(f"Rejected Column: Too dense (Avg Gap={avg_gap:.1f} < 30). Likely Order Book.")
             # We return None so the caller knows this column is bad.
             return None
        
        logging.info(f"OCR Success: Top({p_top} @ {y_top:.1f}), Bottom({p_bottom} @ {y_bottom:.1f})")
        logging.info(f"Calculated Scale: {scale:.6f}, Avg Gap: {avg_gap:.1f}")
        logging.info(f"Found Numbers (Cleaned): {[x['val'] for x in cleaned_items]}")

        return {
            'scale': scale,
            'ref_y_local': y_top, 
            'ref_price': p_top,
            'avg_gap': avg_gap
        }
