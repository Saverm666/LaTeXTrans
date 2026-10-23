import asyncio
import gc
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import sys
import threading
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from streamlit.runtime.scriptrunner_utils.exceptions import StopException

from src.agents.tool_agents.translator_agent import TranslatorAgent


class TranslatorTasksTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.stderr = sys.stderr
        self.ui = patch("src.agents.tool_agents.translator_agent.st", MagicMock())
        self.ui.start()
        self.addCleanup(self.ui.stop)
        self.addCleanup(setattr, sys, "stderr", self.stderr)
        self.loop_errors = []
        loop = asyncio.get_running_loop()
        previous_handler = loop.get_exception_handler()
        loop.set_exception_handler(lambda loop, context: self.loop_errors.append(context))
        self.addCleanup(loop.set_exception_handler, previous_handler)
        self.agent = TranslatorAgent(
            {"source_language": "en", "target_language": "ch", "llm_config": {}},
            project_dir="project", output_dir="output",
        )
        self.sections = [{"section": str(i), "content": "text"} for i in range(3)]
        self.agent.add_placeholder = MagicMock()
        self.agent.build_term_dict = MagicMock()
        self.agent.log = MagicMock()
        self.agent.read_file = lambda path, kind: self.sections.copy() if path.name == "sections_map.json" else []
        self.agent.save_file = MagicMock()
        self.agent._val_fail_parts = AsyncMock()

    async def test_worker_stop_retrieves_sibling_exceptions_and_cancels_pending(self):
        started = asyncio.Event()
        release = asyncio.Event()
        workers = []
        cancelled = []

        async def translate(section, *args):
            workers.append(asyncio.current_task())
            if len(workers) == 3:
                started.set()
            if section["section"] in ("0", "1"):
                await release.wait()
                raise StopException()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.append(section["section"])

        self.agent.translate = translate
        execution = asyncio.create_task(self.agent.execute())
        await asyncio.wait_for(started.wait(), 2)
        release.set()
        with self.assertRaises(StopException):
            await asyncio.wait_for(execution, 2)
        self.assertTrue(all(task.done() for task in workers))
        self.assertEqual(cancelled, ["2"])
        self.agent._val_fail_parts.assert_not_awaited()
        del workers[:]
        gc.collect()
        await asyncio.sleep(0)
        self.assertEqual(self.loop_errors, [])

    async def test_external_cancellation_waits_for_worker_cleanup(self):
        started = asyncio.Event()
        workers = []
        cleaned = []

        async def translate(section, *args):
            workers.append(asyncio.current_task())
            if len(workers) == 3:
                started.set()
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0)
                cleaned.append(section["section"])

        self.agent.translate = translate
        execution = asyncio.create_task(self.agent.execute())
        await asyncio.wait_for(started.wait(), 2)
        execution.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(execution, 2)
        self.assertCountEqual(cleaned, ["0", "1", "2"])
        self.assertTrue(all(task.done() for task in workers))

    async def test_success_keeps_section_order_and_saves_results(self):
        async def translate(section, *args):
            await asyncio.sleep(0)
            return dict(section, trans_content=section["section"])

        self.agent.translate = translate
        await self.agent.execute()
        self.agent._val_fail_parts.assert_awaited_once()
        saved_sections = self.agent._val_fail_parts.call_args.kwargs["sections"]
        self.assertEqual([s["trans_content"] for s in saved_sections], ["0", "1", "2"])
        self.assertEqual(self.agent.save_file.call_count, 9)

    async def test_translation_requests_follow_the_configured_http_proxy(self):
        forwarded = []

        class Proxy(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                forwarded.append((self.path, payload["model"]))
                body = json.dumps({"choices": [{"message": {"content": "translated"}}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        proxy = ThreadingHTTPServer(("127.0.0.1", 0), Proxy)
        threading.Thread(target=proxy.serve_forever, daemon=True).start()
        self.agent.base_url = "http://translation.invalid/v1/chat/completions"

        async def translate(section, envs, captions, session):
            content = await self.agent._request_llm_for_trans("Translate", section["content"], section["section"], "sec", session)
            return dict(section, trans_content=content)

        self.agent.translate = translate
        proxy_url = f"http://127.0.0.1:{proxy.server_port}"
        try:
            with patch.dict(os.environ, {"HTTP_PROXY": proxy_url, "http_proxy": proxy_url, "NO_PROXY": "", "no_proxy": ""}):
                await asyncio.wait_for(self.agent.execute(), 3)
            self.assertEqual(len(forwarded), 3)
            self.assertTrue(all(path == self.agent.base_url for path, model in forwarded))
            saved = self.agent._val_fail_parts.call_args.kwargs["sections"]
            self.assertTrue(all(section["trans_content"] == "translated" for section in saved))
        finally:
            await asyncio.to_thread(proxy.shutdown)
            proxy.server_close()

    async def test_progress_stop_cancels_workers_and_restores_stderr(self):
        started = asyncio.Event()
        release = asyncio.Event()
        workers = []

        async def translate(section, *args):
            workers.append(asyncio.current_task())
            if len(workers) == 3:
                started.set()
            await release.wait()
            if section["section"] == "0":
                return section
            await asyncio.Event().wait()

        def progress(value, **kwargs):
            if kwargs.get("text"):
                raise StopException()

        self.agent.translate = translate
        with patch("src.agents.tool_agents.translator_agent.st") as backend:
            backend.progress.return_value.progress.side_effect = progress
            execution = asyncio.create_task(self.agent.execute())
            await asyncio.wait_for(started.wait(), 2)
            release.set()
            with self.assertRaises(StopException):
                await asyncio.wait_for(execution, 2)
        self.assertTrue(all(task.done() for task in workers))
        self.assertIs(sys.stderr, self.stderr)

    async def test_retry_stop_cleans_up_nested_workers(self):
        self.agent.errors_report = [{"part": "sec", "num_or_ph": "0"}]
        workers = []

        async def translate_section(*args, **kwargs):
            workers.append(asyncio.current_task())
            raise StopException()

        self.agent._translate_section = translate_section
        with self.assertRaises(StopException):
            await asyncio.wait_for(
                self.agent._retranslate_error_parts(self.sections, [], [], None), 2,
            )
        self.assertTrue(all(task.done() for task in workers))
        del workers[:]
        gc.collect()
        await asyncio.sleep(0)
        self.assertEqual(self.loop_errors, [])

    async def test_timeout_retries_then_returns_original_and_records_failure(self):
        session = MagicMock()
        session.post.side_effect = asyncio.TimeoutError()
        with patch("src.agents.tool_agents.translator_agent.asyncio.sleep", new_callable=AsyncMock):
            result = await self.agent._request_llm_for_trans(
                "Translate", "original text", "1", "sec", session,
            )
        self.assertEqual(result, "original text")
        self.assertEqual(session.post.call_count, 3)
        self.assertTrue(self.agent.have_fail_parts)
        self.assertEqual(self.agent.fail_section_nums, ["1"])
