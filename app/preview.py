"""PDF 페이지 미리보기 (Kitty 프로토콜 이미지 + ASCII 폴백)."""
from __future__ import annotations

import base64
import os
import sys

try:
    import pymupdf
except ImportError:
    pymupdf = None  # type: ignore

from app.config import DEFAULT_PREVIEW_COLS, DEFAULT_PREVIEW_ROWS


class PagePreview:
    """PDF 페이지를 터미널에 미리보기합니다 (Kitty 이미지 또는 ASCII)."""

    def __init__(self, default_cols: int | None = None, default_rows: int | None = None):
        self.default_cols = default_cols if default_cols is not None else DEFAULT_PREVIEW_COLS
        self.default_rows = default_rows if default_rows is not None else DEFAULT_PREVIEW_ROWS

    @staticmethod
    def supports_kitty_graphics() -> bool:
        """터미널이 Kitty 그래픽 프로토콜을 지원하는지 여부."""
        term = os.environ.get("TERM", "").lower()
        program = os.environ.get("TERM_PROGRAM", "").lower()
        return (
            "ghostty" in term or "ghostty" in program
            or "kitty" in term or "kitty" in program
            or "wezterm" in term or "wezterm" in program
        )

    @staticmethod
    def clear_kitty_graphics() -> None:
        """Kitty 그래픽 프로토콜로 그린 이미지를 모두 지웁니다."""
        try:
            sys.stdout.buffer.write(b"\033_Ga=d;\033\\")
            sys.stdout.buffer.flush()
        except (OSError, AttributeError):
            pass

    def render_kitty_bytes(
        self,
        doc: "pymupdf.Document",
        page_no: int,
        display_cols: int,
        display_rows: int,
    ) -> tuple[bytes, int, int] | None:
        """
        페이지를 렌더해 Kitty 프로토콜 바이트와 (실제_cols, 실제_rows)를 반환합니다.
        실패 시 None.
        """
        if pymupdf is None:
            return None
        try:
            page = doc[page_no]
        except IndexError:
            return None

        try:
            page_aspect = page.rect.width / page.rect.height
        except ZeroDivisionError:
            return None

        if display_cols / (display_rows * 2) > page_aspect:
            actual_rows = display_rows
            actual_cols = max(1, int(page_aspect * actual_rows * 2))
        else:
            actual_cols = display_cols
            actual_rows = max(1, int(actual_cols / (page_aspect * 2)))

        pix_w = max(1, min(actual_cols * 8, 800))
        pix_h = max(2, min(actual_rows * 16, 1200))

        try:
            zoom = min(pix_w / page.rect.width, pix_h / page.rect.height) * 0.95
        except ZeroDivisionError:
            return None
        mat = pymupdf.Matrix(zoom, zoom)
        try:
            pix = page.get_pixmap(matrix=mat, alpha=False)
        except Exception:
            return None
        w, h = pix.width, pix.height
        if w == 0 or h == 0:
            return None
        try:
            samples = bytes(pix.samples)
        except Exception:
            return None

        chunk_size = 4092
        raw_size = w * h * 3
        if len(samples) < raw_size:
            return None
        b64 = base64.standard_b64encode(samples[:raw_size])
        out: list[bytes] = []
        first = True
        i = 0
        while i < len(b64):
            end = min(i + chunk_size, len(b64))
            chunk = b64[i:end]
            i = end
            if first:
                meta = f"a=T,f=24,s={w},v={h},c={actual_cols},r={actual_rows},m={1 if i < len(b64) else 0};"
                first = False
            else:
                meta = f"m={1 if i < len(b64) else 0};"
            out.append(f"\033_G{meta}".encode("ascii"))
            out.append(chunk)
            out.append(b"\033\\")
        return (b"".join(out), actual_cols, actual_rows)

    def render_ascii_lines(
        self,
        doc: "pymupdf.Document",
        page_no: int,
        max_cols: int | None = None,
        max_rows: int | None = None,
    ) -> tuple[list[str], int]:
        """페이지를 픽셀맵으로 렌더한 뒤 ASCII 줄 목록과 실제 너비를 반환합니다."""
        if pymupdf is None:
            return (["(pymupdf 없음)"], 14)
        cols = max_cols if max_cols is not None else self.default_cols
        rows = max_rows if max_rows is not None else self.default_rows

        try:
            page = doc[page_no]
        except IndexError:
            return (["(유효하지 않은 페이지)"], 20)

        try:
            page_aspect = page.rect.width / page.rect.height
        except ZeroDivisionError:
            return (["(빈 페이지)"], 12)

        if cols / (rows * 2) > page_aspect:
            actual_rows = rows
            actual_cols = max(1, int(page_aspect * actual_rows * 2))
        else:
            actual_cols = cols
            actual_rows = max(1, int(actual_cols / (page_aspect * 2)))

        pix_w = max(1, actual_cols)
        pix_h = max(2, actual_rows * 2)

        try:
            zoom = min(pix_w / page.rect.width, pix_h / page.rect.height) * 0.95
        except ZeroDivisionError:
            return (["(빈 페이지)"], 12)
        mat = pymupdf.Matrix(zoom, zoom)
        try:
            pix = page.get_pixmap(matrix=mat, alpha=False)
        except Exception:
            return (["(미리보기 실패)"], 14)
        w, h = pix.width, pix.height
        if w == 0 or h == 0:
            return (["(빈 페이지)"], 12)
        try:
            stride = pix.stride
            samples = pix.samples
        except Exception:
            return (["(미리보기 실패)"], 14)

        shades = " .:-=+*#@"
        lines: list[str] = []
        row_height = 2
        col_start = max(0, (w - actual_cols) // 2) if w > actual_cols else 0

        def gray(y: int, x: int) -> int:
            if y >= h or x >= w or x < 0:
                return 255
            idx = y * stride + x * 3
            if idx + 2 >= len(samples):
                return 255
            r, g, b = samples[idx], samples[idx + 1], samples[idx + 2]
            return int(0.299 * r + 0.587 * g + 0.114 * b)

        for row in range(0, min(h, pix_h), row_height):
            line_chars = []
            for c in range(actual_cols):
                col = col_start + c
                top = gray(row, col)
                bottom = gray(row + 1, col) if row + 1 < h else 255
                g = (top + bottom) // 2
                idx = min(9, int((255 - g) / 255.0 * (len(shades) - 1)))
                line_chars.append(shades[idx])
            lines.append("".join(line_chars))
        return (lines[:actual_rows] if lines else ["(미리보기)"], actual_cols)
