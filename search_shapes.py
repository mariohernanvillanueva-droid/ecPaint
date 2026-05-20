with open('canvas.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

for idx, line in enumerate(lines):
    if 'shape_pen' in line or 'shape_brush' in line:
        print(f"{idx+1}: {line.strip()}")
