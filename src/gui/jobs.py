"""Background process lifecycle and a UI-independent monitor model."""
from collections import deque
import atexit
from datetime import datetime
import json
import os
from pathlib import Path
import queue
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
import weakref


STAGES = {"prepare": "准备输入", "parse": "解析源码", "translate": "翻译正文", "repair": "校验修复", "compile": "生成 PDF"}
TERMINAL = {"success", "partial", "failed", "stopped"}
_live_processes = weakref.WeakSet()


@atexit.register
def _stop_workers_on_server_exit():
    for process in list(_live_processes):
        if process.poll() is not None:
            continue
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, timeout=5)
            else:
                os.killpg(process.pid, signal.SIGTERM)
        except (ProcessLookupError, OSError, subprocess.TimeoutExpired):
            pass


def redact(value, secrets=()):
    if isinstance(value, dict):
        return {k: "[已隐藏]" if k.lower() in {"api_key", "repair_api_key", "key", "repair_key", "authorization"}
                else redact(v, secrets) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v, secrets) for v in value]
    if isinstance(value, str):
        for secret in sorted((s for s in secrets if s), key=len, reverse=True):
            value = value.replace(secret, "[已隐藏]")
        value = re.sub(r"(?i)(Bearer\s+)[^\s,'\"}]+", r"\1[已隐藏]", value)
        value = re.sub(r"(?i)([?&](?:api[_-]?key|token|key)=)[^&\s'\"]+", r"\1[已隐藏]", value)
    return value


class JobSnapshot:
    def __init__(self):
        self.started = time.time()
        self.ended = None
        self.status = "running"
        self.stage = "prepare"
        self.project = "正在准备输入"
        self.index = 0
        self.total = 0
        self.completed = 0
        self.failed = 0
        self.stage_progress = 0.0
        self.stage_detail = "正在加载配置与输入"
        self.last_activity = self.started
        self.logs = deque(maxlen=1500)
        self.issues = deque(maxlen=200)
        self.requests = {}
        self.successful_requests = 0
        self.retry_count = 0
        self.retry_waits = {}
        self.result = None
        self.error = None

    def issue(self, event, level="error"):
        self.issues.append({"level": level, "resolved": False, **event})

    def apply(self, event):
        kind = event["type"]
        at = event.get("at", time.time())
        self.last_activity = at
        if kind == "log":
            message = event["message"]
            stamp = datetime.fromtimestamp(at).strftime("%H:%M:%S")
            self.logs.append(f"{stamp}  {message}")
            percent = re.search(r"^\[[#-]+\]\s+(\d+(?:\.\d+)?)%", message)
            if percent:
                self.stage_progress = min(1, float(percent.group(1)) / 100)
                self.stage_detail = message[percent.end():].strip() or STAGES.get(self.stage, self.stage)
            if re.search(r"error processing|\berror:|exception:|traceback|\bfailed\b|warning:|\[(?:skip|error|warning)\]|翻译失败|运行失败", message, re.I):
                self.issue({**event, "error": message}, "warning")
        elif kind == "project_start":
            self.project = event["project_name"]
            self.index, self.total = event["index"], event["total"]
            self.stage_progress = 0
        elif kind == "project_complete":
            self.completed += 1
        elif kind == "project_error":
            self.failed += 1
            self.issue(event)
        elif kind == "stage_start":
            self.stage = event["stage"]
            self.stage_progress = 0
            self.stage_detail = STAGES.get(self.stage, self.stage)
        elif kind == "stage_complete":
            self.stage_progress = 1
        elif kind == "request_start":
            self.requests[event["request_id"]] = event
            self.retry_waits.pop((event["model"], event["part"]), None)
        elif kind in {"request_success", "request_error", "request_cancelled"}:
            self.requests.pop(event["request_id"], None)
            if kind == "request_success":
                self.successful_requests += 1
                for issue in self.issues:
                    if (issue.get("type") in {"request_error", "request_retry"}
                            and issue.get("part") == event["part"] and issue.get("model") == event["model"]):
                        if issue.get("project_name") == event.get("project_name"):
                            issue["resolved"] = True
            elif kind == "request_error":
                self.issue(event, "warning")
        elif kind == "request_retry":
            self.retry_count += 1
            self.retry_waits[(event["model"], event["part"])] = {**event, "at": at}
            self.issue({**event, "error": f"{event['part']}：{event['delay']} 秒后重试，第 {event['attempt']}/3 次"}, "warning")
        elif kind == "fatal":
            self.error = event["error"]
            self.issue(event)
            self.status = "failed"
            self.ended = at
        elif kind == "result":
            self.result = event["result"]
            self.failed = len(self.result["failed_projects"])
            self.completed = len(self.result["completed_projects"])
            self.status = "partial" if self.failed and self.completed else "failed" if self.failed else "success"
            self.ended = at
        elif kind == "exit":
            self.requests.clear()
            self.retry_waits.clear()
            if self.status not in TERMINAL:
                self.status = "stopped" if event.get("cancelled") else "failed"
                if self.status == "failed":
                    self.error = f"翻译进程意外退出（退出码 {event['returncode']}）"
                    self.issue({**event, "error": self.error})
            self.ended = self.ended or at


class TranslationJob:
    def __init__(self, params, inputs, title="当前任务"):
        self.id = uuid.uuid4().hex
        self.params, self.inputs, self.title = dict(params), dict(inputs), title
        self.snapshot = JobSnapshot()
        self.events = queue.Queue(maxsize=4000)
        self.cancelled = False
        self.recorded = False
        self.process = None
        self.secrets = [params.get("key", ""), params.get("repair_key", "")]

    def start(self):
        overrides = {k: self.params[k] for k in (
            "model", "url", "key", "repair_model", "repair_url", "repair_key", "max_concurrent_requests",
            "source", "output", "source_language", "target_language", "mode", "user_term", "update_term",
        )}
        overrides["paper_list"] = self.inputs["paper_list"]
        overrides["export_dir"] = self.params.get("export_dir", "")
        overrides["export_variants"] = self.params.get("export_variants", ["mono", "bilingual", "sidebyside"])
        payload = dict(config_path=self.params["config_path"], overrides=overrides,
                       project_items=self.inputs["project_items"], all_existing=self.params["all_existing"],
                       fresh=self.params.get("fresh", False))
        # Include keys loaded from the config as well as UI overrides in redaction.
        try:
            import toml
            llm = toml.load(self.params["config_path"]).get("llm_config", {})
            self.secrets.extend([llm.get("api_key", ""), llm.get("repair_api_key", "")])
        except (OSError, ValueError):
            pass
        options = {"start_new_session": True} if os.name != "nt" else {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
        self.process = subprocess.Popen(
            [sys.executable, "-u", "-m", "src.gui.job_worker"],
            cwd=str(Path(__file__).resolve().parents[2]), stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", **options,
        )
        _live_processes.add(self.process)
        try:
            self.process.stdin.write(json.dumps(payload))
            self.process.stdin.close()
        except Exception:
            self.stop()
            self.process.wait()
            raise
        threading.Thread(target=self._read, daemon=True, name="translation-events").start()

    def _read(self):
        try:
            for line in self.process.stdout:
                try:
                    event = json.loads(line)
                    if not isinstance(event, dict) or "type" not in event:
                        raise ValueError("not an event")
                except (ValueError, TypeError):
                    event = {"type": "log", "message": line.strip(), "at": time.time()}
                self._enqueue(redact(event, self.secrets))
        finally:
            self.process.stdout.close()
            code = self.process.wait()
            _live_processes.discard(self.process)
            self._enqueue({"type": "exit", "returncode": code, "cancelled": self.cancelled, "at": time.time()})

    def _enqueue(self, event):
        # A closed tab must never block the child on a full stdout pipe.
        while True:
            try:
                self.events.put_nowait(event)
                return
            except queue.Full:
                try:
                    self.events.get_nowait()
                except queue.Empty:
                    pass

    def poll(self):
        for _ in range(4000):
            try:
                self.snapshot.apply(self.events.get_nowait())
            except queue.Empty:
                break
        return self.snapshot

    @property
    def running(self):
        return self.process is not None and self.process.poll() is None

    def stop(self):
        if not self.running:
            return
        self.cancelled = True
        self.snapshot.status = "stopping"
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(self.process.pid), "/T", "/F"], capture_output=True, timeout=5)
            else:
                os.killpg(self.process.pid, signal.SIGTERM)
                threading.Thread(target=self._force_stop, daemon=True).start()
        except ProcessLookupError:
            pass

    def _force_stop(self):
        try:
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def diagnostics(self):
        state = self.poll()
        return json.dumps(redact({"status": state.status, "stage": state.stage, "project": state.project,
                                 "issues": list(state.issues), "logs": list(state.logs)}, self.secrets),
                          ensure_ascii=False, indent=2)
