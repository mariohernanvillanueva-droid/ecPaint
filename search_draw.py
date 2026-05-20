import re

def search():
    with open('canvas.py', 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Let's find all methods in canvas.py to see what drawing-related methods exist
    methods = re.findall(r'def\s+(\w+)\(', content)
    draw_methods = [m for m in methods if 'draw' in m or 'paint' in m or 'mouse' in m or 'tool' in m]
    print("Drawing/Tool Methods:", draw_methods)

    # Let's find how active_color is set and how colors are used in different tools
    # We can search for the definitions of specific tool methods, e.g. "brush", "spray", "marker", "pen"
    lines = content.split('\n')
    for idx, line in enumerate(lines):
        if 'QColor(' in line or 'QPen(' in line or 'QBrush(' in line:
            # print if inside a drawing tool
            print(f"{idx+1}: {line.strip()}")

if __name__ == '__main__':
    search()
