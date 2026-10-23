import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path

from src.gui.jobs import JobSnapshot, TranslationJob, TERMINAL, redact
from src.utils.telemetry import event_sink, request_event


class MonitorStateTest(unittest.TestCase):
    def test_timeout_is_visible_before_retry_and_recovery(self):
        state = JobSnapshot()
        events = []
        with event_sink(events.append):
            with self.assertRaises(asyncio.TimeoutError):
                with request_event("model", "section 1", 1):
                    raise asyncio.TimeoutError()
        for event in events:
            state.apply(event)
        self.assertEqual(len(state.requests), 0)
        self.assertIn("100 秒", state.issues[-1]["error"])
        self.assertEqual(state.status, "running")
        state.apply(dict(type="request_retry", model="model", part="section 1", attempt=2, delay=5))
        self.assertEqual(state.retry_count, 1)
        with event_sink(state.apply):
            with request_event("model", "section 1", 2):
                pass
        self.assertTrue(all(i["resolved"] for i in state.issues))
        self.assertEqual(state.successful_requests, 1)

    def test_failed_project_does_not_become_successful_at_job_end(self):
        state = JobSnapshot()
        state.apply({"type": "project_error", "error": "TeX failed", "traceback": "details"})
        state.apply({"type": "result", "result": {"failed_projects": [{"error": "TeX failed"}], "completed_projects": []}})
        state.apply({"type": "exit", "returncode": 0})
        self.assertEqual(state.status, "failed")
        self.assertEqual(state.issues[-1]["traceback"], "details")

    def test_crash_stop_and_partial_results(self):
        for event, expected in [({"returncode": -9}, "failed"), ({"returncode": -15, "cancelled": True}, "stopped")]:
            state = JobSnapshot()
            state.apply({"type": "exit", **event})
            self.assertEqual(state.status, expected)
            self.assertIsNotNone(state.ended)
        state = JobSnapshot()
        state.apply({"type": "result", "result": {"failed_projects": [{}], "completed_projects": [{}]}})
        self.assertEqual(state.status, "partial")

    def test_normal_repair_logs_do_not_raise_false_alarms(self):
        state = JobSnapshot()
        state.apply({"type": "log", "message": "Starting retranslation for error parts: 1"})
        state.apply({"type": "log", "message": "Body mentions 50%"})
        self.assertEqual(len(state.issues), 0)
        self.assertEqual(state.stage_progress, 0)
        state.apply({"type": "log", "message": "[####--------------------] 25.0% 正在翻译"})
        self.assertEqual(state.stage_progress, .25)

    def test_diagnostics_redact_keys_headers_urls_and_tracebacks(self):
        value = {"api_key": "hidden-value", "traceback": "Bearer top-secret /?token=top-secret", "logs": ["top-secret"]}
        safe = json.dumps(redact(value, ["top-secret"]))
        self.assertNotIn("top-secret", safe)
        self.assertNotIn("hidden-value", safe)


class WorkerProcessTest(unittest.TestCase):
    def make_job(self, folder, config):
        params = dict(config_path=str(config), model="test", url="http://127.0.0.1:1/v1/chat/completions", key="test-secret",
                      repair_model="", repair_url="", repair_key="", max_concurrent_requests=1,
                      source=str(folder / "source"), output=str(folder / "output"), source_language="en",
                      target_language="ch", mode=0, user_term="", update_term="False", all_existing=False)
        return TranslationJob(params, {"paper_list": [], "project_items": [str(folder / "missing-project")]})

    def wait_done(self, job):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            state = job.poll()
            if state.status in TERMINAL and not job.running:
                return state
            time.sleep(.02)
        job.stop()
        self.fail("worker did not finish")

    def test_real_worker_reports_startup_failure_with_traceback(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            config = folder / "config.toml"
            config.write_text('[llm_config]\napi_key="test-secret"\n')
            job = self.make_job(folder, config)
            job.start()
            try:
                state = self.wait_done(job)
                self.assertEqual(state.status, "failed")
                self.assertIn("No valid TeX projects", state.error)
                self.assertIn("Traceback", state.issues[-1]["traceback"])
                self.assertNotIn("test-secret", job.diagnostics())
            finally:
                job.stop()

    def test_stop_terminates_real_process_and_records_stopped(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            job = self.make_job(folder, folder / "config.toml")
            job.start()
            job.stop()
            state = self.wait_done(job)
            self.assertEqual(state.status, "stopped")
            self.assertIsNotNone(job.process.returncode)

    def test_log_flood_never_blocks_closed_page(self):
        job = TranslationJob({}, {})
        for i in range(5000):
            job._enqueue({"type": "log", "message": str(i)})
        job._enqueue({"type": "fatal", "error": "final error"})
        state = job.poll()
        self.assertEqual(state.status, "failed")
        self.assertEqual(state.error, "final error")
        self.assertEqual(len(state.logs), 1500)
