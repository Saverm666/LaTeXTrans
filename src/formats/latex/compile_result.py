from typing import List, Optional
import re


def should_run_final_compile(errors_report) -> bool:
    return not errors_report


def pick_compiled_pdf(compile_ok: bool, pdf_files: List[str]) -> Optional[str]:
    if compile_ok and pdf_files:
        return pdf_files[0]
    return None


def latexmk_args(engine: str, out_dir: str, tex_file: str) -> List[str]:
    return [
        "latexmk",
        f"-{engine}",
        "-interaction=nonstopmode",
        f"-outdir={out_dir}",
        "-file-line-error",
        "-synctex=1",
        tex_file,
    ]


def tex_requires_cjk_engine(tex_source: str) -> bool:
    markers = (
        "\\usepackage[UTF8]{ctex}",
        "\\usepackage{ctex}",
        "\\usepackage{xeCJK}",
        "\\usepackage{luatexja}",
    )
    return any(marker in tex_source for marker in markers)


def compile_engine_order(tex_source: str) -> tuple:
    if tex_requires_cjk_engine(tex_source):
        return ("xelatex", "lualatex")
    return ("pdflatex", "xelatex")


def sanitize_pdftex_primitives(tex_source: str) -> str:
    return re.sub(
        r"\\pdfobjcompresslevel\s*=\s*\d+",
        r"\\ifdefined\\pdfobjcompresslevel\\pdfobjcompresslevel=0\\fi",
        tex_source,
        count=1,
    )


def pdf_needs_xdvipdfmx_rewrite(header: bytes) -> bool:
    if not header.startswith(b"%PDF-"):
        return False
    line = header.split(b"\n", 1)[0]
    try:
        version = line.decode("ascii", errors="ignore").replace("%PDF-", "")
        major, minor = version.split(".")
        return int(major) > 1 or int(minor) > 5
    except (ValueError, IndexError):
        return False
