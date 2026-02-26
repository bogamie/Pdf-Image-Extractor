"""PDF 페이지에서 이미지 추출 (임베디드 + 벡터 영역)."""
from __future__ import annotations

from pathlib import Path

try:
    import pymupdf
except ImportError:
    pymupdf = None  # type: ignore


class PDFImageExtractor:
    """PDF 한 페이지에서 이미지를 추출해 PNG로 저장합니다."""

    def __init__(
        self,
        dpi: int = 300,
        min_size_pt: float = 12.0,
        gap_pt: float = 25.0,
        clip_inset_pt: float = 2.5,
    ):
        self.dpi = dpi
        self.min_size_pt = min_size_pt
        self.gap_pt = gap_pt
        self.clip_inset_pt = clip_inset_pt

    @staticmethod
    def _merge_nearby_rects(rects: list, gap_pt: float = 25.0) -> list:
        """겹치거나 가까운 사각형들을 하나의 영역으로 합칩니다."""
        if not rects:
            return []

        def to_rect(r):
            if hasattr(r, "x0"):
                return (r.x0, r.y0, r.x1, r.y1)
            return (r[0], r[1], r[2], r[3])

        def rect_union(a, b):
            return (
                min(a[0], b[0]), min(a[1], b[1]),
                max(a[2], b[2]), max(a[3], b[3]),
            )

        def overlaps_or_near(r1, r2, gap):
            if r1[2] <= r2[0] - gap or r2[2] <= r1[0] - gap:
                return False
            if r1[3] <= r2[1] - gap or r2[3] <= r1[1] - gap:
                return False
            return True

        merged = [to_rect(r) for r in rects]
        changed = True
        while changed:
            changed = False
            new_merged = []
            used = [False] * len(merged)
            for i, a in enumerate(merged):
                if used[i]:
                    continue
                current = a
                for j in range(i + 1, len(merged)):
                    if used[j]:
                        continue
                    if overlaps_or_near(current, merged[j], gap_pt):
                        current = rect_union(current, merged[j])
                        used[j] = True
                        changed = True
                new_merged.append(current)
            merged = new_merged
        return merged

    @staticmethod
    def _filter_small_rects(
        rects: list,
        min_width_pt: float = 12.0,
        min_height_pt: float = 12.0,
    ) -> list:
        """너무 작은 영역을 제외합니다."""
        out = []
        for r in rects:
            if hasattr(r, "width"):
                w, h = r.width, r.height
            else:
                w = r[2] - r[0]
                h = r[3] - r[1]
            if w >= min_width_pt and h >= min_height_pt:
                out.append(r)
        return out

    @staticmethod
    def _filter_figure_like_rects(
        rects: list,
        page_rect: "pymupdf.Rect",
        min_area_pt2: float = 2500.0,
        min_height_pt: float = 28.0,
        max_aspect: float = 5.0,
        header_footer_margin_ratio: float = 0.08,
    ) -> list:
        """그림에 해당하지 않는 영역(헤더/푸터, 가는 선 등)을 제외합니다."""
        if not rects:
            return []
        page_h = page_rect.height
        top_cut = page_h * header_footer_margin_ratio
        bottom_cut = page_h * (1.0 - header_footer_margin_ratio)
        out = []
        for r in rects:
            if hasattr(r, "x0"):
                x0, y0, x1, y1 = r.x0, r.y0, r.x1, r.y1
            else:
                x0, y0, x1, y1 = r[0], r[1], r[2], r[3]
            w, h = x1 - x0, y1 - y0
            area = w * h
            if area < min_area_pt2 or h < min_height_pt or w < min_height_pt:
                continue
            aspect = max(w / h, h / w) if min(w, h) > 0.1 else 999
            if aspect > max_aspect:
                continue
            cy = (y0 + y1) / 2
            if h < page_h * 0.15 and (cy < top_cut or cy > bottom_cut):
                continue
            out.append(r)
        return out

    def extract_page_images(
        self,
        pdf_path: Path,
        page_no: int,
        out_dir: Path | None = None,
        clip_inset_pt: float | None = None,
    ) -> list[Path]:
        """
        지정한 PDF의 한 페이지만 추출합니다.
        임베디드 이미지 + 벡터 영역을 추출해
        {이름}_{페이지}.png 또는 {이름}_{페이지}_{번호}.png 로 저장합니다.
        clip_inset_pt: 여백(pt). 양수=줄이기, 음수=늘리기. None이면 self.clip_inset_pt 사용.
        """
        inset = self.clip_inset_pt if clip_inset_pt is None else clip_inset_pt
        if pymupdf is None:
            raise RuntimeError("pymupdf가 필요합니다. pip install pymupdf")

        out_dir = out_dir or (pdf_path.parent / f"{pdf_path.stem}_images")
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        base_name = pdf_path.stem
        page_num = page_no + 1

        doc = pymupdf.open(pdf_path)
        try:
            page = doc[page_no]
        except IndexError:
            doc.close()
            return []

        to_save: list[tuple] = []

        # 1) 임베디드 이미지
        seen_xrefs: set[int] = set()
        for img_entry in page.get_images():
            xref = img_entry[0]
            if xref in seen_xrefs:
                continue
            seen_xrefs.add(xref)
            try:
                info = doc.extract_image(xref)
            except Exception:
                continue
            if not info or not info.get("image"):
                continue
            smask_xref = info.get("smask") or (img_entry[1] if len(img_entry) > 1 else 0)
            try:
                if smask_xref and smask_xref > 0:
                    pix0 = pymupdf.Pixmap(doc, xref)
                    mask_info = doc.extract_image(smask_xref)
                    if mask_info and mask_info.get("image"):
                        mask_pix = pymupdf.Pixmap(mask_info["image"])
                        if not mask_pix.alpha:
                            mask_pix = pymupdf.Pixmap(mask_pix, 1)
                        pix = pymupdf.Pixmap(pix0, mask_pix)
                        to_save.append((pix, "png"))
                        continue
                pix = pymupdf.Pixmap(doc, xref)
                to_save.append((pix, "png"))
            except Exception:
                pass

        # 2) 벡터 영역
        drawings = page.get_drawings()
        if drawings:
            raw_rects = []
            for d in drawings:
                # [추가] 도형에 색칠(fill)이 되어있거나, 테두리 선(color+width)이 있는 경우만 취급
                has_fill = d.get("fill") is not None
                has_stroke = d.get("color") is not None and d.get("width", 0) > 0
                
                # 둘 다 없으면 화면에 보이지 않는 투명 박스(하이퍼링크 등)이므로 무시
                if not has_fill and not has_stroke:
                    continue
                    
                raw_rects.append(d["rect"])

            page_h = page.rect.height
            rects = []
            for r in raw_rects:
                # [유지] 상/하단 15% 영역의 노이즈 제거 필터
                is_header_noise = (r.y1 < page_h * 0.15) and (r.height < 30)
                is_footer_noise = (r.y0 > page_h * 0.85) and (r.height < 30)
                
                if is_header_noise or is_footer_noise:
                    continue
                rects.append(r)

            merged = self._merge_nearby_rects(rects, gap_pt=self.gap_pt)
            merged = self._filter_small_rects(
                merged,
                min_width_pt=self.min_size_pt,
                min_height_pt=self.min_size_pt,
            )
            merged = self._filter_figure_like_rects(merged, page.rect)
            for r in merged:
                clip = pymupdf.Rect(r[0], r[1], r[2], r[3]) & page.rect
                if inset != 0:
                    if inset > 0 and clip.width > inset * 2 and clip.height > inset * 2:
                        clip = pymupdf.Rect(
                            clip.x0 + inset,
                            clip.y0 + inset,
                            clip.x1 - inset,
                            clip.y1 - inset,
                        )
                    elif inset < 0:
                        clip = pymupdf.Rect(
                            clip.x0 + inset,
                            clip.y0 + inset,
                            clip.x1 - inset,
                            clip.y1 - inset,
                        )
                    clip = clip & page.rect
                if clip.is_empty or clip.width < 2 or clip.height < 2:
                    continue
                try:
                    pix = page.get_pixmap(
                        clip=clip, dpi=self.dpi, alpha=False
                    )
                    to_save.append((pix, "png"))
                except Exception:
                    continue

        doc.close()

        result: list[Path] = []
        for i, (pix, ext) in enumerate(to_save):
            name = (
                f"{base_name}_{page_num}.png"
                if len(to_save) == 1
                else f"{base_name}_{page_num}_{i + 1}.png"
            )
            path = out_dir / name
            _save_pixmap_as_png(pix, path)
            result.append(path)
        return result


def _save_pixmap_as_png(pix: "pymupdf.Pixmap", path: Path) -> None:
    """PNG는 grayscale 또는 RGB만 지원하므로, CMYK/alpha 등은 RGB로 변환 후 저장."""
    try:
        pix.save(path)
    except Exception as e:
        if "grayscale or rgb" not in str(e).lower():
            raise
        # RGB로 변환 (CMYK 등 처리)
        pix_rgb = pymupdf.Pixmap(pymupdf.csRGB, pix)
        if pix_rgb.alpha:
            pix_rgb = pymupdf.Pixmap(pix_rgb, 0)  # alpha 제거
        pix_rgb.save(path)
