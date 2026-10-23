"""Local HTTP application for the standalone translation workbench."""
import argparse
import asyncio
import errno
from http.client import HTTPConnection, HTTPException
import json
import mimetypes
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit
import uuid

import aiohttp
import toml

from src.gui.jobs import TranslationJob, redact
from src.runtime import PROJECT_ROOT, resolve_path
from src.formats.latex.bilingual import PDF_VARIANTS, variant_filename
from src.utils.model_catalog import ModelCatalogError, chat_completions_url, fetch_model_ids
from src.utils.webui_config import merge_ui_into_config, save_config_file
from src.utils.export_path import pick_save_directory

WEB_ROOT = Path(__file__).with_name("web")


class Workbench:
    def __init__(self, config_path=None):
        self.config_path = Path(config_path or os.environ.get("LATEXTRANS_CONFIG", PROJECT_ROOT / "config/default.toml")).resolve()
        self.lock = threading.RLock()
        self.jobs = {}
        self.probes = {}
        self.uploads = {}
        self.directory_picker_lock = threading.Lock()

    def pick_output_directory(self):
        if not any(shutil.which(command) for command in ("powershell.exe", "pwsh.exe", "zenity")):
            raise ValueError("当前环境无法打开文件管理器，请手动填写输出目录。")
        if not self.directory_picker_lock.acquire(blocking=False):
            raise ValueError("目录选择窗口已打开，请先完成选择。")
        try:
            directory = pick_save_directory()
            return {"path": directory, "cancelled": directory is None}
        except subprocess.TimeoutExpired:
            raise ValueError("目录选择超时，请重新打开选择窗口。") from None
        finally:
            self.directory_picker_lock.release()

    def config(self):
        return toml.load(self.config_path) if self.config_path.exists() else {}

    def params(self):
        config = self.config()
        llm = config.get("llm_config") or {}
        return dict(config_path=str(self.config_path), model=llm.get("model", ""), url=llm.get("base_url", ""),
                    key=llm.get("api_key", ""), repair_model=llm.get("repair_model", ""),
                    repair_url=llm.get("repair_base_url", ""), repair_key=llm.get("repair_api_key", ""),
                    max_concurrent_requests=llm.get("max_concurrent_requests", 3),
                    source=config.get("tex_sources_dir", "tex source"), output="outputs",
                    export_dir=config.get("export_dir", "" if config.get("output_dir", "outputs") == "outputs" else config["output_dir"]),
                    export_variants=config.get("export_variants", [name for name, _ in PDF_VARIANTS]),
                    source_language=config.get("source_language", "en"), target_language=config.get("target_language", "ch"),
                    mode=config.get("mode", 0), user_term=config.get("user_term", ""),
                    update_term=config.get("update_term", "False"), all_existing=False, fresh=False)

    def public_settings(self):
        params = self.params()
        for field in ("key", "repair_key"):
            params[f"{field}_saved"] = bool(params.pop(field))
        return params

    def candidate(self, changes):
        params = self.params()
        for name in params:
            if name in changes and name not in {"config_path", "key", "repair_key"}:
                params[name] = changes[name]
        for name in ("key", "repair_key"):
            if changes.get(f"clear_{name}"):
                params[name] = ""
            elif changes.get(name):
                params[name] = str(changes[name]).strip()
        params["output"] = "outputs"
        variants = params["export_variants"]
        if not isinstance(variants, list) or not variants or any(v not in dict(PDF_VARIANTS) for v in variants):
            raise ValueError("请至少选择一种有效的 PDF 导出版本。")
        params["export_variants"] = list(dict.fromkeys(variants))
        for name in ("model", "url", "repair_model", "repair_url", "source", "export_dir", "user_term", "source_language", "target_language"):
            params[name] = str(params[name]).strip()
        params["max_concurrent_requests"] = int(params["max_concurrent_requests"])
        if not 1 <= params["max_concurrent_requests"] <= 32:
            raise ValueError("并发请求数应在 1–32 之间。")
        params["mode"] = int(params["mode"])
        if params["mode"] not in (0, 1, 2):
            raise ValueError("请选择有效的翻译模式。")
        for field in ("url", "repair_url"):
            if params[field]:
                params[field] = chat_completions_url(params[field])
        if not params["source_language"] or not params["target_language"]:
            raise ValueError("请填写源语言和目标语言。")
        return params

    def save_settings(self, changes):
        with self.lock:
            params = self.candidate(changes)
            config = merge_ui_into_config(self.config(), params)
            config.update(tex_sources_dir=params["source"], output_dir=params["output"])
            config.update(export_dir=params["export_dir"], export_variants=params["export_variants"])
            save_config_file(str(self.config_path), config)
            return self.public_settings()

    def start(self, body):
        with self.lock:
            if any(job.running for job in self.jobs.values()):
                raise ValueError("已有翻译任务正在运行，请先停止或等待完成。")
            params = self.params()
            if not params["model"] or not params["url"]:
                raise ValueError("请先在模型设置中填写服务地址和模型。")
            params["url"] = chat_completions_url(params["url"])
            if params["user_term"] and not resolve_path(params["user_term"]).is_file():
                raise ValueError("找不到术语 CSV 文件，请检查高级设置中的路径。")
            params["fresh"] = bool(body.get("fresh", False))
            params["all_existing"] = bool(body.get("all_existing", False))
            inputs = {"paper_list": body.get("paper_list", []), "project_items": body.get("project_items", [])}
            if not all(isinstance(values, list) and all(isinstance(item, str) for item in values) for values in inputs.values()):
                raise ValueError("输入格式无效。")
            for upload_id in body.get("upload_ids", []):
                if upload_id not in self.uploads:
                    raise ValueError("上传文件已失效，请重新上传。")
                inputs["project_items"].append(str(self.uploads[upload_id]))
            if not any(inputs.values()) and not params["all_existing"]:
                raise ValueError("请上传源码、添加 arXiv 序号或填写本地工程路径。")
            for path in inputs["project_items"]:
                if not resolve_path(path).exists():
                    raise ValueError(f"找不到本地工程：{path}")
            job = TranslationJob(params, inputs)
            job.start()
            self.jobs[job.id] = job
            for old_id, old_job in list(self.jobs.items()):
                if len(self.jobs) <= 24:
                    break
                if old_id != job.id and not old_job.running:
                    del self.jobs[old_id]
            return {"id": job.id}

    def job(self, job_id):
        if job_id not in self.jobs:
            raise ValueError("任务已不在当前服务中，可重新添加输入并从断点继续。")
        return self.jobs[job_id]

    def files(self, job):
        result = job.snapshot.result
        if not result:
            return []
        language = result["config"].get("target_language", "ch")
        files = []
        for project in result["projects"]:
            name = Path(project).name
            for variant, label in PDF_VARIANTS:
                path = Path(result["output_dir"]) / f"{language}_{name}" / variant_filename(language, name, variant)
                if path.is_file():
                    files.append({"path": str(path), "name": path.name, "label": label, "size": path.stat().st_size})
        return files

    def snapshot(self, job):
        with self.lock:
            state = job.poll()
            fields = ("status", "stage", "project", "index", "total", "completed", "failed", "stage_progress",
                      "stage_detail", "last_activity", "started", "ended", "successful_requests", "retry_count", "error")
            data = {name: getattr(state, name) for name in fields}
            data.update(id=job.id, running=job.running, requests=list(state.requests.values()),
                        retry_waits=list(state.retry_waits.values()), issues=list(state.issues), logs=list(state.logs),
                        inputs=job.inputs, all_existing=job.params.get("all_existing", False), model=job.params.get("model"), files=[
                            {**{k: v for k, v in item.items() if k != "path"}, "url": f"/api/jobs/{job.id}/files/{index}"}
                            for index, item in enumerate(self.files(job))])
            return redact(data, job.secrets)

    def summary(self, job):
        data = self.snapshot(job)
        return {key: data[key] for key in ("id", "status", "running", "project", "started", "ended", "model", "completed", "failed", "total")}

    def probe(self, changes, target, kind="connect"):
        params = self.candidate(changes)
        repair = target == "repair"
        model = (params["repair_model"] or params["model"]) if repair else params["model"]
        url = (params["repair_url"] or params["url"]) if repair else params["url"]
        key = (params["repair_key"] or params["key"]) if repair else params["key"]
        if not model or not url:
            raise ValueError("请先填写模型和服务地址。")
        translation = kind == "translation"
        sample = r"""\section{Experimental Setup}
We evaluate a document translation system on a collection of scientific papers. The system preserves LaTeX commands, mathematical expressions, and references while translating the surrounding text into Chinese. Each document is divided into sections before translation, and the results are validated before the final PDF is compiled.

Let $x_i$ denote the source paragraph and $y_i$ denote its translation. The average processing time is $T = \frac{1}{N}\sum_{i=1}^{N} t_i$. As shown in Table~\ref{tab:results}, network latency and output length can both influence the time required to finish a request. A timeout means that the response was not received within the configured deadline. Additional measurements are needed to identify the cause.

\textbf{Implementation details.} We use a bounded number of concurrent requests and retry transient failures. The interface reports errors as soon as they occur so that users can distinguish an active request from a failed one.
"""
        probe_id = uuid.uuid4().hex
        self.probes[probe_id] = {"status": "running", "started": time.time(), "model": model,
                                 "endpoint": redact(url, [key]), "test": f"LaTeX 短文翻译（{len(sample)} 字符）" if translation else "最小生成请求"}

        async def request():
            async with aiohttp.ClientSession(trust_env=True) as session:
                messages = [{"role": "system", "content": "Translate the following LaTeX into Simplified Chinese. Preserve all LaTeX commands, equations and references. Return only translated LaTeX."},
                            {"role": "user", "content": sample}] if translation else [{"role": "user", "content": "Reply with exactly OK."}]
                async with session.post(chat_completions_url(url), json={"model": model, "messages": messages,
                    "max_new_tokens": 8192 if translation else 64},
                    headers={"Authorization": f"Bearer {key}"}, timeout=aiohttp.ClientTimeout(total=100)) as response:
                    response.raise_for_status()
                    data = await response.json()
                    content = (data.get("choices") or [{}])[0].get("message", {}).get("content")
                    if not isinstance(content, str) or not content.strip():
                        raise ValueError("接口返回成功，但没有生成文本。")
                    if translation and not any("\u4e00" <= char <= "\u9fff" for char in content):
                        raise ValueError("接口返回了文本，但未检测到中文翻译。")
                    return len(content)

        def run():
            started = time.monotonic()
            try:
                characters = asyncio.run(request())
                update = {"status": "success", "message": f"短文翻译完成，返回 {characters} 字符。测试样例与实际论文不同，长文仍可能需要更长时间。" if translation else "最小生成请求成功。长文翻译仍可能需要更长时间。"}
            except Exception as exc:
                update = {"status": "failed", "message": redact(str(exc) or "请求超过 100 秒未返回。", [key])}
            update["elapsed"] = round(time.monotonic() - started, 1)
            with self.lock:
                self.probes[probe_id].update(update)

        threading.Thread(target=run, daemon=True).start()
        return {"id": probe_id}


class WorkbenchServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, app):
        self.app = app
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, data, status=200, content_type="application/json; charset=utf-8", filename=None):
        if not isinstance(data, bytes):
            data = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if filename:
            from urllib.parse import quote
            self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{quote(filename)}")
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        app = self.server.app
        path = urlsplit(self.path).path
        parts = path.strip("/").split("/")
        try:
            if path == "/api/health":
                return self.send({"application": "LaTeXTrans", "protocol": 1})
            if path == "/api/bootstrap":
                with app.lock:
                    return self.send({"settings": app.public_settings(), "jobs": [app.summary(job) for job in reversed(list(app.jobs.values()))]})
            if path == "/api/jobs":
                with app.lock:
                    return self.send([app.summary(job) for job in reversed(list(app.jobs.values()))])
            if len(parts) >= 3 and parts[:2] == ["api", "jobs"]:
                with app.lock:
                    job = app.job(parts[2])
                    if len(parts) == 3:
                        return self.send(app.snapshot(job))
                    if len(parts) == 4 and parts[3] == "diagnostics":
                        return self.send(job.diagnostics().encode(), filename="latextrans-diagnostics.json")
                    if len(parts) == 5 and parts[3] == "files":
                        files = app.files(job)
                        index = int(parts[4])
                        if not 0 <= index < len(files):
                            raise ValueError("找不到该 PDF。")
                        file = files[index]
                        return self.send(Path(file["path"]).read_bytes(), content_type="application/pdf", filename=file["name"])
            if len(parts) == 3 and parts[:2] == ["api", "probes"]:
                with app.lock:
                    if parts[2] not in app.probes:
                        raise ValueError("连接测试不存在。")
                    return self.send(dict(app.probes[parts[2]]))
            assets = {"/": "index.html", "/app.js": "app.js", "/style.css": "style.css"}
            if path in assets:
                asset = WEB_ROOT / assets[path]
                return self.send(asset.read_bytes(), content_type=(mimetypes.guess_type(asset.name)[0] or "text/plain") + "; charset=utf-8")
            return self.send({"error": "页面不存在。"}, 404)
        except (ValueError, OSError, KeyError, ModelCatalogError) as exc:
            return self.send({"error": str(exc)}, 404)

    def do_POST(self):
        app = self.server.app
        path = urlsplit(self.path).path
        origin = self.headers.get("Origin")
        if origin and urlsplit(origin).netloc != self.headers.get("Host"):
            return self.send({"error": "请求来源无效。"}, 403)
        try:
            length = int(self.headers.get("Content-Length", 0))
            if path == "/api/uploads":
                if not 0 < length <= 100 * 1024 * 1024:
                    raise ValueError("源码压缩包应小于 100 MB。")
                name = Path(unquote(self.headers.get("X-Filename", ""))).name
                if not name.lower().endswith((".zip", ".tar", ".tar.gz", ".tgz")):
                    raise ValueError("请上传 ZIP、TAR 或 TAR.GZ 源码压缩包。")
                upload_id = uuid.uuid4().hex
                directory = resolve_path(app.params()["source"]) / ".uploads" / upload_id
                directory.mkdir(parents=True, exist_ok=True)
                dest = directory / name
                remaining = length
                with dest.open("wb") as output:
                    while remaining:
                        chunk = self.rfile.read(min(1024 * 1024, remaining))
                        if not chunk:
                            raise ValueError("上传中断，请重新上传。")
                        output.write(chunk)
                        remaining -= len(chunk)
                app.uploads[upload_id] = dest
                return self.send({"id": upload_id, "name": name, "size": length})
            if not 0 < length <= 1024 * 1024:
                raise ValueError("请求内容为空或过大。")
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError("请求格式无效。")
            if path == "/api/settings":
                return self.send(app.save_settings(body))
            if path == "/api/directories/pick":
                return self.send(app.pick_output_directory())
            if path == "/api/jobs":
                return self.send(app.start(body), 201)
            parts = path.strip("/").split("/")
            if len(parts) == 4 and parts[:2] == ["api", "jobs"] and parts[3] == "stop":
                app.job(parts[2]).stop()
                return self.send(app.snapshot(app.job(parts[2])))
            if path == "/api/models":
                params = app.candidate(body.get("settings", {}))
                repair = body.get("target") == "repair"
                url = (params["repair_url"] or params["url"]) if repair else params["url"]
                key = (params["repair_key"] or params["key"]) if repair else params["key"]
                return self.send({"models": fetch_model_ids(url, key)})
            if path == "/api/probes":
                return self.send(app.probe(body.get("settings", {}), body.get("target", "primary"), body.get("kind", "connect")), 201)
            return self.send({"error": "接口不存在。"}, 404)
        except (ValueError, OSError, TypeError, KeyError, ModelCatalogError) as exc:
            secrets = [app.params().get("key", ""), app.params().get("repair_key", "")]
            return self.send({"error": redact(str(exc), secrets)}, 400)


def existing_workbench(host, port):
    """Identify the listener locally, without following HTTP proxy settings."""
    connection = HTTPConnection(host, port, timeout=1)
    try:
        connection.request("GET", "/api/health")
        response = connection.getresponse()
        payload = json.loads(response.read(4096))
        return response.status == 200 and isinstance(payload, dict) and payload.get("application") == "LaTeXTrans"
    except (OSError, ValueError, HTTPException):
        return False
    finally:
        connection.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description="LaTeXTrans 网页翻译工作台")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8501)
    parser.add_argument("--config", default=None)
    args = parser.parse_args(argv)
    if not 0 <= args.port <= 65535:
        parser.error("端口应在 0–65535 之间。")
    access_host = "127.0.0.1" if args.host in {"0.0.0.0", ""} else args.host
    url = f"http://{access_host}:{args.port}"
    try:
        server = WorkbenchServer((args.host, args.port), Workbench(args.config))
    except OSError as exc:
        if exc.errno in {errno.EADDRINUSE, 10048} or getattr(exc, "winerror", None) == 10048:
            if existing_workbench(access_host, args.port):
                print(f"LaTeXTrans 已在运行，请直接访问：{url}", flush=True)
                print("本次未启动新服务；关闭此窗口不会停止已有服务。", flush=True)
                return 0
            alternate = args.port + 1 if args.port < 65535 else 8501
            print(f"无法启动：端口 {args.port} 已被其他程序或旧版服务占用。", flush=True)
            print(f"请关闭占用进程，或换端口启动：latextrans-gui --port {alternate}", flush=True)
        else:
            print(f"无法启动 LaTeXTrans：{exc}", flush=True)
        return 1
    print(f"LaTeXTrans 工作台：http://{access_host}:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        for job in server.app.jobs.values():
            job.stop()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
