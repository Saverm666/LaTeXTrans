import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.runtime import export_project_pdfs, load_runtime_config, run_projects


class PdfExportTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.output = self.root / "outputs"
        self.work = self.output / "ch_paper"
        self.work.mkdir(parents=True)
        for variant in ("mono", "bilingual", "sidebyside"):
            (self.work / f"ch_paper_{variant}.pdf").write_bytes(b"%PDF-test-" + variant.encode())
        (self.work / "sections_map.json").write_text("{}")
        (self.work / ".latextrans").mkdir()
        (self.work / "paper").mkdir()
        self.target = self.root / "saved PDFs"
        self.config = {"target_language": "ch", "export_dir": str(self.target),
                       "export_variants": ["mono", "sidebyside"]}

    def test_only_selected_pdfs_are_copied_and_work_files_remain(self):
        with patch("src.runtime.CoordinatorAgent") as coordinator:
            result = run_projects(self.config, [str(self.root / "paper")], str(self.output))
        self.assertEqual(len(result["completed_projects"]), 1)
        self.assertEqual(result["failed_projects"], [])
        self.assertEqual(coordinator.call_args.kwargs["output_dir"], str(self.output))
        self.assertEqual(sorted(p.name for p in self.target.iterdir()),
                         ["ch_paper_mono.pdf", "ch_paper_sidebyside.pdf"])
        self.assertTrue((self.work / "sections_map.json").exists())
        self.assertTrue((self.work / "ch_paper_bilingual.pdf").exists())
        self.assertEqual((self.target / "ch_paper_mono.pdf").read_bytes(),
                         (self.work / "ch_paper_mono.pdf").read_bytes())

    def test_copy_failure_is_reported_and_keeps_work_files(self):
        self.target.write_text("a file blocks the destination directory")
        events = []
        with patch("src.runtime.CoordinatorAgent"):
            result = run_projects(self.config, [str(self.root / "paper")], str(self.output), events.append)
        self.assertEqual(len(result["failed_projects"]), 1)
        self.assertTrue(any(e["type"] == "project_error" for e in events))
        self.assertTrue((self.work / "ch_paper_mono.pdf").exists())

    def test_blank_export_directory_saves_only_in_workspace(self):
        self.config["export_dir"] = ""
        self.assertEqual(export_project_pdfs(self.config, str(self.output), "paper"), [])
        self.assertFalse(self.target.exists())

    def test_missing_variant_is_reported(self):
        (self.work / "ch_paper_sidebyside.pdf").unlink()
        with self.assertRaisesRegex(FileNotFoundError, "sidebyside"):
            export_project_pdfs(self.config, str(self.output), "paper")

    def test_empty_override_disables_previously_saved_export(self):
        config_file = self.root / "config.toml"
        config_file.write_text('export_dir = "old-directory"\nexport_variants = ["mono"]\n')
        config = load_runtime_config(str(config_file), {"export_dir": "", "export_variants": ["bilingual"]})
        self.assertEqual(config["export_dir"], "")
        self.assertEqual(config["export_variants"], ["bilingual"])
