with open('canvas.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

for idx, line in enumerate(lines):
    if line.startswith('    def ') and ('mouse' in line or 'draw' in line or 'paint' in line):
        print(f"{idx+1}: {line.strip()}")
