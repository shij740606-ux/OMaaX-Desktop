"""Local PDF extraction; uploaded papers never go to a parsing server."""
import asyncio
from pathlib import Path
import pymupdf
from ..config import get_settings
class ParseError(Exception):
    pass
async def parse_pdf(path: str) -> str:
    return await parse_pdf_bytes(Path(path).read_bytes())
async def parse_pdf_bytes(file_bytes: bytes, filename: str = "paper.pdf") -> str:
    if not file_bytes or len(file_bytes) > get_settings().max_pdf_bytes:
        raise ParseError("PDF 为空或超过 50 MB")
    def extract():
        try:
            with pymupdf.open(stream=file_bytes, filetype="pdf") as doc:
                if doc.needs_pass: raise ParseError("请先解密 PDF")
                text = "\n".join(page.get_text() for page in doc).strip()
        except ParseError: raise
        except Exception as exc: raise ParseError("PDF 无法解析，请检查文件") from exc
        if not text: raise ParseError("PDF 没有可读取文字，请使用文本型 PDF")
        return text
    return await asyncio.to_thread(extract)
