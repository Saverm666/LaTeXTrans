import os
import shutil
from typing import Any, Dict, Optional
import sys
import asyncio

base_dir = os.getcwd()
sys.path.append(base_dir)

from .tool_agents.parser_agent import ParserAgent
from .tool_agents.translator_agent import TranslatorAgent
from .tool_agents.generator_agent import GeneratorAgent
from .tool_agents.validator_agent import ValidatorAgent
from src.formats.latex.compile_result import should_run_final_compile
from src.formats.latex.bilingual import variant_filename
from src.formats.latex.checkpoint import (
    STAGES,
    config_fingerprint,
    read_checkpoint,
    resolve_resume_stage,
    restore_snapshot,
    save_snapshot,
    snapshot_restore_stage,
    write_checkpoint,
)


class CoordinatorAgent:
    """
    The main orchestrator agent for the translation system.
    It coordinates the workflow of various tool agents based on document format
    and configuration.
    """

    MAX_RETRIES = 3

    def __init__(self,
                 config: Dict[str, Any],
                 project_dir: str = None,
                 output_dir: Optional[str] = None,
                 fresh: bool = False
                 ):
        self.config = config
        self.name = config.get("sys_name", "LaTeXTrans")
        self.target_language = config.get("target_language", "ch")
        self.source_language = config.get("source_language", "en")
        self.project_dir = project_dir
        self.output_dir = output_dir
        self.loop = asyncio.new_event_loop()
        self.mode = config.get("mode", 0)
        self.fresh = fresh

    def run_async(self, coro):
        return self.loop.run_until_complete(coro)

    async def workflow_latextrans_async(self) -> None:
        base_name = os.path.basename(self.project_dir)
        transed_project_dir = os.path.join(self.output_dir, f"{self.target_language}_{base_name}")
        os.makedirs(transed_project_dir, exist_ok=True)

        fingerprint = config_fingerprint(self.config)
        checkpoint = None if self.fresh else read_checkpoint(transed_project_dir)
        start_stage = resolve_resume_stage(checkpoint, fingerprint, fresh=self.fresh)
        if start_stage is None:
            print(f"🤖🎉 {self.name}: {base_name} already completed, skipping.")
            return

        if checkpoint and start_stage != "parse":
            print(f"🤖💬 {self.name}: Resuming {base_name} from stage `{start_stage}`.")

        translator_agent = None
        start_index = STAGES.index(start_stage)
        for stage in STAGES[start_index:]:
            restore_from = snapshot_restore_stage(stage)
            if restore_from:
                restore_snapshot(transed_project_dir, restore_from)
            write_checkpoint(transed_project_dir, stage, "running", fingerprint)
            try:
                if stage == "parse":
                    self._run_parse(transed_project_dir)
                    save_snapshot(transed_project_dir, "parse")
                elif stage == "translate":
                    translator_agent = self._ensure_translator(translator_agent, transed_project_dir)
                    translator_agent.trans_mode = self.mode
                    await translator_agent.execute()
                    save_snapshot(transed_project_dir, "translate")
                elif stage == "repair":
                    translator_agent = self._ensure_translator(translator_agent, transed_project_dir)
                    repaired = await self._run_repair(translator_agent, transed_project_dir)
                    if not repaired:
                        write_checkpoint(transed_project_dir, "repair", "failed", fingerprint)
                        print(
                            f"🤖🚧 {self.name}: TeX errors remain after {self.MAX_RETRIES} retries; "
                            f"skipping compile for {base_name}."
                        )
                        raise RuntimeError(f"TeX errors remain after repair for {base_name}")
                elif stage == "compile":
                    compiled = self._run_compile(transed_project_dir, base_name)
                    if not compiled:
                        write_checkpoint(transed_project_dir, "compile", "failed", fingerprint)
                        raise RuntimeError(f"Failed to compile PDF for {base_name}")
                write_checkpoint(transed_project_dir, stage, "completed", fingerprint)
            except Exception:
                write_checkpoint(transed_project_dir, stage, "failed", fingerprint)
                raise

    def _run_parse(self, transed_project_dir: str) -> None:
        parser_agent = ParserAgent(
            config=self.config,
            project_dir=self.project_dir,
            output_dir=transed_project_dir,
        )
        parser_agent.execute()

    def _ensure_translator(self, translator_agent, transed_project_dir: str) -> TranslatorAgent:
        if translator_agent is not None:
            return translator_agent
        return TranslatorAgent(
            config=self.config,
            project_dir=self.project_dir,
            output_dir=transed_project_dir,
            trans_mode=self.mode,
        )

    async def _run_repair(self, translator_agent: TranslatorAgent, transed_project_dir: str) -> bool:
        validator_agent = ValidatorAgent(
            config=self.config,
            project_dir=self.project_dir,
            output_dir=transed_project_dir,
        )
        errors_report = validator_agent.execute()
        retry_count = 0
        if errors_report:
            translator_agent.trans_mode = 1
            translator_agent.use_repair_model()

        while errors_report and retry_count < self.MAX_RETRIES:
            translator_agent.errors_report = errors_report
            await translator_agent.execute(error_retry_count=retry_count, Maxtry=self.MAX_RETRIES)
            errors_report = validator_agent.execute(errors_report)
            retry_count += 1

        if should_run_final_compile(errors_report):
            leftover = os.path.join(transed_project_dir, "errors_report.json")
            if os.path.isfile(leftover):
                os.remove(leftover)
            return True
        return False

    def _run_compile(self, transed_project_dir: str, base_name: str) -> bool:
        generator_agent = GeneratorAgent(
            config=self.config,
            project_dir=self.project_dir,
            output_dir=transed_project_dir,
        )
        try:
            pdf_file_path = generator_agent.execute()
        except Exception as e:
            print(f"🤖🚧 {self.name}: Failed to translated {base_name}.{e}")
            return False

        if pdf_file_path:
            mono_pdf_path = os.path.join(
                transed_project_dir,
                variant_filename(self.target_language, base_name, "mono"),
            )
            shutil.move(pdf_file_path, mono_pdf_path)
            print(f"🤖🎉 {self.name}: Successfully translated {base_name} to {mono_pdf_path}.")
            latex_dir = os.path.join(transed_project_dir, base_name)
            for src_name, variant in (("bilingual.pdf", "bilingual"), ("sidebyside.pdf", "sidebyside")):
                src_path = os.path.join(latex_dir, src_name)
                if os.path.isfile(src_path):
                    dest_path = os.path.join(
                        transed_project_dir,
                        variant_filename(self.target_language, base_name, variant),
                    )
                    shutil.move(src_path, dest_path)
                    print(f"🤖📖 {self.name}: {variant} PDF saved to {dest_path}.")
            return True

        print(f"🤖🚧 {self.name}: Failed to translated {base_name}.")
        return False

    def workflow_latextrans(self) -> None:
        if hasattr(self, 'loop') and not self.loop.is_closed():
            self.loop.close()

        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

        try:
            self.loop.run_until_complete(self.workflow_latextrans_async())

        finally:
            if tasks := asyncio.all_tasks(self.loop):
                self.loop.run_until_complete(
                    asyncio.gather(*tasks, return_exceptions=True)
                )

            if sys.platform == "win32":
                self.loop.run_until_complete(
                    self.loop.shutdown_asyncgens()
                )

            self.loop.run_until_complete(self.loop.shutdown_default_executor())
