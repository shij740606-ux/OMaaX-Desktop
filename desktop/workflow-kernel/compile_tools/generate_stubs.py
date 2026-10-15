#!/usr/bin/env python3
"""
为 Cython 编译产物生成 .py stub 文件。

每个 stub 同时支持：
  1. 子进程调用 (python apps/cli/run_xxx.py --args)
     -> __name__ == "__main__" 分支加载 .so 并调用 main()
  2. 模块导入 (from apps.cli.run_xxx import SomeClass)
     -> 模块级 __getattr__ 懒加载 .so ，透明转发属性访问

用法ￚ
    python generate_stubs.py <output_dir> [<output_dir2> ...]
"""
import sys
from pathlib import Path

STUB_TEMPLATE = (
    "# Auto-generated stub — delegates to the compiled Cython extension.\n"
    "# Supports both subprocess execution and module import.\n"
    "import importlib.util as _ilu\n"
    "import sys as _sys\n"
    "from pathlib import Path as _P\n"
    "\n"
    "_stem = _P(__file__).stem\n"
    "_so = next(_P(__file__).parent.glob(_stem + '.*.so'), None)\n"
    "_cached_mod = None\n"
    "\n"
    "\n"
    "def _load_compiled():\n"
    "    global _cached_mod\n"
    "    if _cached_mod is None:\n"
    "        if _so is None:\n"
    "            raise FileNotFoundError(\n"
    "                f'Compiled module not found for {__file__}'\n"
    "            )\n"
    "        _spec = _ilu.spec_from_file_location(_stem, _so)\n"
    "        _cached_mod = _ilu.module_from_spec(_spec)\n"
    "        _spec.loader.exec_module(_cached_mod)\n"
    "    return _cached_mod\n"
    "\n"
    "\n"
    "def __getattr__(name: str):\n"
    "    \"\"\"Transparent proxy: forward attribute access to the compiled module.\"\"\"\n"
    "    return getattr(_load_compiled(), name)\n"
    "\n"
    "\n"
    "if __name__ == '__main__':\n"
    "    _mod = _load_compiled()\n"
    "    _sys.exit(_mod.main() if hasattr(_mod, 'main') else 0)\n"
)


def generate_stubs(output_dir: Path) -> int:
    created = 0
    for so_path in sorted(output_dir.rglob("*.so")):
        stem = so_path.name.split(".")[0]
        if stem == "__init__":
            continue
        py_path = so_path.parent / f"{stem}.py"
        if py_path.exists():
            continue
        py_path.write_text(STUB_TEMPLATE, encoding="utf-8")
        print(f"  stub: {py_path.relative_to(output_dir)}")
        created += 1
    return created


def main():
    if len(sys.argv) < 2:
        print(f"用法: {sys.argv[0]} <output_dir> [<output_dir2> ...]", file=sys.stderr)
        sys.exit(1)

    total = 0
    for arg in sys.argv[1:]:
        d = Path(arg)
        if not d.is_dir():
            print(f"目录不存在，跳过: {d}", file=sys.stderr)
            continue
        print(f"[generate_stubs] 扫描: {d}")
        n = generate_stubs(d)
        print(f"[generate_stubs] 已生成 {n} 个 stub: {d}")
        total += n

    print(f"[generate_stubs] 合计: {total} 个 stub")


if __name__ == "__main__":
    main()
