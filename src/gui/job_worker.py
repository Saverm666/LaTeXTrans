"""Isolated translation runner. stdin: job JSON; stdout: JSON events."""
import io
import json
import sys
import threading
import time
import traceback
from contextlib import redirect_stdout, redirect_stderr

from src.utils.telemetry import event_sink


class EventLogWriter(io.TextIOBase):
    def __init__(self, send):
        self.send = send
        self.buffer = ""

    def write(self, data):
        self.buffer += data.replace("\r", "\n")
        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            if line.strip():
                self.send({"type": "log", "message": line.strip()})
        # Bound buffers from tools that never emit newlines.
        if len(self.buffer) > 8192:
            self.flush()
        return len(data)

    def flush(self):
        if self.buffer.strip():
            self.send({"type": "log", "message": self.buffer.strip()})
        self.buffer = ""


def main():
    job = json.load(sys.stdin)
    output = sys.stdout
    lock = threading.Lock()
    current_project = ""

    def send(event):
        nonlocal current_project
        if event["type"] == "project_start":
            current_project = event["project_name"]
        with lock:
            output.write(json.dumps({"at": time.time(), "project_name": current_project, **event}, ensure_ascii=False, default=str) + "\n")
            output.flush()

    writer = EventLogWriter(send)
    with redirect_stdout(writer), redirect_stderr(writer), event_sink(send):
        try:
            from src.runtime import run_translation
            result = run_translation(**job, event_callback=send)
            writer.flush()
            send({"type": "result", "result": result})
        except BaseException as exc:
            writer.flush()
            send({"type": "fatal", "error": str(exc) or type(exc).__name__,
                  "traceback": traceback.format_exc()})
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
