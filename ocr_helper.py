
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
        
        # 2. Extract Longest Consistent Subsequence (Linearity Check)
        # We expect Price = Scale * Y + C
        # We calculate the scale between every adjacent pair in the column.
        # The "True Scale" should be the most common scale (mode).
        
        if len(column_items) < 2:
             logging.warning("Not enough numbers in best column.")
             return None

        # Calculate scales between adjacent items
        # scale = (val2 - val1) / (y2 - y1)
        # We expect Y to increase (downwards) and Val to decrease.
        # So scale is usually NEGATIVE.
        
        scales = []
        for i in range(len(column_items) - 1):
            curr = column_items[i]
            next_item = column_items[i+1] # Next one down
            
            dy = next_item['y'] - curr['y']
            dp = next_item['val'] - curr['val']
            
            if dy < 10: continue # Too close
            
            scale = dp / dy
            scales.append({'scale': scale, 'i': i, 'j': i+1})
            
        if not scales:
            logging.warning("No valid pairs found.")
            return None
            
        # Find dominant scale cluster
        # We accept scales that are within 10% of each other?
        # Or just use histogram binning.
        
        # Simple cluster:
        clusters = []
        
        for s in scales:
            sc = s['scale']
            # Find if it fits an existing cluster
            found = False
            for cl in clusters:
                 # Check relative error
                 avg = sum([x['scale'] for x in cl]) / len(cl)
                 if abs(sc - avg) / (abs(avg) + 0.0001) < 0.1: # 10% tolerance
                     cl.append(s)
                     found = True
                     break
            if not found:
                 clusters.append([s])
                 
        # Best cluster is longest
        if not clusters: return None
        best_cluster = max(clusters, key=len)
        
        # Calculate weighted average scale of best cluster
        avg_scale = sum([x['scale'] for x in best_cluster]) / len(best_cluster)
        
        logging.info(f"Dominant Scale Found: {avg_scale} (from {len(best_cluster)} pairs)")
        
        # Now filter items that fit this scale
        # We pick the "Anchor" as the top item of the first pair in the cluster
        anchor_pair_idx = best_cluster[0]['i']
        anchor = column_items[anchor_pair_idx]
        
        cleaned_items = []
        for item in column_items:
            # Check if item lies on the line defined by Anchor and Scale
            # Predicted Val = AnchorVal + (ItemY - AnchorY) * Scale
            # Actual Val should be close.
            
            pred_val = anchor['val'] + (item['y'] - anchor['y']) * avg_scale
            diff = abs(item['val'] - pred_val)
            
            # Tolerance? Maybe 5% of the value interval or absolute?
            # Let's use visually relative tolerance.
            # If the number is "3200", error shouldn't be "100".
            # But if scale is 0.5 per pixel, and height error is 5px -> error 2.5.
            
            # Heuristic: error < abs(50 * avg_scale)  (approx 50 pixels worth of error)
            limit = abs(50 * avg_scale)
            if diff < limit:
                cleaned_items.append(item)
            else:
                logging.warning(f"Rejecting outlier: {item['val']} (Pred: {pred_val:.2f})")
                
        if len(cleaned_items) < 2:
             logging.warning("Cleaned list too short.")
             return None
             
        # Pick Top and Bottom from the Cleaned list
        top_item = cleaned_items[0]
        bottom_item = cleaned_items[-1]

        # Calculate Final Scale from extremes
        p_top = top_item['val']
        p_bottom = bottom_item['val']
        y_top = top_item['y']
        y_bottom = bottom_item['y']
        
        dy = y_bottom - y_top
        dp = p_bottom - p_top
        
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
        # We set threshold at 15px.
        if avg_gap < 15:
             logging.warning(f"Rejected Column: Too dense (Avg Gap={avg_gap:.1f} < 15). Likely Order Book.")
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
