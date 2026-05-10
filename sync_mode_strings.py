import os

def sync_modes(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Change tool mode string "arrow" to "line"
    # This matches the restored canvas.py method names (line_mousePressEvent, etc.)
    content = content.replace('"arrow"', '"line"')
    
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)

sync_modes('main.py')
sync_modes('canvas.py')

print("Synchronized all 'arrow' mode strings to 'line'")
