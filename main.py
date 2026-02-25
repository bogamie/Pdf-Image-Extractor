#!/usr/bin/env python3
"""
PDF 이미지 추출기 - 진입점
- 기본 디렉터리: Documents
- 방향키로 파일/폴더 선택, Enter로 PDF 선택 또는 폴더 진입
- 페이지 번호 입력 시 해당 페이지 미리보기
- Enter로 해당 페이지 이미지 추출 (이름_페이지.png 등)
"""

import sys

from app import DEFAULT_DIR, ImageExtractorTUI


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--help":
        print("사용법: python main.py")
        print("  기본 디렉터리: ~/Documents")
        print("  ↑/↓: 선택, Enter: 폴더 진입 또는 PDF 선택 / 페이지 추출, q: 종료 또는 뒤로")
        return 0

    try:
        import pymupdf  # noqa: F401
    except ImportError:
        print("오류: pymupdf가 필요합니다. pip install pymupdf", file=sys.stderr)
        return 1

    app = ImageExtractorTUI(initial_dir=DEFAULT_DIR)
    return app.run()


if __name__ == "__main__":
    sys.exit(main())
