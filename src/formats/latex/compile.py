from typing import List
import os
import shutil
import subprocess
from .utils import find_main_tex_file
from .compile_result import (
    compile_engine_order,
    latexmk_args,
    pdf_needs_xdvipdfmx_rewrite,
    pick_compiled_pdf,
    sanitize_pdftex_primitives,
)


class LaTexCompiler:
    def __init__(self, output_latex_dir: str):
        self.output_latex_dir = output_latex_dir

    def compile(self):
        tex_file_to_compile = find_main_tex_file(self.output_latex_dir)
        if not tex_file_to_compile:
            print("⚠️ Warning: There is no main tex file to compile in this directory.")
            return None

        with open(tex_file_to_compile, "r", encoding="utf-8") as handle:
            tex_source = handle.read()
        sanitized = sanitize_pdftex_primitives(tex_source)
        if sanitized != tex_source:
            with open(tex_file_to_compile, "w", encoding="utf-8") as handle:
                handle.write(sanitized)
            tex_source = sanitized
        self._rewrite_included_pdfs()
        engines = compile_engine_order(tex_source)
        log_dirs = []

        for index, engine in enumerate(engines):
            out_dir = os.path.join(self.output_latex_dir, f"build_{engine}")
            log_dirs.append(out_dir)
            if index == 0:
                print(f"Start compiling with {engine}...⏳")
            else:
                print(f"⚠️  Failed to generate PDF with {engines[index - 1]}. 🔁Retrying with {engine}...⏳")
            compile_ok = self._run_latexmk(tex_file_to_compile, out_dir, engine, write_success=True)
            if not compile_ok and engine == "xelatex":
                compile_ok = self._finish_xdv_to_pdf(out_dir)
            pdf_path = pick_compiled_pdf(compile_ok, self._list_pdfs(out_dir))
            if pdf_path:
                print(f"✅  Successfully generated PDF file !")
                return pdf_path

        print("⚠️  Failed to generate PDF. Please check the log.")
        for log_dir in log_dirs:
            log_files = self._list_logs(log_dir)
            if log_files:
                print(f"📄 Log files for {os.path.basename(log_dir)}: {log_files}")
        return None

    def compile_ja(self):
        tex_file_to_compile = find_main_tex_file(self.output_latex_dir)
        if not tex_file_to_compile:
            print("⚠️ Warning: There is no main tex file to compile in this directory.")
            return None
        print("Start compiling with lualatex...⏳")
        compile_out_dir_lualatex = os.path.join(self.output_latex_dir, "build_lualatex")
        lualatex_ok = self._compile_with_lualatex(tex_file_to_compile, compile_out_dir_lualatex, engine="lualatex")
        pdf_path = pick_compiled_pdf(lualatex_ok, self._list_pdfs(compile_out_dir_lualatex))
        if pdf_path:
            print(f"✅  Successfully generated PDF file !")
            return pdf_path

        print(f"⚠️  Failed to generate PDF with lualatex. Please check the log.")
        log_files_lualatex = self._list_logs(compile_out_dir_lualatex)
        if log_files_lualatex:
            print(f"📄 Log files for lualatex: {log_files_lualatex}")
        return None

    def compile_source(self, pdf_dir):
        if pdf_dir is None:
            pdf_dir = self.output_latex_dir
        os.makedirs(pdf_dir, exist_ok=True)

        tex_file_to_compile = find_main_tex_file(self.output_latex_dir)
        if not tex_file_to_compile:
            print("⚠️ Warning: No main .tex file found in directory.")
            return None

        with open(tex_file_to_compile, "r", encoding="utf-8") as handle:
            tex_source = handle.read()
        engines = compile_engine_order(tex_source)

        for index, engine in enumerate(engines):
            if index == 0:
                print(f"Start compiling with {engine}...⏳")
            else:
                print(f"⚠️ {engines[index - 1]} failed. Retrying with {engine}...⏳")
            compile_ok = self._run_latexmk(tex_file_to_compile, pdf_dir, engine, write_success=True)
            pdf_path = pick_compiled_pdf(compile_ok, self._list_pdfs(pdf_dir))
            if pdf_path:
                print(f"✅ Successfully generated PDF at: {pdf_path}")
                return pdf_path

        print("⚠️ Failed to generate PDF with available compilers.")
        log_files = self._list_logs(pdf_dir)
        if log_files:
            print("📄 Compilation logs:")
            for log in log_files:
                print(f"  - {log}")
        return None

    def _list_pdfs(self, directory: str) -> List[str]:
        if not os.path.isdir(directory):
            return []
        return [
            os.path.join(directory, file)
            for file in os.listdir(directory)
            if file.lower().endswith('.pdf') and not file.startswith('._')
        ]

    def _list_logs(self, directory: str) -> List[str]:
        if not os.path.isdir(directory):
            return []
        return [
            os.path.join(directory, file)
            for file in os.listdir(directory)
            if file.lower().endswith('.log')
        ]

    def _compile_with_pdflatex(self,
                              tex_file: str,
                              out_dir: str,
                              engine: str = "pdflatex") -> bool:
        return self._run_latexmk(tex_file, out_dir, engine, write_success=True)

    def _compile_with_xelatex(self,
                              tex_file: str,
                              out_dir: str,
                              engine: str = "xelatex") -> bool:
        return self._run_latexmk(tex_file, out_dir, engine, write_success=False)

    def _compile_with_lualatex(self,
                              tex_file: str,
                              out_dir: str,
                              engine: str = "lualatex") -> bool:
        return self._run_latexmk(tex_file, out_dir, engine, write_success=True)

    def _run_latexmk(self, tex_file: str, out_dir: str, engine: str, write_success: bool) -> bool:
        os.makedirs(out_dir, exist_ok=True)
        cmd = latexmk_args(engine, out_dir, tex_file)
        cwd = os.path.dirname(tex_file)
        try:
            subprocess.run(cmd, check=True, capture_output=True, cwd=cwd, env=self._latexmk_env())
            print("✅  Compilation successful!")
            if write_success:
                output_path = os.path.join(self.output_latex_dir, "success.txt")
                with open(output_path, "w", encoding="utf-8") as f:
                    f.write("Compilation successful\n")
            return True
        except subprocess.CalledProcessError as exc:
            print(f"⚠️  Somthing went wrong during compiling with {engine}.")
            self._print_latexmk_output(exc)
            return False

    def _latexmk_env(self):
        env = os.environ.copy()
        xelatex = shutil.which("xelatex")
        if not xelatex:
            return env
        try:
            with open(xelatex, "r", encoding="utf-8") as handle:
                header = handle.read(4000)
        except (OSError, UnicodeDecodeError):
            return env
        if not header.startswith("#!"):
            return env
        for line in header.splitlines():
            stripped = line.strip()
            if not stripped.startswith("export ") or "=" not in stripped:
                continue
            key, value = stripped[len("export "):].split("=", 1)
            env[key] = value.strip().strip('"').strip("'")
        return env

    def _rewrite_included_pdfs(self) -> None:
        gs = shutil.which("gs")
        if not gs:
            return
        for root, dirs, files in os.walk(self.output_latex_dir):
            dirs[:] = [name for name in dirs if not name.startswith("build_")]
            rel_dir = os.path.relpath(root, self.output_latex_dir)
            if rel_dir.split(os.sep)[0] != "plots":
                continue
            for name in files:
                if not name.lower().endswith(".pdf") or name.startswith("._"):
                    continue
                pdf_path = os.path.join(root, name)
                try:
                    with open(pdf_path, "rb") as handle:
                        header = handle.read(16)
                except OSError:
                    continue
                if not pdf_needs_xdvipdfmx_rewrite(header):
                    continue
                tmp_path = pdf_path + ".compat.pdf"
                cmd = [
                    gs,
                    "-q",
                    "-dSAFER",
                    "-dBATCH",
                    "-dNOPAUSE",
                    "-sDEVICE=pdfwrite",
                    "-dCompatibilityLevel=1.4",
                    f"-sOutputFile={tmp_path}",
                    pdf_path,
                ]
                result = subprocess.run(cmd, capture_output=True)
                if result.returncode == 0 and os.path.isfile(tmp_path) and os.path.getsize(tmp_path) > 0:
                    os.replace(tmp_path, pdf_path)
                    print(f"🔧 Rewrote PDF 1.4 for xdvipdfmx: {pdf_path}")
                elif os.path.isfile(tmp_path):
                    os.remove(tmp_path)

    def _finish_xdv_to_pdf(self, out_dir: str) -> bool:
        xdv_files = [
            os.path.join(out_dir, name)
            for name in os.listdir(out_dir)
            if name.lower().endswith(".xdv")
        ]
        if not xdv_files:
            return False
        xdv_path = xdv_files[0]
        pdf_path = os.path.splitext(xdv_path)[0] + ".pdf"
        cmd = ["xdvipdfmx", "-V", "7", "-o", pdf_path, xdv_path]
        try:
            subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                cwd=self.output_latex_dir,
            )
            print("✅  Converted XDV to PDF with xdvipdfmx.")
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            print("⚠️  xdvipdfmx failed to convert XDV to PDF.")
            return False

    def _print_latexmk_output(self, exc: subprocess.CalledProcessError) -> None:
        for stream_name, raw in (("stderr", exc.stderr), ("stdout", exc.stdout)):
            if not raw:
                continue
            text = raw.decode("utf-8", errors="replace").strip()
            if not text:
                continue
            tail = "\n".join(text.splitlines()[-30:])
            print(f"📄 latexmk {stream_name} (tail):\n{tail}")
