
lines = []
with open('overlay.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Target lines are roughly 168 (else:) to 210
# We look for the marker to be sure
start_index = -1
for i, line in enumerate(lines):
    if 'logging.info(f"Target Candle Color: {target_color.name()}")' in line:
        start_index = i
        break

if start_index != -1:
    print(f"Found start at line {start_index+1}")
    
    # Line 169 (start_index) should be 16 spaces
    # lines[start_index] = ' ' * 16 + 'logging.info(f"Target Candle Color: {target_color.name()}")\n'
    
    # We will just rewrite the whole block rigidly
    
    new_block = [
        ' ' * 16 + 'logging.info(f"Target Candle Color: {target_color.name()}")\n',
        '\n',
        ' ' * 16 + '# Pre-check if target is black\n',
        ' ' * 16 + 'tr, tg, tb = target_color.red(), target_color.green(), target_color.blue()\n',
        ' ' * 16 + 'target_is_black = (max(tr,tg,tb) - min(tr,tg,tb) < 20) and (max(tr,tg,tb) < 100)\n',
        '\n',
        ' ' * 16 + 'def matches_target(c):\n',
        ' ' * 20 + '# Robust matching for thin wicks (anti-aliasing)\n',
        ' ' * 20 + '\n',
        ' ' * 20 + '# 1. Saturation Check: Reject Gray lines\n',
        ' ' * 20 + '# gray has low diff between channels\n',
        ' ' * 20 + 'rgb = c.red(), c.green(), c.blue()\n',
        ' ' * 20 + 'saturation = max(rgb) - min(rgb)\n',
        ' ' * 20 + '\n',
        ' ' * 20 + '# Check for BLACK candle (low saturation but also LOW brightness)\n',
        ' ' * 20 + 'brightness = max(rgb)\n',
        ' ' * 20 + '\n',
        ' ' * 20 + 'if saturation < 15:\n',
        ' ' * 24 + '# If it\'s dark (Black)\n',
        ' ' * 24 + 'if brightness < 100:\n',
        ' ' * 28 + '# Only accept black if the target itself is black\n',
        ' ' * 28 + 'return target_is_black\n',
        ' ' * 24 + 'else:\n',
        ' ' * 28 + 'return False # It\'s a gray grid line\n',
        ' ' * 20 + '\n',
        ' ' * 20 + '# 2. Dominant Channel Check\n',
        ' ' * 20 + '# If target is RED dominant (R > G, R > B), candidate must be too.\n',
        ' ' * 20 + '# If target is GREEN dominant (G > R, G > B), candidate must be too.\n',
        ' ' * 20 + '# This handles light-green wicks vs dark-green body.\n',
        ' ' * 20 + '\n',
        ' ' * 20 + 'tr, tg, tb = target_color.red(), target_color.green(), target_color.blue()\n',
        ' ' * 20 + 'cr, cg, cb = c.red(), c.green(), c.blue()\n',
        ' ' * 20 + '\n',
        ' ' * 20 + '# What is target\'s dominant channel?\n',
        ' ' * 20 + 't_dom = \'r\' if tr > tg and tr > tb else (\'g\' if tg > tr and tg > tb else \'b\')\n',
        ' ' * 20 + 'c_dom = \'r\' if cr > cg and cr > cb else (\'g\' if cg > cr and cg > cb else \'b\')\n',
        ' ' * 20 + '\n',
        ' ' * 20 + '# If dominant channels match, it\'s the same "hue" family\n',
        ' ' * 20 + 'if t_dom == c_dom:\n',
        ' ' * 24 + 'return True\n',
        ' ' * 20 + '\n',
        ' ' * 20 + 'return False\n'
    ]
    
    # We need to find where to END the replacement.
    # The previous code ended at 'return False' or 'target_is_black' depending on where I left it.
    # But checking the file content in Step 273, the block ends at line 208 'return False', 
    # and 210 'Fallback: simple distance...' is the next part.
    
    end_index = -1
    for i in range(start_index, len(lines)):
        if "Fallback: simple distance" in lines[i]:
            end_index = i
            break
            
    if end_index != -1:
         # Before Fallback, there might be empty lines
         print(f"Found end at line {end_index+1}")
         
         # Slice and dice
         # Keep lines[:start_index]
         # Insert new_block
         # Keep lines[end_index:]
         
         final_lines = lines[:start_index] + new_block + lines[end_index:]
         
         with open('overlay.py', 'w', encoding='utf-8') as f:
             f.writelines(final_lines)
         print("File updated successfully.")
    else:
        print("Could not find end marker.")
else:
    print("Could not find start marker.")
