from typing import Dict, Any, List
from src.agents.tool_agents.base_tool_agent import BaseToolAgent
from pathlib import Path
import sys
import os
import shutil

from src.utils.progress import st
import time

base_dir = os.getcwd()
sys.path.append(base_dir)

 
class GeneratorAgent(BaseToolAgent):
    def __init__(self, 
                 config: Dict[str, Any],
                 project_dir: str = None,
                 output_dir: str = None  # Output directory for parsed files
                 ):
        super().__init__(agent_name="GeneratorAgent", config=config)
        self.config = config
        self.project_dir = project_dir
        self.output_dir = output_dir  # Output directory for parsed files

    def execute(self) -> Any:
        sys.stderr = open(os.devnull, 'w')
        self.process_b = st.empty()
        with self.process_b:
            self.progress_bar = st.progress(0)
        self.status_text = st.empty()
        sys.stderr = sys.__stderr__
        
        self.log(f"🤖💬 Start generating for project...⏳: {os.path.basename(self.project_dir)}.")

        sys.stderr = open(os.devnull, 'w')
        self.status_text.text("🔄 开始生成工程…")
        self.progress_bar.progress(5)
        sys.stderr = sys.__stderr__

        from src.formats.latex.compile import LaTexCompiler
        from src.formats.latex.reconstruct import LatexConstructor

        sys.stderr = open(os.devnull, 'w')
        self.status_text.text("📂 正在读取…")
        self.progress_bar.progress(10)
        sys.stderr = sys.__stderr__
        sections = self.read_file(Path(self.output_dir, "sections_map.json"), "json")
        sys.stderr = open(os.devnull, 'w')
        self.progress_bar.progress(20)
        sys.stderr = sys.__stderr__
        captions = self.read_file(Path(self.output_dir, "captions_map.json"), "json")
        sys.stderr = open(os.devnull, 'w')
        self.progress_bar.progress(30)
        sys.stderr = sys.__stderr__
        envs = self.read_file(Path(self.output_dir, "envs_map.json"), "json")
        sys.stderr = open(os.devnull, 'w')
        self.progress_bar.progress(40)
        sys.stderr = sys.__stderr__
        newcommands = self.read_file(Path(self.output_dir, "newcommands_map.json"), "json")
        sys.stderr = open(os.devnull, 'w')
        self.progress_bar.progress(50)
        sys.stderr = sys.__stderr__
        inputs = self.read_file(Path(self.output_dir, "inputs_map.json"), "json")
        sys.stderr = open(os.devnull, 'w')
        self.progress_bar.progress(60)

        self.status_text.text("📁 正在创建译文工程目录…")
        sys.stderr = sys.__stderr__

        transed_latex_dir = self._creat_transed_latex_folder(self.project_dir)
        self._ensure_original_pdf(transed_latex_dir)

        sys.stderr = open(os.devnull, 'w')
        self.progress_bar.progress(70)
        sys.stderr = sys.__stderr__

        print(transed_latex_dir)

        sys.stderr = open(os.devnull, 'w')
        self.status_text.text("🔨 正在重构 LaTeX 文档…")
        sys.stderr = sys.__stderr__
        latex_constructor = LatexConstructor(
                                sections=sections,
                                captions=captions,
                                envs=envs,
                                inputs=inputs,
                                newcommands=newcommands,
                                output_latex_dir=transed_latex_dir
                            )
        latex_constructor.construct()

        sys.stderr = open(os.devnull, 'w')
        self.progress_bar.progress(80)
        self.status_text.text("🛠️ 正在编译 PDF…")
        sys.stderr = sys.__stderr__

        latex_compiler = LaTexCompiler(output_latex_dir=transed_latex_dir)
        pdf_file = latex_compiler.compile()

        sys.stderr = open(os.devnull, 'w')
        self.progress_bar.progress(90)
        sys.stderr = sys.__stderr__
        if pdf_file:
            self._try_compile_variants(transed_latex_dir, pdf_file)

            sys.stderr = open(os.devnull, 'w')
            self.status_text.text("✅ PDF 编译成功。")
            self.progress_bar.progress(100)
            st.success(f"✅ 已生成：{os.path.basename(self.project_dir)}。")
            time.sleep(2)
            self.process_b.empty()
            self.status_text.empty()
            sys.stderr = sys.__stderr__

            self.log(f"✅ Successfully generated for {os.path.basename(self.project_dir)}.")
            return pdf_file
        else:
            sys.stderr = open(os.devnull, 'w')
            self.status_text.error("❌ PDF 编译失败。")
            self.process_b.empty()
            sys.stderr = sys.__stderr__
            return None
        
    def _creat_transed_latex_folder(self, src_dir: str) -> str:
        """
        Create a translated folder by copying the source directory and renaming it.
        """
        if not os.path.isdir(src_dir):
            raise NotADirectoryError(f"The path {src_dir} is not a valid directory.")

        base_name = os.path.basename(src_dir)
        dest_dir = os.path.join(self.output_dir, base_name)

        if os.path.exists(dest_dir):
            shutil.rmtree(dest_dir)
        shutil.copytree(src_dir, dest_dir)

        return dest_dir

    def _ensure_original_pdf(self, transed_latex_dir: str) -> None:
        from src.formats.latex.arxiv_pdf import download_arxiv_pdf, infer_arxiv_id
        from src.formats.latex.bilingual import ensure_original_pdf, preferred_original_pdf_path
        from src.formats.latex.compile import LaTexCompiler

        dest = preferred_original_pdf_path(transed_latex_dir)
        arxiv_id = infer_arxiv_id(self.project_dir) or infer_arxiv_id(transed_latex_dir)

        def download_original():
            if not arxiv_id:
                return None
            print(f"📄 Downloading original PDF from arXiv {arxiv_id}...")
            return download_arxiv_pdf(arxiv_id, dest)

        def compile_original():
            print("📄 No original PDF found; compiling original TeX...")
            return LaTexCompiler(output_latex_dir=transed_latex_dir).compile()

        original_pdf = ensure_original_pdf(
            transed_latex_dir,
            download_original=download_original,
            compile_original=compile_original,
        )
        if original_pdf:
            print(f"✅  Original PDF ready: {original_pdf}")
        else:
            print("⚠️  Could not obtain original PDF; bilingual variants may be skipped.")

    def _try_compile_variants(self, transed_latex_dir: str, translated_pdf: str) -> None:
        from src.formats.latex.bilingual import (
            compile_bilingual_pdf,
            compile_side_by_side_pdf,
            find_original_pdf,
        )

        original_pdf = find_original_pdf(transed_latex_dir, translated_pdf)
        if not original_pdf:
            print("⚠️  No original PDF found; skip bilingual and side-by-side PDFs.")
            return
        try:
            compile_bilingual_pdf(
                original_pdf,
                translated_pdf,
                os.path.join(transed_latex_dir, "bilingual.pdf"),
            )
        except Exception as exc:
            print(f"⚠️  Failed to build bilingual PDF: {exc}")
        try:
            compile_side_by_side_pdf(
                original_pdf,
                translated_pdf,
                os.path.join(transed_latex_dir, "sidebyside.pdf"),
            )
        except Exception as exc:
            print(f"⚠️  Failed to build side-by-side PDF: {exc}")
        
    


# import toml
# import argparse

# parser = argparse.ArgumentParser()
# parser.add_argument("--config", type=str, default="config/default.toml")
# args = parser.parse_args()

# config = toml.load(args.config)
# dir = "D:\code\AutoLaTexTrans\output\ch_arXiv-2504.06261v2/arXiv-2504.06261v2"
# Validator = ValidatorAgent(config=config,
#                           project_dir=config["paths"].get("project_dir", None),
#                           validator_dir=dir
#                           )
# Validator.execute()
