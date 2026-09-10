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

        tex = "\\documentclass{article}\n\\usepackage[UTF8]{ctex}\n"
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

    def test_detects_pdf_versions_that_break_xdvipdfmx(self):
        from src.formats.latex.compile_result import pdf_needs_xdvipdfmx_rewrite

        self.assertFalse(pdf_needs_xdvipdfmx_rewrite(b"%PDF-1.5\n"))
        self.assertTrue(pdf_needs_xdvipdfmx_rewrite(b"%PDF-1.7\n"))


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


if __name__ == "__main__":
    unittest.main()
