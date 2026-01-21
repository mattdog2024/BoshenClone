
import re

new_logic = """            else:
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
"""

with open('overlay.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Find start and end indices
start_idx = -1
end_idx = -1

for i, line in enumerate(lines):
    if 'logging.info(f"Target Candle Color: {target_color.name()}")' in line:
        start_idx = i - 1 # Include the "else:" line which is usually just before
        # Actually in my previous fix script I looked for the logging line.
        # But wait, "else:" is the line before.
        # Let's play safe and search for "else:" followed by the logging line?
        # Simpler: Search for the logging line index.
        pass

if start_idx == -1:
    # Try searching efficiently
    for i, line in enumerate(lines):
         if 'logging.info(f"Target Candle Color: ' in line:
             start_idx = i - 1 # The 'else:'
             break

if start_idx != -1:
    # Find end
    for i in range(start_idx, len(lines)):
         if "Fallback: simple distance" in lines[i]:
             end_idx = i
             break

    if end_idx != -1:
        print(f"Replacing lines {start_idx+1} to {end_idx}")
        # Construct new lines list
        # We need to make sure new_logic has correct indentation
        # My new_logic string above already has 12 spaces indentation for "else:" etc.
        # Wait, "else:" needs 12 spaces?
        # previous code:
        # 164:             
        # 165:             if not target_color:
        #                 ...
        # 168:             else:
        
        # Yes, 12 spaces.
        
        final_lines = lines[:start_idx] + [new_logic] + lines[end_idx:]
        
        with open('overlay.py', 'w', encoding='utf-8') as f:
            f.writelines(final_lines)
            print("Successfully updated overlay.py")

    else:
        print("End marker not found")
else:
    print("Start marker not found")
