from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

from src.gui.server import Workbench, WorkbenchServer, existing_workbench


class LauncherTest(unittest.TestCase):
    def serve(self, server):
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 2)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server.server_port

    def launch(self, port):
        return subprocess.run(
            [sys.executable, "-m", "src.gui.launcher", "--port", str(port)],
            cwd=str(Path(__file__).resolve().parents[2]),
            capture_output=True, text=True, timeout=8,
        )

    def test_second_launch_identifies_existing_workbench(self):
        with tempfile.TemporaryDirectory() as folder:
            server = WorkbenchServer(("127.0.0.1", 0), Workbench(Path(folder) / "config.toml"))
            port = self.serve(server)
            self.assertTrue(existing_workbench("127.0.0.1", port))
            result = self.launch(port)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("已在运行", result.stdout)
            self.assertIn(f"http://127.0.0.1:{port}", result.stdout)
            self.assertIn("关闭此窗口不会停止已有服务", result.stdout)
            self.assertNotIn("Traceback", result.stderr)
            self.assertTrue(existing_workbench("127.0.0.1", port))

    def test_other_application_gets_clear_error_and_nonzero_exit(self):
        class OtherApp(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"application":"another-app"}')

        port = self.serve(ThreadingHTTPServer(("127.0.0.1", 0), OtherApp))
        result = self.launch(port)
        self.assertEqual(result.returncode, 1)
        self.assertIn(f"端口 {port}", result.stdout)
        self.assertIn("--port", result.stdout)
        self.assertNotIn("Traceback", result.stderr)

    def test_disconnected_listener_is_not_mistaken_for_a_workbench(self):
        class Disconnect(BaseHTTPRequestHandler):
            def do_GET(self):
                self.close_connection = True

        port = self.serve(ThreadingHTTPServer(("127.0.0.1", 0), Disconnect))
        result = self.launch(port)
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("Traceback", result.stderr)
