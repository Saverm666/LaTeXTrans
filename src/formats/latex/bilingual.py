from typing import Callable, List, Optional, Tuple
import os
import shutil

from pypdf import PdfReader, PdfWriter, Transformation
from pypdf.generic import RectangleObject


def pair_pdf_pages(orig_count: int, trans_count: int) -> List[Tuple[Optional[int], Optional[int]]]:
    total = max(orig_count, trans_count)
    pairs = []
    for index in range(1, total + 1):
        left = index if index <= orig_count else None
        right = index if index <= trans_count else None
        pairs.append((left, right))
    return pairs


def interleaved_page_order(
    orig_count: int,
    trans_count: int,
) -> List[Tuple[str, int]]:
    order: List[Tuple[str, int]] = []
    for orig_page, trans_page in pair_pdf_pages(orig_count, trans_count):
        if orig_page is not None:
            order.append(("orig", orig_page))
        if trans_page is not None:
            order.append(("trans", trans_page))
    return order


PDF_VARIANTS = (
    ("mono", "单语版"),
    ("bilingual", "双语对照版"),
    ("sidebyside", "边栏版"),
)


def variant_filename(target_language: str, project_name: str, variant: str) -> str:
    return f"{target_language}_{project_name}_{variant}.pdf"


def page_size(page) -> Tuple[float, float]:
    box = page.mediabox
    return float(box.width), float(box.height)


def spread_dimensions(
    left_wh: Optional[Tuple[float, float]],
    right_wh: Optional[Tuple[float, float]],
) -> Tuple[float, float, float, float, float]:
    if left_wh is None and right_wh is None:
        raise ValueError("Need at least one page to build a bilingual spread.")
    if left_wh is None:
        left_wh = right_wh
    if right_wh is None:
        right_wh = left_wh
    height = max(left_wh[1], right_wh[1])
    left_scale = height / left_wh[1]
    right_scale = height / right_wh[1]
    left_width = left_wh[0] * left_scale
    width = left_width + right_wh[0] * right_scale
    return width, height, left_scale, right_scale, left_width


def pdf_page_count(path: str) -> int:
    return len(PdfReader(path).pages)


def find_original_pdf(project_dir: str, translated_pdf: Optional[str] = None) -> Optional[str]:
    base_name = os.path.basename(os.path.abspath(project_dir))
    preferred = os.path.join(project_dir, f"{base_name}.pdf")
    skip = set()
    if translated_pdf:
        skip.add(os.path.abspath(translated_pdf))
    if os.path.isfile(preferred) and os.path.abspath(preferred) not in skip:
        return preferred
    candidates = []
    for name in os.listdir(project_dir):
        if not name.lower().endswith(".pdf") or name.startswith("._"):
            continue
        path = os.path.join(project_dir, name)
        if os.path.abspath(path) in skip:
            continue
        if name.endswith("_bilingual.pdf") or name.endswith("_sidebyside.pdf") or name.endswith("_mono.pdf"):
            continue
        candidates.append(path)
    return candidates[0] if candidates else None


def preferred_original_pdf_path(project_dir: str) -> str:
    base_name = os.path.basename(os.path.abspath(project_dir))
    return os.path.join(project_dir, f"{base_name}.pdf")


def _place_original_pdf(source: str, dest: str) -> str:
    if os.path.abspath(source) != os.path.abspath(dest):
        shutil.copy2(source, dest)
    return dest


def ensure_original_pdf(
    project_dir: str,
    translated_pdf: Optional[str] = None,
    download_original: Optional[Callable[[], Optional[str]]] = None,
    compile_original: Optional[Callable[[], Optional[str]]] = None,
) -> Optional[str]:
    dest = preferred_original_pdf_path(project_dir)
    skip = set()
    if translated_pdf:
        skip.add(os.path.abspath(translated_pdf))
    if os.path.isfile(dest) and os.path.abspath(dest) not in skip:
        return dest
    if download_original is not None:
        downloaded = download_original()
        if downloaded and os.path.isfile(downloaded):
            return _place_original_pdf(downloaded, dest)
    found = find_original_pdf(project_dir, translated_pdf)
    if found:
        return found
    if compile_original is None:
        return None
    compiled = compile_original()
    if not compiled or not os.path.isfile(compiled):
        return None
    return _place_original_pdf(compiled, dest)


def _merge_page(dest, source, scale: float, tx: float = 0, ty: float = 0) -> None:
    dest.merge_transformed_page(
        source,
        Transformation().scale(scale, scale).translate(tx, ty),
        expand=False,
        over=True,
    )


def compile_side_by_side_pdf(orig_pdf: str, trans_pdf: str, output_pdf: str) -> Optional[str]:
    orig_reader = PdfReader(orig_pdf)
    trans_reader = PdfReader(trans_pdf)
    pairs = pair_pdf_pages(len(orig_reader.pages), len(trans_reader.pages))
    if not pairs:
        return None

    writer = PdfWriter()
    for left, right in pairs:
        left_page = orig_reader.pages[left - 1] if left is not None else None
        right_page = trans_reader.pages[right - 1] if right is not None else None
        left_wh = page_size(left_page) if left_page is not None else None
        right_wh = page_size(right_page) if right_page is not None else None
        width, height, left_scale, right_scale, left_width = spread_dimensions(left_wh, right_wh)
        spread = writer.add_blank_page(width=width, height=height)
        spread.mediabox = RectangleObject((0, 0, width, height))
        if left_page is not None:
            _merge_page(spread, left_page, left_scale, 0, 0)
        if right_page is not None:
            _merge_page(spread, right_page, right_scale, left_width, 0)

    os.makedirs(os.path.dirname(os.path.abspath(output_pdf)), exist_ok=True)
    with open(output_pdf, "wb") as handle:
        writer.write(handle)
    print(f"✅  Side-by-side PDF written to {output_pdf}")
    return output_pdf


def compile_bilingual_pdf(orig_pdf: str, trans_pdf: str, output_pdf: str) -> Optional[str]:
    orig_reader = PdfReader(orig_pdf)
    trans_reader = PdfReader(trans_pdf)
    order = interleaved_page_order(len(orig_reader.pages), len(trans_reader.pages))
    if not order:
        return None

    writer = PdfWriter()
    for source, page_no in order:
        reader = orig_reader if source == "orig" else trans_reader
        writer.add_page(reader.pages[page_no - 1])

    os.makedirs(os.path.dirname(os.path.abspath(output_pdf)), exist_ok=True)
    with open(output_pdf, "wb") as handle:
        writer.write(handle)
    print(f"✅  Bilingual PDF written to {output_pdf}")
    return output_pdf
