"""services/format_fetcher.py —— 投稿指南页面抓取（格式规则自动获取·第一批）。

约束（用户拍板 2026-10-10）：
- 零新依赖：httpx + beautifulsoup4（venv 已装 4.15.0，用户明确指定使用；
  lxml 6.1.2 在 requirements.lock 内，作 bs4 解析器）
- 同域限速 ≥ 2s（robots.txt 与正文请求共用计时器）
- 抓正文前先 GET /robots.txt，命中 Disallow → ok=False 跳过
- UA: "PlantAI-RAG-Bot/1.0 (+contact: admin@example.com)"
- 只提取正文（去导航/页脚/脚本），截断至 30000 字符

返回固定四键：{"ok": bool, "markdown": str, "error": str | None, "fetched_at": str}
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup, NavigableString

logger = logging.getLogger(__name__)

USER_AGENT = "PlantAI-RAG-Bot/1.0 (+contact: admin@example.com)"
TIMEOUT = 30.0
MAX_CHARS = 30_000
MIN_INTERVAL_PER_HOST = 2.0  # 同域请求间隔 ≥ 2s（robots 与正文共用）
_MIN_TEXT_CHARS = 200        # 正文短于此 → 疑似 JS 渲染/反爬页
_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# 同域限速：host → 上次请求完成时刻（monotonic）
_last_hit: dict[str, float] = {}
_throttle_lock = asyncio.Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _fail(error: str) -> dict:
    return {"ok": False, "markdown": "", "error": error, "fetched_at": _now_iso()}


async def _throttle(host: str) -> None:
    """同域串行 + ≥2s 间隔（全局锁：本批抓取量小，简单可靠优先）。"""
    async with _throttle_lock:
        now = time.monotonic()
        wait = _last_hit.get(host, now) + MIN_INTERVAL_PER_HOST - now
        if wait > 0:
            await asyncio.sleep(wait)
        _last_hit[host] = time.monotonic()


# ---------------------------------------------------------------- robots.txt

def _parse_robots(text: str) -> list[tuple[list[str], list[tuple[str, str]]]]:
    """→ [(agents, [(kind, pattern)...]), ...]（连续 UA 行同组；规则行后出现 UA=新组）。"""
    groups: list[tuple[list[str], list[tuple[str, str]]]] = []
    agents: list[str] = []
    rules: list[tuple[str, str]] = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key, value = key.strip().lower(), value.strip()
        if key == "user-agent":
            if agents and rules:  # 上一组已出规则 → 当前 UA 行开新组
                groups.append((agents, rules))
                agents, rules = [], []
            agents.append(value)
        elif key in ("allow", "disallow"):
            if not agents:  # 无前置 UA 的规则按 * 组处理
                agents = ["*"]
            rules.append((key, value))
    if agents:
        groups.append((agents, rules))
    return groups


def _robots_path_allowed(groups, path: str) -> tuple[bool, str]:
    """最长匹配决胜（同长 Allow 优先；`$` 结尾=精确匹配；空 Disallow 跳过）。"""

    def matched_by(pat: str) -> bool:
        if pat.endswith("$"):
            return path == pat[:-1]
        return path.startswith(pat)

    token = "plantai-rag-bot"
    matched = [rules for agents, rules in groups
               if any(token in a.lower() for a in agents)]
    if not matched:
        matched = [rules for agents, rules in groups
                   if any(a.lower() == "*" for a in agents)]
    if not matched:
        return True, "robots.txt 无匹配组（本 bot 不受限）"

    best_len, best_allow, best_pat = -1, False, ""
    for rules in matched:
        for kind, pat in rules:
            if not pat:  # "Disallow:" 空值 = 放行一切（该组无需处理）
                continue
            if not matched_by(pat):
                continue
            length = len(pat)
            allow = kind == "allow"
            if length > best_len or (length == best_len and allow and not best_allow):
                best_len, best_allow, best_pat = length, allow, pat
    if best_len < 0:
        return True, "robots.txt 允许（无路径匹配）"
    if best_allow:
        return True, f"robots.txt Allow 命中（{best_pat}）"
    return False, f"robots.txt Disallow 命中（{best_pat}）"


async def _robots_allows(client: httpx.AsyncClient, url: str) -> tuple[bool, str]:
    p = urlparse(url)
    robots_url = f"{p.scheme}://{p.netloc}/robots.txt"
    await _throttle(p.netloc.lower())
    try:
        resp = await client.get(robots_url, headers=_HEADERS)
    except httpx.HTTPError as exc:
        return True, f"robots.txt 获取失败（{type(exc).__name__}），按允许处理"
    if resp.status_code != 200:
        return True, f"robots.txt HTTP {resp.status_code}，按允许处理"
    groups = _parse_robots(resp.text)
    return _robots_path_allowed(groups, p.path or "/")


# ---------------------------------------------------------------- 正文提取

_STRIP_TAGS = ("script", "style", "noscript", "iframe", "form", "nav",
               "footer", "aside", "button", "select", "textarea", "input", "svg")
_STRIP_ROLES = ("navigation", "contentinfo", "complementary", "search")
# 正文容器候选（按优先级；首个文本 ≥500 字符者胜出）
_CONTENT_SELECTORS = (
    "main", "article", '[role="main"]', "#content", "#main", "#main-content",
    ".article-body", "#article-body", ".article__body", ".hub-article-body",
    ".page-content", ".main-content", ".content-area",
)
_CHALLENGE_MARKERS = ("just a moment", "attention required", "captcha",
                      "access denied", "ddos protection", "verify you are human")


def _decode_html(resp: httpx.Response) -> str:
    """header charset → <meta charset> → utf-8（errors=replace），防 OUP/Wiley 无 header 乱码。"""
    enc = None
    m = re.search(r"charset=([\w.-]+)", resp.headers.get("content-type", ""), re.I)
    if m:
        enc = m.group(1)
    if not enc:
        m = re.search(rb"""charset\s*=\s*["']?([\w.-]+)""", resp.content[:4096], re.I)
        if m:
            enc = m.group(1).decode("ascii", "ignore")
    try:
        return resp.content.decode(enc or "utf-8", errors="replace")
    except LookupError:
        return resp.content.decode("utf-8", errors="replace")


def _squeeze(s: str) -> str:
    """行内空白压缩 + 去空行（保留单换行）。"""
    lines = [re.sub(r"[ \t\r\f\v]+", " ", line).strip() for line in s.split("\n")]
    return "\n".join(line for line in lines if line)


def _inline(el) -> str:
    """行内渲染：a→md链接 / strong→** / em→* / code→` / br→换行；跳过列表与图片。"""
    parts: list[str] = []
    for child in el.children:
        name = getattr(child, "name", None)
        if name is None:  # NavigableString
            parts.append(str(child))
            continue
        if name in ("ul", "ol", "img", "picture", "figure"):
            continue
        if name == "a":
            href = (child.get("href") or "").strip()
            txt = _inline(child).strip()
            if not txt:
                continue
            parts.append(f"[{txt}]({href})" if href.startswith(("http://", "https://")) else txt)
        elif name in ("strong", "b"):
            parts.append(f"**{_inline(child)}**")
        elif name in ("em", "i"):
            parts.append(f"*{_inline(child)}*")
        elif name == "code":
            parts.append(f"`{_inline(child).strip()}`")
        elif name == "br":
            parts.append("\n")
        else:
            parts.append(_inline(child))
    return "".join(parts)


def _list_md(el, ordered: bool) -> str:
    lines: list[str] = []
    idx = 1
    for li in el.find_all("li", recursive=False):
        # li 文本不含嵌套列表（嵌套列表随后缩进输出）
        buf = []
        for child in li.children:
            if getattr(child, "name", None) in ("ul", "ol"):
                continue
            buf.append(_inline(child) if getattr(child, "name", None) else str(child))
        txt = " ".join("".join(buf).split())
        if txt:
            lines.append(f"{idx}. {txt}" if ordered else f"- {txt}")
            idx += 1
        for nested in li.find_all(("ul", "ol"), recursive=False):
            sub = _list_md(nested, nested.name == "ol")
            if sub:
                lines.extend("  " + line for line in sub.split("\n"))
    return "\n".join(lines)


def _table_md(table) -> str | None:
    rows: list[list[str]] = []
    for tr in table.find_all("tr"):
        cells = [" ".join(_squeeze(_inline(c)).split())
                 for c in tr.find_all(("td", "th"))]
        if cells:
            rows.append(cells)
    if not rows:
        return None
    out = ["| " + " | ".join(rows[0]) + " |"]
    if table.find("th"):  # 首行含表头 → 补分隔行（合法 GFM 表）
        out.append("| " + " | ".join(["---"] * len(rows[0])) + " |")
    out += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return "\n".join(out)


def _blocks(el, out: list[str]) -> None:
    for child in el.children:
        name = getattr(child, "name", None)
        if name is None:  # 裸文本
            txt = _squeeze(str(child))
            if txt:
                out.append(txt)
            continue
        if name in _STRIP_TAGS:
            continue
        if name in ("h1", "h2", "h3", "h4", "h5", "h6"):
            txt = " ".join(_squeeze(_inline(child)).split())
            if txt:
                out.append("#" * int(name[1]) + " " + txt)
        elif name == "p":
            txt = _squeeze(_inline(child))
            if txt:
                out.append(txt)
        elif name in ("ul", "ol"):
            md = _list_md(child, name == "ol")
            if md:
                out.append(md)
        elif name == "table":
            md = _table_md(child)
            if md:
                out.append(md)
        elif name == "blockquote":
            txt = _squeeze(_inline(child))
            if txt:
                out.append("\n".join("> " + line for line in txt.split("\n")))
        elif name == "pre":
            code = child.get_text().strip("\n")
            if code.strip():
                out.append("```\n" + code + "\n```")
        elif name == "hr":
            out.append("---")
        else:  # div/section/article/main/figure/dl/details/… 容器递归
            _blocks(child, out)


def _html_to_markdown(html: str) -> tuple[str, str | None]:
    """→ (markdown, error)。选正文容器 → 去壳 → DOM 递归转 Markdown。"""
    soup = BeautifulSoup(html, "lxml")  # lxml 在 lock 内；缺时 bs4 自动回退 html.parser

    title = (soup.title.get_text() if soup.title else "") or ""
    for tag in soup.find_all(_STRIP_TAGS):
        tag.decompose()
    for tag in soup.find_all(attrs={"role": _STRIP_ROLES}):
        tag.decompose()

    container = None
    fallback_hits = []
    for sel in _CONTENT_SELECTORS:
        for node in soup.select(sel):
            text_len = len(node.get_text(" ", strip=True))
            fallback_hits.append((text_len, node))
            if text_len >= 500:
                container = node
                break
        if container is not None:
            break
    if container is None:
        if fallback_hits:
            container = max(fallback_hits, key=lambda x: x[0])[1]
        else:
            container = soup.body or soup

    blocks: list[str] = []
    _blocks(container, blocks)
    md = "\n\n".join(blocks)

    probe = (title + " " + md[:500]).lower()
    marker = next((m for m in _CHALLENGE_MARKERS if m in probe), None)
    if marker:
        return "", f"疑似反爬/挑战页（命中 {marker!r}，title={title[:80]!r}）"
    if len(md) < _MIN_TEXT_CHARS:
        return "", f"正文提取过短（{len(md)} < {_MIN_TEXT_CHARS} 字符），疑似 JS 渲染或空壳页"
    if len(md) > MAX_CHARS:
        md = md[:MAX_CHARS - 1] + "…"
    return md, None


# ---------------------------------------------------------------- 入口

async def fetch_submission_guidelines(url: str) -> dict:
    """抓投稿指南页面 → 正文 Markdown。

    流程：robots.txt 检查（≥2s 限速）→ 禁止即跳过 → GET 正文（≥2s 限速）
    → 状态/类型校验 → 正文提取 → 截断 30000 字符。
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return _fail(f"非法 URL：{url!r}")
    host = parsed.netloc.lower()

    async with httpx.AsyncClient(follow_redirects=True, timeout=TIMEOUT) as client:
        allowed, robots_reason = await _robots_allows(client, url)
        if not allowed:
            logger.info("[fetch] robots 禁止，跳过 %s（%s）", url, robots_reason)
            return _fail(robots_reason)

        await _throttle(host)
        try:
            resp = await client.get(url, headers=_HEADERS)
        except httpx.HTTPError as exc:
            return _fail(f"请求失败（{type(exc).__name__}）：{exc}")
        if resp.status_code != 200:
            return _fail(f"HTTP {resp.status_code}")
        ctype = resp.headers.get("content-type", "")
        if "html" not in ctype and "xhtml" not in ctype:
            return _fail(f"非 HTML 响应：{ctype or '无 Content-Type'}")

        markdown, err = _html_to_markdown(_decode_html(resp))
        if err:
            return _fail(f"{err}（robots: {robots_reason}）")
        logger.info("[fetch] %s → %d 字符（robots: %s）", url, len(markdown), robots_reason)
        return {"ok": True, "markdown": markdown, "error": None, "fetched_at": _now_iso()}
