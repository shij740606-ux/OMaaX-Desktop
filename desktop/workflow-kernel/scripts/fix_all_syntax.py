#!/usr/bin/env python3
"""
一次性修复所有语法错误
"""
import re
from pathlib import Path

def fix_syntax_errors(file_path):
    """修复脚本中的语法错误"""
    path = Path(file_path)
    
    if not path.exists():
        print(f"❌ 文件不存在: {path}")
        return False
    
    # 读取内容
    content = path.read_text(encoding='utf-8', errors='ignore')
    original = content
    
    # 修复1: def main() -> def main():
    content = re.sub(r'^def main\(\)$', 'def main():', content, flags=re.MULTILINE)
    
    # 修复2: main(): -> main()
    content = content.replace('main():', 'main()')
    
    # 修复3: 检查函数定义是否都有冒号
    # 查找所有 def 语句
    lines = content.split('\n')
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith('def ') and not stripped.endswith(':'):
            # 添加冒号
            lines[i] = line.rstrip() + ':'
            print(f"✅ 修复第 {i+1} 行: 添加冒号")
    
    # 修复4: 检查 if __name__ == "__main__": 块后的代码
    in_main_block = False
    for i, line in enumerate(lines):
        if line.strip() == 'if __name__ == "__main__":':
            in_main_block = True
        elif in_main_block and line.strip() == 'main()':
            # main() 调用之后，不应该再有非注释代码
            # 检查后续行
            for j in range(i+1, len(lines)):
                next_line = lines[j]
                if next_line.strip() and not next_line.strip().startswith('#'):
                    # 有非注释代码，这可能是错误的
                    print(f"⚠️  第 {j+1} 行可能有错误的代码: {next_line[:50]}...")
                    # 可以添加缩进或注释掉
                    if not next_line.startswith('    '):
                        lines[j] = '    ' + next_line
    
    # 重新组合
    content = '\n'.join(lines)
    
    # 如果内容有变化，保存
    if content != original:
        path.write_text(content, encoding='utf-8')
        print(f"✅ 修复了 {file_path}")
        return True
    else:
        print(f"✅ 无需修复 {file_path}")
        return False

def main():
    print("🔧 修复 scripts/run_identify_web.py...")
    
    fixed = fix_syntax_errors("scripts/run_identify_web.py")
    
    if fixed:
        print("\n🔍 测试语法...")
        import subprocess
        
        result = subprocess.run(
            ['python', '-m', 'py_compile', 'scripts/run_identify_web.py'],
            capture_output=True,
            text=True
        )
        
        if result.returncode == 0:
            print("✅ 语法检查通过！")
            print("\n🎯 测试运行:")
            print("python scripts/run_identify_web.py --help")
        else:
            print(f"❌ 语法错误:")
            print(result.stderr)
            
            # 显示错误行
            match = re.search(r'line (\d+)', result.stderr)
            if match:
                line_num = int(match.group(1))
                print(f"\n🔍 查看错误行 ({line_num}):")
                path = Path("scripts/run_identify_web.py")
                lines = path.read_text(encoding='utf-8').split('\n')
                start = max(0, line_num - 3)
                end = min(len(lines), line_num + 2)
                for i in range(start, end):
                    prefix = ">>>" if i+1 == line_num else "   "
                    print(f"{prefix} {i+1:3d}: {lines[i]}")
    else:
        print("✅ 文件无需修复")

if __name__ == "__main__":
    main()
