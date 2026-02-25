"""PDF 선택 및 페이지 추출 TUI (vim 스타일 키보드 조작)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import curses

try:
    import pymupdf
except ImportError:
    pymupdf = None  # type: ignore

from app.config import DEFAULT_DIR
from app.pdf_extractor import PDFImageExtractor
from app.preview import PagePreview


def get_entries(path: Path) -> list[tuple[str, Path, bool]]:
    """(표시이름, 절대경로, 디렉터리여부) 목록. 상위(..), 디렉터리, PDF만."""
    path = path.resolve()
    result: list[tuple[str, Path, bool]] = []
    if path != path.parent:
        result.append(("..", path.parent, True))
    try:
        names = sorted(os.listdir(path))
    except OSError:
        return result
    dirs: list[tuple[str, Path, bool]] = []
    pdfs: list[tuple[str, Path, bool]] = []
    for n in names:
        full = path / n
        try:
            if full.is_dir():
                dirs.append((n, full, True))
            elif full.suffix.lower() == ".pdf":
                pdfs.append((n, full, False))
        except OSError:
            continue
    result.extend(dirs)
    result.extend(pdfs)
    return result


class ImageExtractorTUI:
    """TUI 앱: 디렉터리 탐색 → PDF 선택 → 페이지 번호 입력 → 이미지 추출."""

    def __init__(self, initial_dir: Path | None = None):
        self.initial_dir = Path(initial_dir) if initial_dir else DEFAULT_DIR
        self.extractor = PDFImageExtractor()
        self.preview = PagePreview()

    def run(self) -> int:
        """TUI를 실행합니다. 반환값은 종료 코드(0 성공, 1 오류)."""
        if not sys.stdin.isatty():
            print("TUI는 실제 터미널에서 실행해 주세요.", file=sys.stderr)
            return 1
        try:
            curses.wrapper(self._run_tui)
        except KeyboardInterrupt:
            pass
        return 0

    def _run_tui(self, stdscr: "curses.window") -> None:
        stdscr.keypad(True)
        try:
            curses.start_color()
            curses.use_default_colors()
            curses.init_pair(1, curses.COLOR_CYAN, -1)
            curses.init_pair(2, curses.COLOR_WHITE, curses.COLOR_BLUE)
            curses.init_pair(3, curses.COLOR_GREEN, -1)
            curses.init_pair(4, curses.COLOR_YELLOW, -1)
            # 포커스 필드 하이라이트 (전경 검정, 배경 청록)
            curses.init_pair(5, curses.COLOR_BLACK, curses.COLOR_CYAN)
        except Exception:
            pass

        current_dir = self.initial_dir
        if not current_dir.exists():
            current_dir = Path.home()
        selected = 0
        pdf_path: Path | None = None
        doc: "pymupdf.Document | None" = None
        page_input = ""
        margin_input = "2.5"
        focus_page = True  # True=페이지 번호, False=여백
        message = ""
        status_line = ""
        mode = "browse"
        MARGIN_MIN, MARGIN_MAX, MARGIN_STEP = -50.0, 100.0, 0.5
        last_mode: str | None = None
        kitty_preview_bytes: bytes | None = None
        img_cols = 0

        while True:
            kitty_preview_bytes = None
            img_cols = 0
            if last_mode != mode:
                if self.preview.supports_kitty_graphics():
                    self.preview.clear_kitty_graphics()
                stdscr.clear()
                last_mode = mode
                curses.curs_set(1 if mode == "page_input" else 0)
            else:
                stdscr.erase()
            h, w = stdscr.getmaxyx()

            if mode == "browse":
                entries = get_entries(current_dir)
                if selected >= len(entries):
                    selected = max(0, len(entries) - 1)
                if selected < 0:
                    selected = 0

                title = f" PDF 선택 (↑/↓ 이동, Enter 선택, q 뒤로/종료) | {current_dir} "
                try:
                    stdscr.addstr(0, 0, title[: w - 1], curses.color_pair(1) | curses.A_BOLD)
                except curses.error:
                    pass
                start = 1
                for i, (name, full, is_dir) in enumerate(entries):
                    row = start + i
                    if row >= h - 1:
                        break
                    prefix = "[DIR] " if is_dir else "[PDF] "
                    text = prefix + name
                    if len(text) > w - 1:
                        text = text[: w - 2]
                    try:
                        if i == selected:
                            stdscr.addstr(row, 0, text, curses.color_pair(2))
                        else:
                            stdscr.addstr(row, 0, text)
                    except curses.error:
                        pass
                if message:
                    try:
                        stdscr.addstr(h - 1, 0, message[: w - 1], curses.color_pair(3))
                    except curses.error:
                        pass
                    message = ""

            else:
                # mode == "page_input"
                if doc is None or pdf_path is None:
                    mode = "browse"
                    continue
                num_pages = len(doc)
                prompt = f"페이지 번호 (1-{num_pages}): "
                margin_label = " 여백(pt): "
                margin_display = margin_label + margin_input
                # 첫 줄 한 줄에 맞춤 (줄바꿈 방지): 여백을 우측 고정, 왼쪽 초과 시 말줄임
                margin_col = max(w - len(margin_display) - 1, 20)
                max_left_len = margin_col - 1
                left_part = prompt + page_input
                if len(left_part) > max_left_len:
                    left_display = "\u2026" + left_part[-max_left_len + 1 :]
                else:
                    left_display = left_part
                attr_n = curses.color_pair(1) | curses.A_BOLD
                attr_focus = curses.color_pair(5) | curses.A_BOLD
                try:
                    col = 0
                    # 왼쪽: 프롬프트 + 페이지 입력(포커스 시 하이라이트)
                    page_start_in_display = max(0, len(left_display) - len(page_input))
                    if page_start_in_display > 0:
                        stdscr.addstr(0, 0, left_display[:page_start_in_display], attr_n)
                        col = page_start_in_display
                    if col < margin_col:
                        seg = left_display[col:margin_col]
                        stdscr.addstr(0, col, seg, attr_focus if focus_page else attr_n)
                        col += len(seg)
                    if col < margin_col:
                        stdscr.addstr(0, col, " " * (margin_col - col), attr_n)
                    col = margin_col
                    label_len = min(len(margin_label), w - col - 1)
                    if label_len > 0:
                        stdscr.addstr(0, col, margin_label[:label_len], attr_n)
                        col += label_len
                    input_len = min(len(margin_input), w - col - 1)
                    if input_len > 0:
                        stdscr.addstr(0, col, margin_input[:input_len], attr_focus if not focus_page else attr_n)
                    if focus_page:
                        stdscr.move(0, min(len(prompt) + len(page_input), margin_col - 1))
                    else:
                        stdscr.move(0, min(margin_col + len(margin_label) + len(margin_input), w - 1))
                except curses.error:
                    pass
                page_str = page_input.strip() or "0"
                try:
                    page_no = int(page_str)
                except ValueError:
                    page_no = 0
                if 1 <= page_no <= num_pages:
                    status_line = ""
                    preview_rows = max(1, h - 2)
                    preview_cols = max(10, w)
                    kitty_result = None
                    if self.preview.supports_kitty_graphics():
                        kitty_result = self.preview.render_kitty_bytes(
                            doc, page_no - 1,
                            display_cols=preview_cols,
                            display_rows=preview_rows,
                        )
                    if kitty_result is not None:
                        kitty_preview_bytes, img_cols, _ = kitty_result
                    else:
                        try:
                            preview_lines, line_width = self.preview.render_ascii_lines(
                                doc, page_no - 1,
                                max_cols=preview_cols,
                                max_rows=preview_rows,
                            )
                        except Exception:
                            preview_lines, line_width = ["(미리보기 오류)"], 15
                        start_col = max(0, (w - line_width) // 2)
                        for i, line in enumerate(preview_lines):
                            if 1 + i >= h - 1:
                                break
                            try:
                                stdscr.addstr(1 + i, start_col, line)
                            except curses.error:
                                break
                else:
                    status_line = f"1-{num_pages} 사이 번호를 입력하세요."
                last_line = message or status_line
                if last_line:
                    try:
                        stdscr.addstr(h - 1, 0, last_line[: w - 1], curses.color_pair(3))
                    except curses.error:
                        pass
                if message:
                    message = ""

            stdscr.refresh()

            if kitty_preview_bytes is not None:
                try:
                    start_col = max(1, (w - img_cols) // 2 + 1)
                    start_row = 2
                    sys.stdout.buffer.write(f"\033[{start_row};{start_col}H".encode())
                    sys.stdout.buffer.write(kitty_preview_bytes)
                    if mode == "page_input" and doc is not None:
                        num_pages = len(doc)
                        prompt = f"페이지 번호 (1-{num_pages}): "
                        margin_label = "여백(pt): "
                        margin_col = max(w - len(margin_label) - max(8, len(margin_input)) - 1, 20)
                        max_left = margin_col - 1
                        left_part = prompt + page_input
                        left_display = ("\u2026" + left_part[-max_left + 1 :]) if len(left_part) > max_left else left_part
                        pad = margin_col - len(left_display)
                        line_visible = (left_display + " " * max(0, pad) + margin_label + margin_input)[: w - 1]
                        sys.stdout.buffer.write(b"\033[1;1H\033[1;36m")
                        sys.stdout.buffer.write(line_visible.encode("utf-8"))
                        sys.stdout.buffer.write(b"\033[0m")
                        cursor_col = (min(len(prompt) + len(page_input), margin_col - 1) + 1) if focus_page else (margin_col + len(margin_label) + len(margin_input) + 1)
                        sys.stdout.buffer.write(f"\033[1;{cursor_col}H".encode())
                    sys.stdout.buffer.flush()
                except (OSError, AttributeError):
                    pass

            try:
                key = stdscr.getch()
            except KeyboardInterrupt:
                break

            if mode == "browse":
                if key in (ord("q"), ord("Q"), 27):
                    try:
                        DEFAULT_DIR.resolve().relative_to(current_dir.resolve())
                        break
                    except ValueError:
                        current_dir = current_dir.parent
                        selected = 0
                elif key == curses.KEY_UP:
                    selected = max(0, selected - 1)
                elif key == curses.KEY_DOWN:
                    selected = min(len(entries) - 1, selected + 1)
                elif key in (curses.KEY_ENTER, 10, 13):
                    if not entries:
                        continue
                    _, full, is_dir = entries[selected]
                    if is_dir:
                        current_dir = full
                        selected = 0
                    else:
                        pdf_path = full
                        try:
                            if doc is not None:
                                doc.close()
                            doc = pymupdf.open(pdf_path)
                            mode = "page_input"
                            page_input = "1"
                            focus_page = True
                            selected = 0
                        except Exception as e:
                            message = f"열기 실패: {e}"
                            pdf_path = None

            else:
                if key in (ord("q"), ord("Q"), 27):
                    if doc is not None:
                        doc.close()
                        doc = None
                    pdf_path = None
                    page_input = ""
                    margin_input = "2.5"
                    mode = "browse"
                    message = ""
                    status_line = ""
                elif key == 9:  # Tab: 페이지 번호 ↔ 여백 포커스 전환
                    focus_page = not focus_page
                elif key in (curses.KEY_ENTER, 10, 13):
                    page_str = page_input.strip() or "0"
                    try:
                        page_no = int(page_str)
                    except ValueError:
                        message = "숫자를 입력하세요."
                        continue
                    if not (1 <= page_no <= num_pages):
                        message = f"1-{num_pages} 사이 번호를 입력하세요."
                        continue
                    try:
                        try:
                            margin_pt = float(margin_input.strip() or "0")
                        except ValueError:
                            margin_pt = 2.5
                        margin_pt = max(MARGIN_MIN, min(MARGIN_MAX, margin_pt))
                        pictures_dir = Path.home() / "Pictures/Extracted Images"
                        target_dir = pictures_dir / pdf_path.stem
                        target_dir.mkdir(parents=True, exist_ok=True)
                        paths = self.extractor.extract_page_images(
                            pdf_path, page_no - 1, out_dir=target_dir,
                            clip_inset_pt=margin_pt,
                        )
                        if paths:
                            folder = paths[0].parent
                            names = ", ".join(p.name for p in paths)
                            message = f"추출 완료 ({len(paths)}개) → {folder.name}/ : {names}"
                        else:
                            message = "해당 페이지에서 추출할 이미지가 없습니다."
                    except Exception as e:
                        message = f"추출 실패: {e}"
                elif focus_page:
                    if key == curses.KEY_BACKSPACE or key == 127:
                        if page_input:
                            page_input = page_input[:-1]
                    elif key == curses.KEY_UP:
                        page_str = page_input.strip() or "0"
                        try:
                            n = int(page_str)
                            n = max(1, n - 1)
                            page_input = str(n)
                        except ValueError:
                            page_input = "1"
                    elif key == curses.KEY_DOWN:
                        page_str = page_input.strip() or "0"
                        try:
                            n = int(page_str)
                            n = min(num_pages, n + 1)
                            page_input = str(n)
                        except ValueError:
                            page_input = "1"
                    elif key >= 32 and key < 256 and chr(key).isdigit():
                        page_input += chr(key)
                        max_digits = len(str(num_pages)) + 2
                        if len(page_input) > max_digits:
                            page_input = page_input[:max_digits]
                else:
                    # 포커스: 여백
                    if key == curses.KEY_BACKSPACE or key == 127:
                        if margin_input:
                            margin_input = margin_input[:-1]
                    elif key == curses.KEY_UP:
                        try:
                            v = float(margin_input.strip() or "0")
                        except ValueError:
                            v = 0.0
                        v = min(MARGIN_MAX, v + MARGIN_STEP)
                        margin_input = str(int(v) if v == int(v) else v)
                    elif key == curses.KEY_DOWN:
                        try:
                            v = float(margin_input.strip() or "0")
                        except ValueError:
                            v = 0.0
                        v = max(MARGIN_MIN, v - MARGIN_STEP)
                        margin_input = str(int(v) if v == int(v) else v)
                    elif key >= 32 and key < 256:
                        c = chr(key)
                        if c == "-" and margin_input == "":
                            margin_input = "-"
                        elif c == "." and "." not in margin_input:
                            margin_input += c
                        elif c.isdigit():
                            margin_input += c
                            if len(margin_input) > 8:
                                margin_input = margin_input[:8]

        if doc is not None:
            doc.close()
        if self.preview.supports_kitty_graphics():
            self.preview.clear_kitty_graphics()
        curses.curs_set(1)
