"""Utilities for converting uploaded PDF files to DOCX bytes."""

from __future__ import annotations

import io
import re
from docx import Document as DocxDocument
from pypdf import PdfReader


class PdfConversionError(Exception):
    """Raised when a PDF file cannot be converted into usable DOCX content."""


def _clean_line(line: str) -> str:
    line = re.sub(r"\s+", " ", line).strip()
    return line


def convert_pdf_bytes_to_docx_bytes(pdf_bytes: bytes, source_name: str = "") -> bytes:
    """
    Convert PDF bytes into DOCX bytes using extracted text.

    Notes:
    - This conversion extracts text content only.
    - Image-only (scanned) PDFs may fail if no OCR text is embedded.
    """
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
    except Exception as exc:
        raise PdfConversionError(f"無法讀取 PDF：{exc}") from exc

    lines: list[str] = []
    for page_index, page in enumerate(reader.pages, start=1):
        try:
            page_text = page.extract_text() or ""
        except Exception as exc:
            raise PdfConversionError(f"第 {page_index} 頁讀取失敗：{exc}") from exc

        cleaned_page_lines = [_clean_line(line) for line in page_text.splitlines()]
        cleaned_page_lines = [line for line in cleaned_page_lines if line]
        if cleaned_page_lines:
            # Preserve page boundary for downstream citation and chunk relevance.
            lines.append(f"[第 {page_index} 頁]")
            lines.extend(cleaned_page_lines)

    if not lines:
        display_name = source_name or "此 PDF"
        raise PdfConversionError(f"{display_name} 沒有可提取文字（可能為掃描圖片 PDF）")

    doc = DocxDocument()
    for line in lines:
        doc.add_paragraph(line)

    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()
