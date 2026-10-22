r"""services/zip_safety.py —— LaTeX 项目 zip 安全校验与解压（P4，纯函数模块）。

安全清单（每条攻击 → 防御位置，对应设计 §2）：
  #1  路径穿越 ../../etc/passwd   → sanitize_entry_name：拒绝 .. 段/绝对路径/盘符/空段
                                    （\ 已先归一为 /，Windows 反斜杠变体同规则）
  #2  反斜杠变体 ..\\..\\x         → 同上（name.replace("\\\\", "/") 后再校验）
  #3  符号链接条目                 → _is_symlink：external_attr 的 S_IFMT == S_IFLNK → 整包拒绝
  #4  可执行扩展名 .exe/.sh/.so    → classify_entry：DENY_EXT → 整包拒绝（点名条目）
  #5  Unix 可执行位 0o111          → _has_exec_bit：普通文件带 x 位 → 整包拒绝（目录 0o755 例外）
  #6  无害非白名单 .docx/.log/.zip → classify_entry：skip（记 skipped[]；.zip 跳过=天然防嵌套递归）
  #7  zip bomb 压缩比              → 预扫 file_size/compress_size > max_zip_ratio → ZIP_BOMB
  #8  zip bomb 总量                → 预扫声明累计 > max_extract_bytes → ZIP_BOMB；
                                    流式写盘按**实际字节**再计数（不信头部声明）超限即中止
  #9  zip bomb 单条目              → 预扫 file_size > max_entry_bytes → ZIP_BOMB；实写计数兜底
  #10 zip bomb 条目数              → 预扫 len(infolist) > max_zip_entries → ZIP_BOMB（先于一切写盘）
  #11 深嵌套/超长路径              → 路径深度 > max_path_depth 或总长 > max_path_len → ZIP_UNSAFE
  #12 损坏/加密 zip                → zipfile.BadZipFile / RuntimeError → ZIP_INVALID
  #13 头部谎报小文件               → 实写计数兜底（#8）；CRC 错 → ZIP_INVALID
  #14 恶意 .tex shell escape       → 不在本模块范围（tectonic --untrusted 防护，P3a 已实证）
  #15 解压阻塞事件循环             → 调用方 asyncio.to_thread 包裹；本模块上限 200MB/2000 条封顶

设计要点：
  - 两段式：①纯预扫（所有结构性检查，全部通过才开始写）→ ②流式写盘（8KB 块 + 实字节计数）。
    任何异常 → 调用方 rmtree(dest)，磁盘零残留。
  - 绝不 extractall；绝不保留可执行位（写盘一律 0o644）。
  - 目录条目只建不校验扩展名；SKIP_DIRS 整目录跳过（补充 4 提示对应）。

纯函数：输入 bytes/路径/Settings，输出 ExtractResult 或抛 ZipSafetyError，无 FastAPI/asyncio 依赖。
"""
from __future__ import annotations

import io
import re
import stat
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Literal

# 危险类型（deny → 整包拒绝）
DENY_EXT = {
    ".exe", ".com", ".bat", ".cmd", ".sh", ".bash", ".ps1", ".vbs",
    ".dll", ".so", ".dylib", ".msi", ".jar", ".py", ".rb", ".pl", ".php", ".js",
}
# LaTeX 项目白名单（allow → 解压）
ALLOW_EXT = {
    ".tex", ".cls", ".sty", ".bst", ".bib", ".bbl", ".blg", ".def", ".clo",
    ".fd", ".cfg", ".ist", ".dtx", ".ins",
    ".png", ".jpg", ".jpeg", ".pdf", ".eps", ".svg",
    ".txt", ".md", ".csv", ".dat", ".ttf", ".otf",
}
# 无扩展名白名单
ALLOW_BARE = {"README", "LICENSE", "Makefile", ".gitignore"}
# 整目录跳过（不进 skipped 计数之外的拒绝路径，直接 skip）
SKIP_DIRS = {".git", "node_modules", "__MACOSX"}


class ZipSafetyError(Exception):
    """zip 不安全/超限/损坏。code ∈ {ZIP_UNSAFE, ZIP_BOMB, ZIP_INVALID}。"""

    def __init__(self, code: str, message: str, entry: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.entry = entry


@dataclass
class SkippedEntry:
    name: str
    reason: str


@dataclass
class ExtractResult:
    files: int = 0
    tex: int = 0
    images: int = 0
    cls: int = 0
    bib: int = 0
    other: int = 0
    bytes: int = 0
    skipped: list[SkippedEntry] = field(default_factory=list)
    skipped_count: int = 0


_IMG_EXT = {".png", ".jpg", ".jpeg", ".eps", ".svg"}
_BARE_RE = re.compile(r"^[A-Za-z]:")   # 盘符
_CTRL_RE = re.compile(r"[\x00-\x1f]")  # 控制字符


def sanitize_entry_name(raw: str) -> str:
    """归一 + 校验条目名，返回 posix 相对路径；不安全 → ZIP_UNSAFE。

    拒绝：绝对路径（/ 或 \\ 开头）、盘符（C:）、任一段 `..`、空段、NUL/控制字符。
    """
    name = raw.replace("\\", "/")
    if not name or name in ("/", "."):
        raise ZipSafetyError("ZIP_UNSAFE", f"zip 含空条目名：{raw!r}", raw)
    if name.startswith("/") or _BARE_RE.match(name):
        raise ZipSafetyError("ZIP_UNSAFE", f"zip 含绝对路径条目：{raw}", raw)
    if _CTRL_RE.search(name):
        raise ZipSafetyError("ZIP_UNSAFE", f"zip 条目名含控制字符：{raw!r}", raw)
    segs = name.split("/")
    if any(s == ".." for s in segs):
        raise ZipSafetyError("ZIP_UNSAFE", f"zip 含路径穿越条目：{raw}", raw)
    # 空段（//）拒绝；尾段为 ""（目录 / 结尾）在除末位外的段上已由 split 覆盖
    if any(s == "" for s in segs[:-1]) or (segs and segs[0] == ""):
        raise ZipSafetyError("ZIP_UNSAFE", f"zip 含非法路径条目：{raw}", raw)
    depth = len([s for s in segs if s])
    if depth > 16:
        raise ZipSafetyError("ZIP_UNSAFE", f"zip 条目路径过深（{depth} 层）：{raw}", raw)
    if len(name) > 200:
        raise ZipSafetyError("ZIP_UNSAFE", f"zip 条目路径过长（{len(name)} 字符）：{raw}", raw)
    return "/".join(s for s in segs if s)


def classify_entry(name: str, is_dir: bool) -> Literal["allow", "skip", "deny"]:
    """条目分类：目录 → allow（只建）；DENY_EXT → deny（整包拒绝）；白名单 → allow；其余 → skip。"""
    if is_dir:
        return "allow"
    segs = name.replace("\\", "/").split("/")
    # 中间目录段命中 SKIP_DIRS → skip（.git/node_modules 整树跳过）
    if any(seg in SKIP_DIRS for seg in segs[:-1]):
        return "skip"
    p = PurePosixPath(name)
    ext = p.suffix.lower()
    # 多重扩展如 .synctex.gz：取最后一段即可（gz 不在白名单 → skip）
    if ext in DENY_EXT:
        return "deny"
    if ext in ALLOW_EXT:
        return "allow"
    if p.name.upper() in {b.upper() for b in ALLOW_BARE}:
        return "allow"
    return "skip"


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    """external_attr 高 16 位为 Unix mode；S_IFMT == S_IFLNK(0xA000) → 符号链接。"""
    mode = (info.external_attr >> 16) & 0xFFFF
    return stat.S_IFMT(mode) == stat.S_IFLNK


def _has_exec_bit(info: zipfile.ZipInfo) -> bool:
    mode = (info.external_attr >> 16) & 0xFFFF
    # Windows 造的 zip（external_attr 无 Unix mode）mode 可能为 0 → 无 x 位
    return bool(mode & 0o111)


def _prescan(zf: zipfile.ZipFile, settings) -> list[tuple[zipfile.ZipInfo, str]]:
    """预扫中央目录：条目数 → 路径 → 符号链接/可执行位 → 分类 → 声明大小/压缩比。

    全部通过才允许写盘（两段式第一段）。
    """
    infos = zf.infolist()
    if len(infos) > settings.max_zip_entries:
        raise ZipSafetyError(
            "ZIP_BOMB", f"zip 条目数超限（{len(infos)} > {settings.max_zip_entries}）")

    plan: list[tuple[zipfile.ZipInfo, str]] = []
    declared_total = 0
    for info in infos:
        is_dir = info.is_dir()
        name = sanitize_entry_name(info.filename)
        if is_dir:
            continue
        # #3 符号链接 → 整包拒绝
        if _is_symlink(info):
            raise ZipSafetyError("ZIP_UNSAFE", f"zip 含符号链接条目：{info.filename}", info.filename)
        # #4/#6 分类
        kind = classify_entry(name, is_dir=False)
        if kind == "deny":
            raise ZipSafetyError("ZIP_UNSAFE", f"zip 含危险条目：{info.filename}", info.filename)
        if kind == "skip":
            continue
        # #5 可执行位 → 整包拒绝（仅普通文件；目录已 continue）
        if _has_exec_bit(info):
            raise ZipSafetyError(
                "ZIP_UNSAFE", f"zip 含可执行权限条目：{info.filename}", info.filename)
        # #9 单条目声明大小
        if info.file_size > settings.max_zip_entry_bytes:
            raise ZipSafetyError(
                "ZIP_BOMB",
                f"zip 条目过大：{info.filename}（{info.file_size} 字节）", info.filename)
        # #7 压缩比（compress_size=0 且 file_size>0 视为异常）
        if info.file_size > 0:
            cs = info.compress_size
            if cs == 0 or info.file_size / cs > settings.max_zip_ratio:
                raise ZipSafetyError(
                    "ZIP_BOMB",
                    f"zip 条目压缩比异常（疑似 zip bomb）：{info.filename}", info.filename)
        declared_total += info.file_size
        # #8 声明累计
        if declared_total > settings.max_extract_bytes:
            raise ZipSafetyError(
                "ZIP_BOMB",
                f"zip 解压总大小超限（声明 {declared_total} > "
                f"{settings.max_extract_bytes} 字节）")
        plan.append((info, name))
    return plan


def safe_extract(zip_bytes: bytes, dest: Path, settings) -> ExtractResult:
    """安全解压 zip_bytes → dest（新建目录）。

    两段式：①预扫（结构性检查全过）②流式写盘（8KB 块 + 实字节计数）。
    任何 ZipSafetyError → 调用方负责 rmtree(dest) 清残留。
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as exc:
        raise ZipSafetyError("ZIP_INVALID", f"zip 文件损坏或不是有效 zip：{exc}") from exc

    result = ExtractResult()
    with zf:
        # ---- 第一段：纯预扫 ----
        plan = _prescan(zf, settings)

        # ---- 第二段：流式写盘 ----
        dest.mkdir(parents=True, exist_ok=True)
        written_total = 0
        for info, rel in plan:
            target = dest / rel
            # #1 双保险：resolve 后必须仍在 dest 内
            resolved_parent = target.parent.resolve()
            if not str(resolved_parent).startswith(str(dest.resolve())):
                raise ZipSafetyError(
                    "ZIP_UNSAFE", f"zip 条目逃逸目标目录：{info.filename}", info.filename)
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                src = zf.open(info)
            except RuntimeError as exc:  # 加密条目
                raise ZipSafetyError("ZIP_INVALID", f"zip 条目受密码保护：{info.filename}") from exc
            try:
                with src, open(target, "wb") as out:
                    while True:
                        chunk = src.read(8192)
                        if not chunk:
                            break
                        # #8/#13 实写字节计数（不信头部声明）
                        written_total += len(chunk)
                        if written_total > settings.max_extract_bytes:
                            raise ZipSafetyError(
                                "ZIP_BOMB",
                                f"zip 实际解压超限（> {settings.max_extract_bytes} 字节），已中止")
                        out.write(chunk)
            except zipfile.BadZipFile as exc:  # CRC 校验失败（#12/#13）
                raise ZipSafetyError(
                    "ZIP_INVALID", f"zip 条目损坏（CRC 校验失败）：{info.filename}",
                    info.filename) from exc
            # #5 绝不保留可执行位
            target.chmod(0o644)
            result.files += 1
            _tally(result, rel)
        result.bytes = written_total  # 实写累计（#8 计数器终值）

        # 跳过统计（限 20 条 + 总数）
        skipped: list[SkippedEntry] = []
        skipped_count = 0
        for info in zf.infolist():
            if info.is_dir():
                continue
            try:
                name = sanitize_entry_name(info.filename)
            except ZipSafetyError:
                continue  # 预扫已拒绝，不会到这里
            if classify_entry(name, is_dir=False) == "skip":
                skipped_count += 1
                if len(skipped) < 20:
                    skipped.append(SkippedEntry(name=name, reason="unsupported"))
        result.skipped = skipped
        result.skipped_count = skipped_count

    return result


def _tally(result: ExtractResult, rel: str) -> None:
    ext = PurePosixPath(rel).suffix.lower()
    if ext == ".tex":
        result.tex += 1
    elif ext in _IMG_EXT or ext == ".png":
        result.images += 1
    elif ext == ".cls":
        result.cls += 1
    elif ext == ".bib":
        result.bib += 1
    else:
        result.other += 1
