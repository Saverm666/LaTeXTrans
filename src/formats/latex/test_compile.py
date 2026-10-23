import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from src.formats.latex.compile import LaTexCompiler
from src.formats.latex.compile_result import (
    latexmk_args,
    pick_compiled_pdf,
    should_run_final_compile,
)


class ShouldRunFinalCompileTest(unittest.TestCase):
    def test_blocks_compile_when_tex_errors_remain_after_retries(self):
        remaining_errors = [{"part": "sec", "num_or_ph": "1", "bracket_error": "Unmatched"}]
        self.assertFalse(should_run_final_compile(remaining_errors))

    def test_allows_compile_when_no_tex_errors_remain(self):
        self.assertTrue(should_run_final_compile([]))
        self.assertTrue(should_run_final_compile(None))


class PickCompiledPdfTest(unittest.TestCase):
    def test_ignores_pdf_when_latexmk_failed(self):
        pdf_files = ["/tmp/build/paper.pdf"]
        self.assertIsNone(pick_compiled_pdf(False, pdf_files))

    def test_returns_pdf_only_when_latexmk_succeeded(self):
        pdf_files = ["/tmp/build/paper.pdf"]
        self.assertEqual(pick_compiled_pdf(True, pdf_files), "/tmp/build/paper.pdf")

    def test_returns_none_when_latexmk_succeeded_but_no_pdf(self):
        self.assertIsNone(pick_compiled_pdf(True, []))


class LatexmkArgsTest(unittest.TestCase):
    def test_does_not_force_compile_past_tex_errors(self):
        args = latexmk_args("pdflatex", "/tmp/out", "/tmp/paper.tex")
        self.assertNotIn("-f", args)
        self.assertIn("-interaction=nonstopmode", args)


class CjkEngineOrderTest(unittest.TestCase):
    def test_ctex_skips_pdflatex_and_tries_xelatex_then_lualatex(self):
        from src.formats.latex.compile_result import compile_engine_order, tex_requires_cjk_engine

        tex = "\\documentclass{article}\n\\usepackage[UTF8,scheme=plain,fontset=fandol]{ctex}\n"
        self.assertTrue(tex_requires_cjk_engine(tex))
        self.assertEqual(compile_engine_order(tex), ("xelatex", "lualatex"))

    def test_latin_still_tries_pdflatex_first(self):
        from src.formats.latex.compile_result import compile_engine_order

        tex = "\\documentclass{article}\n\\begin{document}Hi\\end{document}\n"
        self.assertEqual(compile_engine_order(tex), ("pdflatex", "xelatex"))

    def test_guards_pdftex_only_primitive_for_xelatex(self):
        from src.formats.latex.compile_result import sanitize_pdftex_primitives

        tex = "\\pdfobjcompresslevel=0\n\\documentclass{article}\n"
        out = sanitize_pdftex_primitives(tex)
        self.assertIn("\\ifdefined\\pdfobjcompresslevel", out)
        self.assertNotIn("\\pdfobjcompresslevel=0\n\\documentclass", out)

    def test_guards_pdfoutput_primitive_for_xelatex(self):
        from src.formats.latex.compile_result import sanitize_pdftex_primitives

        tex = "\\pdfoutput=1\n\\documentclass{article}\n"
        out = sanitize_pdftex_primitives(tex)
        self.assertIn("\\ifdefined\\pdfoutput", out)
        self.assertNotIn("\\pdfoutput=1\n\\documentclass", out)

    def test_guards_pdfsuppressptexinfo_primitive_for_xelatex(self):
        from src.formats.latex.compile_result import sanitize_pdftex_primitives

        tex = "\\pdfsuppressptexinfo=-1\n\\pdfinfoomitdate=1\n"
        out = sanitize_pdftex_primitives(tex)
        self.assertIn("\\ifdefined\\pdfsuppressptexinfo\\pdfsuppressptexinfo=-1\\fi", out)
        self.assertIn("\\ifdefined\\pdfinfoomitdate\\pdfinfoomitdate=1\\fi", out)
        self.assertNotRegex(out, r"(?m)^=-1")

    def test_guards_pdftrailerid_braced_primitive_for_xelatex(self):
        from src.formats.latex.compile_result import sanitize_pdftex_primitives

        out = sanitize_pdftex_primitives("\\pdftrailerid{}\n")
        self.assertEqual(out, "\\ifdefined\\pdftrailerid\\pdftrailerid{}\\fi\n")

    def test_guards_yue2_preamble_block_and_is_idempotent(self):
        from src.formats.latex.compile_result import adapt_preamble_for_engine

        tex = (
            "\\documentclass{article}\n"
            "\\pdfsuppressptexinfo=-1\n"
            "\\pdfinfoomitdate=1\n"
            "\\pdftrailerid{}\n"
            "\\pdfsuppressptexinfo=-1\n"
            "\\begin{document}\nhi\n\\end{document}\n"
        )
        out = adapt_preamble_for_engine(tex, "ch")
        # A bare assignment surviving here is typeset as body text right above
        # the title under XeLaTeX -- the "=-1 =1 =-1" artifact.
        self.assertNotRegex(out, r"(?m)^\s*(?:=-1|=1)\s*$")
        self.assertNotRegex(out, r"(?m)^\s*\\(?:pdfsuppressptexinfo|pdfinfoomitdate|pdftrailerid)")
        self.assertEqual(out.count("\\ifdefined\\pdfsuppressptexinfo"), 2)
        self.assertIn("\\ifdefined\\pdfinfoomitdate\\pdfinfoomitdate=1\\fi", out)
        self.assertIn("\\ifdefined\\pdftrailerid\\pdftrailerid{}\\fi", out)
        self.assertEqual(adapt_preamble_for_engine(out, "ch"), out)

    def test_detects_undefined_citations_in_log(self):
        from src.formats.latex.compile_result import log_needs_extra_pass

        self.assertTrue(
            log_needs_extra_pass(
                "Package natbib Warning: Citation `deng2025bagel' on page 1 undefined on input line 286.\n"
            )
        )
        self.assertTrue(
            log_needs_extra_pass(
                "LaTeX Warning: Hyper reference `sec:authors' on page 1 undefined on input line 286.\n"
            )
        )
        self.assertTrue(log_needs_extra_pass("LaTeX Warning: There were undefined references.\n"))
        self.assertTrue(log_needs_extra_pass("LaTeX Warning: Rerun to get cross-references right.\n"))

    def test_clean_log_needs_no_extra_pass(self):
        from src.formats.latex.compile_result import log_needs_extra_pass

        self.assertFalse(log_needs_extra_pass(""))
        self.assertFalse(log_needs_extra_pass("Output written on paper.pdf (51 pages).\n"))

    def test_detects_pdf_versions_that_break_xdvipdfmx(self):
        from src.formats.latex.compile_result import pdf_needs_xdvipdfmx_rewrite

        self.assertFalse(pdf_needs_xdvipdfmx_rewrite(b"%PDF-1.5\n"))
        self.assertTrue(pdf_needs_xdvipdfmx_rewrite(b"%PDF-1.7\n"))

    def test_luatexja_prefers_lualatex_then_xelatex(self):
        from src.formats.latex.compile_result import compile_engine_order

        tex = "\\documentclass{article}\n\\usepackage{luatexja}\n"
        self.assertEqual(compile_engine_order(tex), ("lualatex", "xelatex"))


ENGLISH_PREAMBLE = """\
\\pdfoutput=1
\\pdfobjcompresslevel=0
\\pdfminorversion=5
\\pdfsuppresswarningpagegroup=1
\\pdfpagewidth=\\paperwidth
\\documentclass{article}
\\usepackage[utf8]{inputenc}
\\usepackage[T1]{fontenc}
\\usepackage{times}
\\usepackage{mathptmx}
\\usepackage{cmap}
\\usepackage[pdftex]{graphicx}
\\usepackage[pdftex,unicode]{hyperref}
\\begin{document}
hi
\\end{document}
"""


class AdaptPreambleForEngineTest(unittest.TestCase):
    def test_injects_ctex_for_chinese(self):
        from src.formats.latex.compile_result import adapt_preamble_for_engine

        out = adapt_preamble_for_engine(ENGLISH_PREAMBLE, "ch")
        self.assertIn("\\usepackage[UTF8,scheme=plain,fontset=fandol]{ctex}", out)
        self.assertNotIn("\\usepackage{luatexja}", out)
        class_at = out.find("\\documentclass{article}")
        ctex_at = out.find("\\usepackage[UTF8,scheme=plain,fontset=fandol]{ctex}")
        self.assertGreater(ctex_at, class_at)

    def test_injects_luatexja_for_japanese(self):
        from src.formats.latex.compile_result import adapt_preamble_for_engine

        out = adapt_preamble_for_engine(ENGLISH_PREAMBLE, "ja")
        self.assertIn("\\usepackage{luatexja}", out)
        self.assertNotIn("\\usepackage[UTF8,scheme=plain,fontset=fandol]{ctex}", out)

    def test_comments_pdftex_only_packages(self):
        from src.formats.latex.compile_result import adapt_preamble_for_engine

        out = adapt_preamble_for_engine(ENGLISH_PREAMBLE, "ch")
        self.assertIn("% \\usepackage[utf8]{inputenc}", out)
        self.assertIn("% \\usepackage[T1]{fontenc}", out)
        self.assertIn("% \\usepackage{times}", out)
        self.assertIn("% \\usepackage{mathptmx}", out)
        self.assertIn("% \\usepackage{cmap}", out)
        self.assertNotRegex(out, r"(?m)^\\usepackage\[utf8\]\{inputenc\}")
        self.assertNotRegex(out, r"(?m)^\\usepackage\{times\}")

    def test_comments_pdftex_packages_after_input_placeholder(self):
        from src.formats.latex.compile_result import adapt_preamble_for_engine

        tex = (
            "\\documentclass{article}\n"
            "<PLACEHOLDER_01_decl_begin>\\usepackage[utf8]{inputenc}\n"
            "\\usepackage[T1]{fontenc}\n"
            "<PLACEHOLDER_01_decl_end>\n"
        )
        out = adapt_preamble_for_engine(tex, "ch")
        self.assertIn("<PLACEHOLDER_01_decl_begin>% \\usepackage[utf8]{inputenc}", out)
        self.assertIn("% \\usepackage[T1]{fontenc}", out)

    def test_guards_pdftex_primitives_on_their_own_line(self):
        from src.formats.latex.compile_result import adapt_preamble_for_engine

        out = adapt_preamble_for_engine(ENGLISH_PREAMBLE, "ch")
        self.assertIn("\\ifdefined\\pdfoutput\\pdfoutput=1\\fi", out)
        self.assertIn("\\ifdefined\\pdfobjcompresslevel\\pdfobjcompresslevel=0\\fi", out)
        self.assertIn("\\ifdefined\\pdfminorversion\\pdfminorversion=5\\fi", out)
        self.assertIn(
            "\\ifdefined\\pdfsuppresswarningpagegroup\\pdfsuppresswarningpagegroup=1\\fi",
            out,
        )
        self.assertIn("\\ifdefined\\pdfpagewidth\\pdfpagewidth=\\paperwidth\\fi", out)
        self.assertNotRegex(out, r"(?m)^\\pdfoutput=1")

    def test_strips_pdftex_driver_options(self):
        from src.formats.latex.compile_result import adapt_preamble_for_engine

        out = adapt_preamble_for_engine(ENGLISH_PREAMBLE, "ch")
        self.assertIn("\\usepackage{graphicx}", out)
        self.assertIn("\\usepackage[unicode]{hyperref}", out)
        self.assertNotIn("[pdftex]", out)

    def test_is_idempotent(self):
        from src.formats.latex.compile_result import adapt_preamble_for_engine

        once = adapt_preamble_for_engine(ENGLISH_PREAMBLE, "ch")
        twice = adapt_preamble_for_engine(once, "ch")
        self.assertEqual(once, twice)
        self.assertEqual(once.count("\\usepackage[UTF8,scheme=plain,fontset=fandol]{ctex}"), 1)

    def test_rewrites_pdftex_px_unit_to_bp(self):
        from src.formats.latex.compile_result import adapt_preamble_for_engine

        tex = (
            "\\documentclass{article}\n"
            "\\includegraphics[width=360px]{Input_Emebeddings.pdf}\n"
        )
        out = adapt_preamble_for_engine(tex, "ch")
        self.assertIn("\\includegraphics[width=360bp]{Input_Emebeddings.pdf}", out)
        self.assertNotIn("360px", out)
        self.assertEqual(adapt_preamble_for_engine(out, "ch"), out)

    def test_comments_babel_which_conflicts_with_ctex(self):
        from src.formats.latex.compile_result import adapt_preamble_for_engine

        tex = (
            "\\documentclass{article}\n"
            "\\usepackage[english]{babel}\n"
            "\\usepackage{polyglossia}\n"
        )
        out = adapt_preamble_for_engine(tex, "ch")
        self.assertIn("% \\usepackage[english]{babel}", out)
        self.assertIn("% \\usepackage{polyglossia}", out)
        self.assertNotRegex(out, r"(?m)^\\usepackage\[english\]\{babel\}")

    def test_rewrites_llncs_class_to_article_compat(self):
        from src.formats.latex.compile_result import adapt_preamble_for_engine

        tex = (
            "\\documentclass[runningheads]{llncs}\n"
            "\\begin{document}\n"
            "\\institute{Uni}\\email{a@b.com}\n"
            "\\end{document}\n"
        )
        out = adapt_preamble_for_engine(tex, "ch")
        self.assertIn("\\documentclass{article}", out)
        self.assertNotIn("{llncs}", out)
        self.assertIn("\\def\\frontmatter{}", out)
        self.assertIn("\\def\\institute#1{\\date{#1}}", out)
        self.assertIn("\\usepackage[UTF8,scheme=plain,fontset=fandol]{ctex}", out)
        self.assertLess(
            out.find("\\usepackage[UTF8,scheme=plain,fontset=fandol]{ctex}"),
            out.find("\\def\\frontmatter{}"),
        )
        self.assertEqual(adapt_preamble_for_engine(out, "ch"), out)

    def test_restores_times_compatible_latin_font(self):
        from src.formats.latex.compile_result import adapt_preamble_for_engine

        out = adapt_preamble_for_engine(ENGLISH_PREAMBLE, "ch")
        self.assertIn("\\setmainfont{TeX Gyre Termes}", out)
        ctex_at = out.find("\\usepackage[UTF8,scheme=plain,fontset=fandol]{ctex}")
        font_at = out.find("\\setmainfont{TeX Gyre Termes}")
        self.assertGreater(font_at, ctex_at)

    def test_strips_times_from_combined_usepackage(self):
        from src.formats.latex.compile_result import adapt_preamble_for_engine

        tex = (
            "\\documentclass{article}\n"
            "\\usepackage{iclr2017_conference,times}\n"
        )
        out = adapt_preamble_for_engine(tex, "ch")
        self.assertIn("\\usepackage{iclr2017_conference}", out)
        self.assertNotIn("{iclr2017_conference,times}", out)
        self.assertIn("\\setmainfont{TeX Gyre Termes}", out)


class CitationRecoveryTest(unittest.TestCase):
    def test_runs_bibtex_for_aux_with_bibliography(self):
        with TemporaryDirectory() as tmp:
            project = Path(tmp)
            build = project / "build_xelatex"
            build.mkdir()
            tex_file = project / "main.tex"
            tex_file.write_text("", encoding="utf-8")
            (build / "main.aux").write_text("\\bibstyle{x}\n\\bibdata{main}\n", encoding="utf-8")
            compiler = LaTexCompiler(str(project))

            with patch("src.formats.latex.compile.subprocess.run") as run:
                run.return_value.returncode = 0
                status = compiler._run_bibtex_if_needed(str(tex_file), str(build))

            self.assertTrue(status)
            self.assertEqual(run.call_args.args[0], ["bibtex", "build_xelatex/main"])
            self.assertEqual(run.call_args.kwargs["cwd"], str(project))

    def test_skips_bibtex_when_aux_has_no_bibliography(self):
        with TemporaryDirectory() as tmp:
            project = Path(tmp)
            build = project / "build_xelatex"
            build.mkdir()
            tex_file = project / "main.tex"
            tex_file.write_text("", encoding="utf-8")
            (build / "main.aux").write_text("\\relax\n", encoding="utf-8")
            compiler = LaTexCompiler(str(project))

            with patch("src.formats.latex.compile.subprocess.run") as run:
                status = compiler._run_bibtex_if_needed(str(tex_file), str(build))

            self.assertIsNone(status)
            run.assert_not_called()

    def test_reruns_xelatex_twice_after_bibtex(self):
        compiler = LaTexCompiler("/tmp/project")
        with patch("src.formats.latex.compile.subprocess.run") as run:
            compiler._rerun_latex_engine(
                "/tmp/project/main.tex",
                "/tmp/project/build_xelatex",
                "xelatex",
            )

        self.assertEqual(run.call_count, 2)
        self.assertEqual(run.call_args.args[0][0:2], ["xelatex", "-no-pdf"])

    def test_recovers_xdv_even_when_bibtex_fails(self):
        compiler = LaTexCompiler("/tmp/project")
        with patch.object(compiler, "_run_bibtex_if_needed", return_value=False), patch.object(
            compiler, "_finish_xdv_to_pdf", return_value=True
        ) as finish:
            ok = compiler._recover_xelatex_output(
                "/tmp/project/main.tex",
                "/tmp/project/build_xelatex",
            )
        self.assertTrue(ok)
        finish.assert_called_once_with("/tmp/project/build_xelatex")

    def test_reruns_xelatex_when_failed_log_has_undefined_citations(self):
        with TemporaryDirectory() as tmp:
            project = Path(tmp)
            build = project / "build_xelatex"
            build.mkdir()
            (build / "main.log").write_text(
                "Package natbib Warning: Citation `x' on page 1 undefined on input line 1.\n",
                encoding="utf-8",
            )
            compiler = LaTexCompiler(str(project))

            with patch.object(
                compiler, "_run_bibtex_if_needed", return_value=None
            ), patch.object(compiler, "_rerun_latex_engine") as rerun, patch.object(
                compiler, "_finish_xdv_to_pdf", return_value=True
            ):
                ok = compiler._recover_xelatex_output(
                    str(project / "main.tex"), str(build)
                )

        self.assertTrue(ok)
        rerun.assert_called_once_with(str(project / "main.tex"), str(build), "xelatex", passes=2)

    def test_skips_extra_passes_when_failed_log_is_clean(self):
        with TemporaryDirectory() as tmp:
            project = Path(tmp)
            build = project / "build_xelatex"
            build.mkdir()
            (build / "main.log").write_text("Output written on main.pdf (3 pages).\n", encoding="utf-8")
            compiler = LaTexCompiler(str(project))

            with patch.object(
                compiler, "_run_bibtex_if_needed", return_value=None
            ), patch.object(compiler, "_rerun_latex_engine") as rerun, patch.object(
                compiler, "_finish_xdv_to_pdf", return_value=True
            ):
                compiler._recover_xelatex_output(str(project / "main.tex"), str(build))

        rerun.assert_not_called()


if __name__ == "__main__":
    unittest.main()
