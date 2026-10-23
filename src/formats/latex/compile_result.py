from typing import List, Optional
import re


_DOCUMENTCLASS_RE = re.compile(
    r"\\document(?:class|style)(?:\[[^\]]*\])?\{[^}]*\}",
    re.DOTALL,
)

_CTEX_PACKAGE = "\\usepackage[UTF8,scheme=plain,fontset=fandol]{ctex}"
_OLD_CTEX_PACKAGE = "\\usepackage[UTF8]{ctex}"
_CTEX_USEPACKAGE_RE = re.compile(r"\\usepackage(?:\[[^\]]*\])?\{ctex\}")
_CTEX_MARKERS = (
    _CTEX_PACKAGE,
    _OLD_CTEX_PACKAGE,
    "\\usepackage{ctex}",
    "\\usepackage{xeCJK}",
)
_TIMES_PACKAGES = ("times", "mathptmx", "txfonts", "newtxtext", "pslatex")
_TIMES_LATIN_FONTS = """\
\\setmainfont{TeX Gyre Termes}
\\setsansfont{TeX Gyre Heros}
\\setmonofont{TeX Gyre Cursor}
"""

_LUATEXJA_MARKERS = (
    "\\usepackage{luatexja}",
    "\\usepackage{luatex-ja}",
)

_PDFTEX_PACKAGES = (
    "inputenc",
    "fontenc",
    "times",
    "mathptmx",
    "cmap",
    "ae",
    "aeguill",
    "ucs",
    "babel",
    "polyglossia",
)

_LLNCS_COMPAT = """\
\\makeatletter
\\def\\frontmatter{}
\\def\\mainmatter{}
\\def\\backmatter{}
\\def\\titlerunning#1{}
\\def\\authorrunning#1{}
\\def\\tocauthor#1{}
\\def\\toctitle#1{}
\\def\\institute#1{\\date{#1}}
\\def\\email#1{\\texttt{#1}}
\\def\\keywords#1{\\par\\noindent\\textbf{Keywords:} #1}
\\makeatother
"""

_PDFTEX_ASSIGN_PRIMITIVES = (
    "pdfoutput",
    "pdfobjcompresslevel",
    "pdfminorversion",
    "pdfcompresslevel",
    "pdfdecimaldigits",
    "pdfinclusioncopyfonts",
    "pdfsuppresswarningpagegroup",
    "pdfgentounicode",
    "pdfadjustspacing",
    "pdfprotrudechars",
    "pdfpkresolution",
    "pdfpagewidth",
    "pdfpageheight",
    "pdfhorigin",
    "pdfvorigin",
    "pdfpxdimen",
    "pdfsuppressptexinfo",
    "pdfinfoomitdate",
)

# pdfTeX primitives that take a braced argument instead of ``= value``.
_PDFTEX_ARG_PRIMITIVES = (
    "pdftrailerid",
)

_UNDEFINED_REF_RE = re.compile(
    r"(?:citation|(?:hyper\s+)?reference)\s+`[^']*'[^\n]*undefined",
    re.IGNORECASE,
)
_UNDEFINED_REFS_SUMMARY_RE = re.compile(
    r"there were undefined (?:references|citations)",
    re.IGNORECASE,
)
_RERUN_RE = re.compile(r"rerun to get|run latex again", re.IGNORECASE)

_PDFTEX_DRIVER_PACKAGES = (
    "graphicx",
    "hyperref",
    "color",
    "xcolor",
    "geometry",
)


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
    if _CTEX_USEPACKAGE_RE.search(tex_source):
        return True
    markers = _CTEX_MARKERS + _LUATEXJA_MARKERS
    return any(marker in tex_source for marker in markers)


def tex_uses_times_font(tex_source: str) -> bool:
    names = "|".join(_TIMES_PACKAGES)
    return bool(
        re.search(
            rf"\\(?:usepackage|RequirePackage)(?:\[[^\]]*\])?\{{[^}}]*\b(?:{names})\b",
            tex_source,
        )
    )


def compile_engine_order(tex_source: str) -> tuple:
    if any(marker in tex_source for marker in _LUATEXJA_MARKERS):
        return ("lualatex", "xelatex")
    if tex_requires_cjk_engine(tex_source):
        return ("xelatex", "lualatex")
    return ("pdflatex", "xelatex")


def _normalize_lang(target_language: str) -> str:
    lang = (target_language or "ch").strip().lower()
    if lang in ("ja", "jp", "japanese"):
        return "ja"
    return "ch"


def inject_cjk_package(tex_source: str, target_language: str = "ch") -> str:
    lang = _normalize_lang(target_language)
    if lang == "ja":
        package = "\\usepackage{luatexja}"
        if any(marker in tex_source for marker in _LUATEXJA_MARKERS):
            return tex_source
    else:
        package = _CTEX_PACKAGE
        if _CTEX_USEPACKAGE_RE.search(tex_source) or any(
            marker in tex_source for marker in _CTEX_MARKERS
        ):
            return tex_source
    match = _DOCUMENTCLASS_RE.search(tex_source)
    if not match:
        return tex_source
    pos = match.end()
    return tex_source[:pos] + "\n" + package + "\n" + tex_source[pos:]


def comment_pdftex_packages(tex_source: str) -> str:
    names = "|".join(_PDFTEX_PACKAGES)
    pattern = re.compile(
        rf"(?m)^(?P<indent>[ \t]*)(?P<prefix>(?:<PLACEHOLDER_[^>]+_begin>)?)(?P<cmd>\\(?:usepackage|RequirePackage)(?:\[[^\]]*\])?\{{(?:{names})\}})"
    )
    return pattern.sub(r"\g<indent>\g<prefix>% \g<cmd>", tex_source)


def strip_times_from_usepackage_lists(tex_source: str) -> str:
    pattern = re.compile(
        r"(?P<cmd>\\(?:usepackage|RequirePackage))(?P<opt>\[[^\]]*\])?\{(?P<pkgs>[^}]+)\}"
    )

    def replacer(match: re.Match) -> str:
        pkgs = [part.strip() for part in match.group("pkgs").split(",") if part.strip()]
        if "times" not in pkgs:
            return match.group(0)
        kept = [pkg for pkg in pkgs if pkg != "times"]
        if not kept:
            return match.group(0)
        opt = match.group("opt") or ""
        return f"{match.group('cmd')}{opt}{{{','.join(kept)}}}"

    return pattern.sub(replacer, tex_source)


def inject_latin_fonts_matching_original(tex_source: str, uses_times: bool) -> str:
    if not uses_times or "\\setmainfont{" in tex_source:
        return tex_source
    for marker in (_CTEX_PACKAGE, "\\usepackage{luatexja}", _OLD_CTEX_PACKAGE):
        at = tex_source.find(marker)
        if at >= 0:
            pos = at + len(marker)
            return tex_source[:pos] + "\n" + _TIMES_LATIN_FONTS + tex_source[pos:]
    return tex_source


def strip_pdftex_driver_options(tex_source: str) -> str:
    names = "|".join(_PDFTEX_DRIVER_PACKAGES)
    pattern = re.compile(
        rf"\\usepackage\[(?P<opts>[^\]]*)\]\{{(?P<pkg>{names})\}}"
    )

    def replacer(match: re.Match) -> str:
        pkg = match.group("pkg")
        kept = [
            part.strip()
            for part in match.group("opts").split(",")
            if part.strip() and part.strip().lower() != "pdftex"
        ]
        if not kept:
            return f"\\usepackage{{{pkg}}}"
        return f"\\usepackage[{','.join(kept)}]{{{pkg}}}"

    return pattern.sub(replacer, tex_source)


def sanitize_pdftex_primitives(tex_source: str) -> str:
    """Guard pdfTeX-only primitives that would leak their arguments as text.

    Engines such as XeLaTeX do not know these primitives.  Left untouched,
    ``\\pdfsuppressptexinfo=-1`` raises ``Undefined control sequence`` followed
    by ``Missing \\begin{document}``, and the leftover ``=-1`` is typeset as the
    first paragraph of the document (it shows up above the title).
    """
    for name in _PDFTEX_ASSIGN_PRIMITIVES:
        tex_source = re.sub(
            rf"(?m)^(?P<indent>[ \t]*)\\{name}(?P<assign>\s*=\s*[^\s%]+)",
            rf"\g<indent>\\ifdefined\\{name}\\{name}\g<assign>\\fi",
            tex_source,
        )
    for name in _PDFTEX_ARG_PRIMITIVES:
        tex_source = re.sub(
            rf"(?m)^(?P<indent>[ \t]*)\\{name}(?P<arg>\{{[^}}]*\}})",
            rf"\g<indent>\\ifdefined\\{name}\\{name}\g<arg>\\fi",
            tex_source,
        )
    tex_source = re.sub(
        r"(?m)^(?P<indent>[ \t]*)\\pdfmapfile(?P<arg>\{[^}]*\})",
        r"\g<indent>\\ifdefined\\pdfmapfile\\pdfmapfile\g<arg>\\fi",
        tex_source,
    )
    return tex_source


def log_needs_extra_pass(log_text: str) -> bool:
    """Whether a LaTeX log still asks for another pass to settle refs/citations."""
    if not log_text:
        return False
    if _RERUN_RE.search(log_text):
        return True
    if _UNDEFINED_REFS_SUMMARY_RE.search(log_text):
        return True
    return bool(_UNDEFINED_REF_RE.search(log_text))


def rewrite_unsupported_classes(tex_source: str) -> str:
    match = re.search(r"\\documentclass(?:\[[^\]]*\])?\{llncs\}", tex_source)
    if not match:
        return tex_source
    tex_source = tex_source[:match.start()] + "\\documentclass{article}" + tex_source[match.end():]
    ctex_at = tex_source.find(_CTEX_PACKAGE)
    if ctex_at >= 0:
        pos = ctex_at + len(_CTEX_PACKAGE)
    elif tex_source.find(_OLD_CTEX_PACKAGE) >= 0:
        ctex_at = tex_source.find(_OLD_CTEX_PACKAGE)
        pos = ctex_at + len(_OLD_CTEX_PACKAGE)
    else:
        class_match = _DOCUMENTCLASS_RE.search(tex_source)
        if not class_match:
            return tex_source
        pos = class_match.end()
    return tex_source[:pos] + "\n" + _LLNCS_COMPAT + tex_source[pos:]


def rewrite_pdftex_px_units(tex_source: str) -> str:
    return re.sub(r"(?<=\d)px\b", "bp", tex_source)


def adapt_preamble_for_engine(tex_source: str, target_language: str = "ch") -> str:
    uses_times = tex_uses_times_font(tex_source)
    tex_source = tex_source.replace(_OLD_CTEX_PACKAGE, _CTEX_PACKAGE)
    tex_source = inject_cjk_package(tex_source, target_language)
    tex_source = rewrite_unsupported_classes(tex_source)
    tex_source = strip_times_from_usepackage_lists(tex_source)
    tex_source = comment_pdftex_packages(tex_source)
    tex_source = inject_latin_fonts_matching_original(tex_source, uses_times)
    tex_source = strip_pdftex_driver_options(tex_source)
    tex_source = sanitize_pdftex_primitives(tex_source)
    tex_source = rewrite_pdftex_px_units(tex_source)
    return tex_source


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
