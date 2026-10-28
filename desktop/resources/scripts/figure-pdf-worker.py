"""One-shot local PDF import. No HTTP service, model requests or user Python setup."""
import base64
import binascii
import json
import math
import os
from pathlib import Path
import re
import sys
import time
import uuid
import xml.etree.ElementTree as ET

import pymupdf

MAX_PDF = 50_000_000
MAX_OUTPUT = 60_000_000
MAX_PAGES = 100
MAX_SELECTED = 20
TTL = 24 * 3600


def fail(zh, en, lang):
    raise ValueError(en if lang == 'en' else zh)


def controlled_root(state):
    root = Path(state)
    if not root.is_absolute() or root == Path(root.anchor):
        raise ValueError('Invalid PDF cache location')
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def token_paths(root, token):
    if not isinstance(token, str) or not re.fullmatch(r'[a-f0-9]{32}', token):
        raise ValueError('Invalid PDF import token')
    return root / (token + '.pdf'), root / (token + '.json')


def clean_cache(root):
    # Only our own flat, expired cache files are removed; never follow links or directories.
    now = time.time()
    for file in root.iterdir():
        if file.is_symlink() or not re.fullmatch(r'[a-f0-9]{32}\.(pdf|json)', file.name):
            continue
        try:
            if file.is_file() and now - file.stat().st_mtime > TTL:
                file.unlink()
        except OSError:
            pass


def open_pdf(raw, lang):
    try:
        doc = pymupdf.open(stream=raw, filetype='pdf')
    except Exception:
        fail('PDF 已损坏或不是可读取的 PDF 文件。', 'This PDF is damaged or cannot be read.', lang)
    if doc.needs_pass:
        doc.close()
        fail('PDF 已加密，请先在本机解锁后重新导入。', 'This PDF is encrypted. Unlock it locally before importing.', lang)
    if not 1 <= len(doc) <= MAX_PAGES:
        doc.close()
        fail('请选择 1–100 页的 PDF；较长文件请先拆分。', 'Choose a PDF with 1–100 pages; split larger documents first.', lang)
    return doc


def image_url(page, longest, dpi=150, warnings=None, lang=None):
    rect = page.rect
    if not all(math.isfinite(v) and 0 < v <= 100_000 for v in (rect.width, rect.height)):
        raise ValueError('Invalid PDF page dimensions')
    scale = min(dpi / 72, longest / max(rect.width, rect.height))
    for attempt in range(6):
        pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=True)
        if pix.width * pix.height > 16_000_000:
            raise ValueError('PDF page image is too large')
        image = pix.tobytes('png')
        if len(image) <= 11_500_000:
            if attempt and warnings is not None:
                warnings.append('导入图像已降低分辨率以满足大小限制。' if lang != 'en' else 'Image resolution was reduced to fit the import size limit.')
            return 'data:image/png;base64,' + base64.b64encode(image).decode('ascii')
        scale *= 0.7
    raise ValueError('PDF page image is too large')


def inspect(body, root):
    lang = body.get('lang')
    value = body.get('dataUrl', '')
    prefix = 'data:application/pdf;base64,'
    if not isinstance(value, str) or not value.startswith(prefix) or len(value) > 67_000_000:
        fail('请选择不超过 50 MB 的 PDF 文件。', 'Choose a PDF no larger than 50 MB.', lang)
    try:
        raw = base64.b64decode(value[len(prefix):], validate=True)
    except (ValueError, binascii.Error):
        fail('PDF 文件内容无效，请重新选择。', 'The PDF data is invalid. Select the file again.', lang)
    if not raw.startswith(b'%PDF-') or len(raw) > MAX_PDF:
        fail('请选择不超过 50 MB 的 PDF 文件。', 'Choose a PDF no larger than 50 MB.', lang)
    clean_cache(root)
    if sum(f.stat().st_size for f in root.glob('*.pdf') if f.is_file() and not f.is_symlink()) + len(raw) > 250_000_000:
        fail('临时 PDF 缓存已满，请稍后重试；缓存会在 24 小时后过期。', 'The temporary PDF cache is full. Cached imports expire after 24 hours.', lang)
    with open_pdf(raw, lang) as doc:
        pages = [{'page': i + 1, 'width': page.rect.width, 'height': page.rect.height,
                  'preview': image_url(page, 180, 72)} for i, page in enumerate(doc)]
    name = str(body.get('name') or 'PDF')[:240].replace('\\', '/').split('/')[-1]
    token = uuid.uuid4().hex
    pdf, meta = token_paths(root, token)
    try:
        with pdf.open('xb') as file:
            file.write(raw)
        with meta.open('x', encoding='utf-8') as file:
            json.dump({'name': name, 'created': time.time(), 'bytes': len(raw)}, file)
    except Exception:
        pdf.unlink(missing_ok=True)
        meta.unlink(missing_ok=True)
        raise
    return {'token': token, 'name': name, 'totalPages': len(pages), 'pages': pages, 'warnings': []}


def import_pages(body, root):
    lang = body.get('lang')
    pdf, metadata = token_paths(root, body.get('token'))
    if pdf.is_symlink() or metadata.is_symlink():
        raise ValueError('Invalid PDF import token')
    try:
        meta = json.loads(metadata.read_text('utf-8'))
        if time.time() - meta['created'] > TTL or pdf.stat().st_size > MAX_PDF:
            raise ValueError()
        raw = pdf.read_bytes()
    except (OSError, ValueError, KeyError):
        fail('PDF 导入已过期，请重新选择文件。', 'This PDF import has expired. Select the file again.', lang)
    pages, mode = body.get('pages'), body.get('mode')
    if mode not in ('editable', 'page') or not isinstance(pages, list) or not 1 <= len(pages) <= MAX_SELECTED:
        fail('请一次选择 1–20 页和有效导入方式。', 'Select 1–20 pages and a valid import mode.', lang)
    if any(type(i) is not int for i in pages) or len(set(pages)) != len(pages):
        fail('选择的页码无效。', 'The selected page numbers are invalid.', lang)
    results, total = [], 0
    with open_pdf(raw, lang) as doc:
        if any(i < 1 or i > len(doc) for i in pages):
            fail('选择的页码不存在。', 'A selected page does not exist.', lang)
        for index in pages:
            page = doc[index - 1]
            warnings = []
            item = {'name': f"{meta['name']} · {index}", 'page': index,
                    'width': page.rect.width, 'height': page.rect.height, 'warnings': warnings}
            if mode == 'editable':
                try:
                    text = page.get_text()
                    outlined = '\ufffd' in text
                    svg = page.get_svg_image(text_as_path=outlined)
                    if len(svg.encode('utf-8')) > 15_000_000:
                        raise ValueError('large SVG')
                    item['svg'] = svg
                    if text.strip():
                        known = {name.casefold() for name in body.get('availableFonts', []) if isinstance(name, str)}
                        used = {element.get('font-family', '').strip(" '\"") for element in ET.fromstring(svg).iter() if element.tag.endswith('}text')}
                        missing = sorted(name for name in used if name and known and name.casefold() not in known)
                        if missing:
                            names = ', '.join(missing)[:180]
                            warnings.append(('本机未找到 PDF 字体：' + names + '；请核对替换效果或使用整页兼容导入。') if lang != 'en' else
                                            ('PDF fonts not found locally: ' + names + '. Check substitutions or use whole-page import.'))
                        warnings.append(('文字编码异常，已转为轮廓保留外观；轮廓文字不能直接改字。' if outlined else
                                         'PDF 字体可能在本机替换，请核对文字；外观异常时可改用整页兼容导入。') if lang != 'en' else
                                        ('Text encoding was invalid; text was outlined to preserve appearance and is no longer directly editable.' if outlined else
                                         'PDF fonts may be substituted locally. Check text appearance or use whole-page import.'))
                except Exception:
                    item['dataUrl'] = image_url(page, 4000, 200, warnings, lang)
                    warnings.append('此页矢量转换未完成，已按整页图像导入。' if lang != 'en' else 'Vector conversion was unavailable; this page was imported as an image.')
            else:
                item['dataUrl'] = image_url(page, 4000, 200, warnings, lang)
            total += len(item.get('svg', '').encode('utf-8')) + len(item.get('dataUrl', ''))
            if total > MAX_OUTPUT:
                fail('所选页面过大，请减少页数后重试。', 'Selected pages are too large. Import fewer pages.', lang)
            results.append(item)
    return {'items': results, 'importedPages': len(results)}


def run(body):
    root = controlled_root(body['state'])
    if body.get('action') == 'inspect':
        return inspect(body, root)
    if body.get('action') == 'import':
        return import_pages(body, root)
    raise ValueError('Unsupported PDF action')


if __name__ == '__main__':
    try:
        request = sys.stdin.buffer.read(68_000_001)
        if len(request) > 68_000_000:
            raise ValueError('PDF request is too large')
        result = run(json.loads(request))
        output = json.dumps(result, ensure_ascii=False)
        if len(output.encode('utf-8')) > 65_000_000:
            raise ValueError('PDF result is too large')
        print(output)
    except Exception as error:
        print(json.dumps({'error': str(error) or 'PDF import failed'}, ensure_ascii=False))
        sys.exit(1)
