with open('canvas.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

for idx, line in enumerate(lines):
    if '_init_brush_tip' in line or 'brush_tip' in line:
        print(f"{idx+1}: {line.strip()}")
