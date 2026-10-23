"""HTTP contracts for the standalone UI, including real worker lifecycle."""
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import zipfile

import requests
import toml

from src.gui.jobs import TranslationJob
from src.gui.server import Workbench, WorkbenchServer
from src.utils.model_catalog import ModelCatalogError


class WorkbenchHTTPTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.config = self.folder / "config.toml"
        self.config.write_text(toml.dumps({
            "source_language": "en", "target_language": "ch",
            "tex_sources_dir": str(self.folder / "source"), "output_dir": str(self.folder / "output"),
            "llm_config": {"model": "test-model", "base_url": "http://127.0.0.1:1/v1/chat/completions", "api_key": "private-test-secret"},
        }))
        self.app = Workbench(self.config)
        self.server = WorkbenchServer(("127.0.0.1", 0), self.app)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.client = requests.Session()
        self.client.trust_env = False

    def tearDown(self):
        for job in self.app.jobs.values():
            job.stop()
            if job.process:
                job.process.wait(timeout=5)
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.client.close()
        self.temp.cleanup()

    def test_bootstrap_hides_saved_keys_and_partial_save_preserves_them(self):
        response = self.client.get(self.url + "/api/bootstrap")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("private-test-secret", response.text)
        self.assertTrue(response.json()["settings"]["key_saved"])
        response = self.client.post(self.url + "/api/settings", json={"model": "new-model", "key": ""})
        self.assertEqual(response.status_code, 200)
        config = toml.load(self.config)
        self.assertEqual(config["llm_config"]["api_key"], "private-test-secret")
        self.assertEqual(config["llm_config"]["model"], "new-model")

    def test_invalid_url_and_model_fetch_failure_return_visible_errors(self):
        response = self.client.post(self.url + "/api/settings", json={"url": "invalid"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("http", response.json()["error"])
        with patch("src.gui.server.fetch_model_ids", side_effect=ModelCatalogError("模型列表请求超时")):
            response = self.client.post(self.url + "/api/models", json={"target": "primary"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("超时", response.json()["error"])

    def test_cross_origin_changes_are_rejected(self):
        response = self.client.post(self.url + "/api/settings", json={"model": "changed"}, headers={"Origin": "https://another.example"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.app.params()["model"], "test-model")

    def test_upload_and_missing_input_validation(self):
        response = self.client.post(self.url + "/api/jobs", json={})
        self.assertEqual(response.status_code, 400)
        response = self.client.post(self.url + "/api/jobs", json={"project_items": [str(self.folder / "missing")]})
        self.assertEqual(response.status_code, 400)
        self.assertIn("找不到", response.json()["error"])
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as target:
            target.writestr("main.tex", "test document")
        response = self.client.post(self.url + "/api/uploads", data=archive.getvalue(), headers={"X-Filename": "paper.zip"})
        self.assertEqual(response.status_code, 200)
        uploaded = self.app.uploads[response.json()["id"]]
        self.assertEqual(uploaded.read_bytes(), archive.getvalue())
        self.assertTrue(uploaded.is_relative_to(self.folder / "source"))
        response = self.client.post(self.url + "/api/uploads", data=b"hello", headers={"X-Filename": "script.exe"})
        self.assertEqual(response.status_code, 400)

    def test_completed_pdf_download_and_diagnostics(self):
        output = self.folder / "output"
        pdf_dir = output / "ch_paper"
        pdf_dir.mkdir(parents=True)
        pdf = b"%PDF-1.4\n%%EOF"
        (pdf_dir / "ch_paper_mono.pdf").write_bytes(pdf)
        job = TranslationJob(self.app.params(), {"paper_list": [], "project_items": ["paper"]})
        job.snapshot.apply({"type": "fatal", "error": "private-test-secret", "traceback": "Traceback details"})
        job.snapshot.apply({"type": "result", "result": {
            "output_dir": str(output), "config": {"target_language": "ch"}, "projects": ["paper"],
            "completed_projects": [{"project_dir": "paper"}], "failed_projects": [],
        }})
        self.app.jobs[job.id] = job
        response = self.client.get(self.url + f"/api/jobs/{job.id}")
        self.assertEqual(response.json()["status"], "success")
        self.assertEqual(len(response.json()["files"]), 1)
        self.assertNotIn("private-test-secret", response.text)
        file_url = response.json()["files"][0]["url"]
        self.assertEqual(self.client.get(self.url + file_url).content, pdf)
        diagnostics = self.client.get(self.url + f"/api/jobs/{job.id}/diagnostics")
        self.assertIn("Traceback details", diagnostics.text)
        self.assertNotIn("private-test-secret", diagnostics.text)
        self.assertEqual(self.client.get(self.url + "/config/default.toml").status_code, 404)

    def test_real_worker_can_be_started_and_stopped_through_http(self):
        project = self.folder / "paper"
        project.mkdir()
        response = self.client.post(self.url + "/api/jobs", json={"project_items": [str(project)]})
        self.assertEqual(response.status_code, 201)
        job_id = response.json()["id"]
        response = self.client.post(self.url + f"/api/jobs/{job_id}/stop", json={})
        self.assertEqual(response.status_code, 200)
        job = self.app.jobs[job_id]
        job.process.wait(timeout=5)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and job.poll().status != "stopped":
            time.sleep(.01)
        self.assertEqual(job.snapshot.status, "stopped")

    def test_static_ui_is_served_with_expected_assets(self):
        page = self.client.get(self.url + "/")
        self.assertEqual(page.status_code, 200)
        self.assertIn("/app.js", page.text)
        self.assertIn("/style.css", page.text)
        self.assertEqual(self.client.get(self.url + "/app.js").status_code, 200)

    def test_directory_selection_is_saved_only_after_settings_confirmation(self):
        directory = str(self.folder / "chosen output")
        original = self.config.read_text()
        with patch("src.gui.server.shutil.which", return_value="picker"), patch("src.gui.server.pick_save_directory", return_value=directory):
            response = self.client.post(self.url + "/api/directories/pick", json={})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"path": directory, "cancelled": False})
        self.assertEqual(self.config.read_text(), original)
        response = self.client.post(self.url + "/api/settings", json={"export_dir": directory, "export_variants": ["mono", "sidebyside"]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(toml.load(self.config)["output_dir"], "outputs")
        self.assertEqual(toml.load(self.config)["export_dir"], directory)
        self.assertEqual(self.app.params()["export_variants"], ["mono", "sidebyside"])

    def test_directory_cancel_and_failure_preserve_existing_output(self):
        original = self.config.read_text()
        with patch("src.gui.server.shutil.which", return_value="picker"), patch("src.gui.server.pick_save_directory", return_value=None):
            response = self.client.post(self.url + "/api/directories/pick", json={})
        self.assertTrue(response.json()["cancelled"])
        with patch("src.gui.server.shutil.which", return_value="picker"), patch("src.gui.server.pick_save_directory", side_effect=OSError("无法打开文件管理器")):
            response = self.client.post(self.url + "/api/directories/pick", json={})
        self.assertEqual(response.status_code, 400)
        self.assertIn("无法打开", response.json()["error"])
        self.assertFalse(self.app.directory_picker_lock.locked())
        self.assertEqual(self.config.read_text(), original)

    def test_legacy_output_directory_becomes_pdf_destination(self):
        self.assertEqual(self.app.params()["output"], "outputs")
        self.assertEqual(self.app.params()["export_dir"], str(self.folder / "output"))
        response = self.client.post(self.url + "/api/settings", json={"model": "test-model"})
        self.assertEqual(response.status_code, 200)
        config = toml.load(self.config)
        self.assertEqual(config["output_dir"], "outputs")
        self.assertEqual(config["export_dir"], str(self.folder / "output"))

    def test_invalid_export_versions_do_not_change_settings(self):
        original = self.config.read_text()
        for variants in ([], ["invalid"], "mono"):
            response = self.client.post(self.url + "/api/settings", json={"export_variants": variants})
            self.assertEqual(response.status_code, 400)
        self.assertEqual(self.config.read_text(), original)
