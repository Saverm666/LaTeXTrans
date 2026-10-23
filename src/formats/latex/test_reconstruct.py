import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from src.formats.latex.reconstruct import LatexConstructor


class LatexConstructorPreambleTest(unittest.TestCase):
    def test_adapts_preamble_in_included_decl_file(self):
        with TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "main.tex").write_text(
                "\\documentclass{article}\n\\input{01_decl}\n\\begin{document}x\\end{document}\n",
                encoding="utf-8",
            )
            (project / "01_decl.tex").write_text("\\usepackage[utf8]{inputenc}\n", encoding="utf-8")
            constructor = LatexConstructor(
                sections=[{
                    "trans_content": (
                        "\\pdfoutput=1\n"
                        "\\documentclass{article}\n"
                        "<PLACEHOLDER_01_decl_begin>\\usepackage[utf8]{inputenc}\n"
                        "<PLACEHOLDER_01_decl_end>\n"
                        "\\begin{document}x\\end{document}\n"
                    ),
                }],
                captions=[],
                envs=[],
                inputs=[{
                    "begin": "<PLACEHOLDER_01_decl_begin>",
                    "end": "<PLACEHOLDER_01_decl_end>",
                    "path": "01_decl",
                    "command": "\\input{01_decl}",
                }],
                newcommands=[],
                output_latex_dir=str(project),
                target_language="ch",
            )
            constructor.construct()

            main = (project / "main.tex").read_text(encoding="utf-8")
            decl = (project / "01_decl.tex").read_text(encoding="utf-8")
            self.assertIn("\\usepackage[UTF8,scheme=plain,fontset=fandol]{ctex}", main)
            self.assertIn("\\ifdefined\\pdfoutput", main)
            self.assertIn("% \\usepackage[utf8]{inputenc}", decl)
