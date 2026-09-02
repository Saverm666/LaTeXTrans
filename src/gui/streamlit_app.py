import io
import re
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

import streamlit as streamlit_backend
import toml

from src.formats.latex.bilingual import PDF_VARIANTS, variant_filename
from src.runtime import run_translation, split_multivalue_text
from src.utils.export_path import (
    copy_pdf_to_path,
    default_export_directory,
    pick_save_directory,
    sanitize_download_filename,
)
from src.utils.progress import get_progress_backend, set_progress_backend


def _collect_result_pdfs(result: Dict[str, Any]) -> List[Dict[str, str]]:
    output_dir = Path(result["output_dir"])
    target_language = result["config"].get("target_language", "ch")
    selected: List[Dict[str, str]] = []

    for project_dir in result["projects"]:
        project_name = Path(project_dir).name
        project_output_dir = output_dir / f"{target_language}_{project_name}"
        for variant, label in PDF_VARIANTS:
            pdf_path = project_output_dir / variant_filename(target_language, project_name, variant)
            if pdf_path.exists():
                selected.append(
                    {
                        "path": str(pdf_path),
                        "variant": variant,
                        "label": label,
                        "name": pdf_path.name,
                    }
                )

    return selected


class StreamlitLogWriter(io.TextIOBase):
    def __init__(self, placeholder, state: Dict[str, Any]):
        self.placeholder = placeholder
        self.state = state

    def write(self, data: str) -> int:
        if not data:
            return 0

        self.state["raw_buffer"] += data
        normalized = self.state["raw_buffer"].replace("\r", "\n")
        lines = normalized.split("\n")
        self.state["raw_buffer"] = lines.pop() if normalized and not normalized.endswith("\n") else ""

        for line in lines:
            text = line.strip()
            if not text:
                continue
            self.state["logs"].append(text)
            self._update_state_from_line(text)

        self.placeholder.code("\n".join(self.state["logs"][-300:]), language="text")
        return len(data)

    def flush(self) -> None:
        return None

    def _update_state_from_line(self, line: str) -> None:
        project_match = re.search(r"\[(\d+)/(\d+)\]\s+Processing\s+(.+)", line)
        if project_match:
            current = int(project_match.group(1))
            total = int(project_match.group(2))
            name = project_match.group(3).strip()
            self.state["project_text"].markdown(f"**工程** `{current}/{total}`  `{name}`")
            if total > 0:
                self.state["overall_bar"].progress((current - 1) / total)
            return

        progress_match = re.search(r"(\d+(?:\.\d+)?)%", line)
        if progress_match:
            percent = min(100.0, max(0.0, float(progress_match.group(1))))
            self.state["stage_bar"].progress(percent / 100.0)
            self.state["stage_text"].markdown(f"**阶段** {line}")
            return

        if line.startswith("[") or "Error processing project" in line or "Successfully" in line:
            self.state["stage_text"].markdown(f"**阶段** {line}")


def _load_defaults(config_path: str) -> Dict[str, Any]:
    try:
        return toml.load(config_path)
    except Exception:
        return {}


def _ensure_session_state() -> None:
    streamlit_backend.session_state.setdefault("job_history", [])
    streamlit_backend.session_state.setdefault("retry_failed_only", False)
    streamlit_backend.session_state.setdefault("retry_payload", None)
    streamlit_backend.session_state.setdefault("last_inputs", None)
    streamlit_backend.session_state.setdefault("last_params", None)
    streamlit_backend.session_state.setdefault("arxiv_ids", "")
    streamlit_backend.session_state.setdefault("project_paths", "")
    streamlit_backend.session_state.setdefault("export_dir", default_export_directory())


def _inject_style() -> None:
    streamlit_backend.set_page_config(
        page_title="LaTeXTrans 翻译工作台",
        page_icon="L",
        layout="wide",
    )
    streamlit_backend.markdown(
        """
        <style>
        .stApp {
            background:
                radial-gradient(circle at top left, rgba(245, 173, 92, 0.18), transparent 28%),
                radial-gradient(circle at top right, rgba(26, 96, 107, 0.18), transparent 24%),
                linear-gradient(180deg, #f7f2ea 0%, #f1ede4 100%);
        }
        .block-container {
            padding-top: 2rem;
            padding-bottom: 2rem;
        }
        .app-shell {
            padding: 1.25rem 1.5rem;
            border-radius: 24px;
            background: rgba(255, 252, 247, 0.84);
            border: 1px solid rgba(46, 56, 64, 0.08);
            box-shadow: 0 18px 60px rgba(67, 51, 32, 0.10);
            backdrop-filter: blur(12px);
        }
        .hero-title {
            font-size: 2.2rem;
            font-weight: 700;
            line-height: 1.05;
            color: #17323b;
            margin-bottom: 0.35rem;
        }
        .hero-subtitle {
            color: #5f5c53;
            font-size: 1rem;
            margin-bottom: 0;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _sidebar_form(defaults: Dict[str, Any]) -> Dict[str, Any]:
    llm_defaults = defaults.get("llm_config", {})
    streamlit_backend.sidebar.header("运行配置")
    config_path = streamlit_backend.sidebar.text_input("配置文件路径", "config/default.toml")
    source_language = streamlit_backend.sidebar.text_input("源语言", defaults.get("source_language", "en"))
    target_language = streamlit_backend.sidebar.text_input("目标语言", defaults.get("target_language", "ch"))
    model = streamlit_backend.sidebar.text_input("模型", llm_defaults.get("model", ""))
    base_url = streamlit_backend.sidebar.text_input("Base URL", llm_defaults.get("base_url", ""))
    api_key = streamlit_backend.sidebar.text_input("API Key", llm_defaults.get("api_key", ""), type="password")
    repair_model = streamlit_backend.sidebar.text_input(
        "修复模型（可选）", llm_defaults.get("repair_model", ""),
        help="仅在校验发现 LaTeX / 占位符 / 括号错误时使用。",
    )
    repair_url = streamlit_backend.sidebar.text_input("修复模型 Base URL", llm_defaults.get("repair_base_url", ""))
    repair_key = streamlit_backend.sidebar.text_input("修复模型 API Key", llm_defaults.get("repair_api_key", ""), type="password")
    mode_options = {"0 - 普通": 0, "1 - 仅重试错误": 1, "2 - 术语词典": 2}
    selected_mode = streamlit_backend.sidebar.selectbox("模式", list(mode_options.keys()), index=0)
    update_term = streamlit_backend.sidebar.checkbox(
        "更新术语表",
        value=str(defaults.get("update_term", "False")) == "True",
    )
    all_existing = streamlit_backend.sidebar.checkbox("处理源码目录中的全部已有工程", value=False)
    resume_from_checkpoint = streamlit_backend.sidebar.checkbox(
        "从断点继续",
        value=True,
        help="若存在 checkpoint，则从上次完成的环节继续。",
    )
    force_rerun = streamlit_backend.sidebar.checkbox(
        "强制重跑",
        value=False,
        help="忽略 checkpoint，从解析重新开始。",
    )
    user_term = streamlit_backend.sidebar.text_area(
        "用户术语",
        defaults.get("user_term", ""),
        height=120,
        help="可选的术语说明，会写入现有配置字段。",
    )

    return {
        "config_path": config_path,
        "source_language": source_language.strip() or "en",
        "target_language": target_language.strip() or "ch",
        "model": model.strip(),
        "url": base_url.strip(),
        "key": api_key.strip(),
        "repair_model": repair_model.strip(),
        "repair_url": repair_url.strip(),
        "repair_key": repair_key.strip(),
        "source": str(defaults.get("tex_sources_dir", "tex source")).strip(),
        "output": str(defaults.get("output_dir", "outputs")).strip(),
        "mode": mode_options[selected_mode],
        "update_term": "True" if update_term else "False",
        "all_existing": all_existing,
        "user_term": user_term.strip(),
        "fresh": bool(force_rerun or not resume_from_checkpoint),
    }


def _collect_inputs() -> Dict[str, List[str]]:
    left, right = streamlit_backend.columns([1.15, 0.85], gap="large")
    with left:
        arxiv_text = streamlit_backend.text_area(
            "arXiv 序号",
            height=120,
            placeholder="2508.18791v2, 2407.01648",
            help="用逗号或换行分隔，支持带版本号的 ID。",
            key="arxiv_ids",
        )
    with right:
        project_text = streamlit_backend.text_area(
            "本地工程或压缩包",
            height=120,
            placeholder=r"D:\path\paper.tar.gz",
            help="支持已解压目录，以及 .zip / .tar / .tar.gz / .tgz。",
            key="project_paths",
        )

    return {
        "paper_list": split_multivalue_text(arxiv_text),
        "project_items": split_multivalue_text(project_text),
    }


def _append_history(result: Dict[str, Any], params: Dict[str, Any], inputs: Dict[str, List[str]], logs: List[str]) -> None:
    pdfs = _collect_result_pdfs(result)
    output_dir = Path(result["output_dir"])
    history_item = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "params": dict(params),
        "inputs": {
            "paper_list": list(inputs["paper_list"]),
            "project_items": list(inputs["project_items"]),
        },
        "output_dir": str(output_dir),
        "projects": list(result["projects"]),
        "completed_projects": list(result["completed_projects"]),
        "failed_projects": list(result["failed_projects"]),
        "pdfs": pdfs,
        "logs": list(logs[-80:]),
    }
    streamlit_backend.session_state.job_history.insert(0, history_item)
    streamlit_backend.session_state.job_history = streamlit_backend.session_state.job_history[:12]


def _render_save_directory_picker(button_key: str = "pick_export_dir") -> None:
    if not streamlit_backend.session_state.export_dir:
        streamlit_backend.session_state.export_dir = default_export_directory()
    streamlit_backend.subheader("保存位置")
    pick_col, path_col = streamlit_backend.columns([0.35, 0.65])
    if pick_col.button("选择保存目录", key=button_key):
        picked = pick_save_directory()
        if picked:
            streamlit_backend.session_state.export_dir = picked
            streamlit_backend.rerun()
        else:
            streamlit_backend.warning("未选择目录。")
    path_col.code(streamlit_backend.session_state.export_dir or "浏览器下载文件夹", language="text")
    streamlit_backend.caption("默认是系统「下载」文件夹。点「选择保存目录」会打开文件管理器。")


def _render_result_files(result: Dict[str, Any], params: Dict[str, Any], inputs: Dict[str, List[str]]) -> None:
    completed = result["completed_projects"]
    failed = result["failed_projects"]
    pdfs = _collect_result_pdfs(result)

    streamlit_backend.subheader("结果")
    stat_a, stat_b, stat_c = streamlit_backend.columns(3)
    stat_a.metric("成功", str(len(completed)))
    stat_b.metric("失败", str(len(failed)))
    stat_c.metric("PDF 文件", str(len(pdfs)))

    if pdfs:
        with streamlit_backend.expander("生成的 PDF", expanded=True):
            streamlit_backend.caption(f"将复制到：{streamlit_backend.session_state.export_dir}")
            for idx, item in enumerate(pdfs, start=1):
                pdf_path = Path(item["path"])
                name_key = f"export_name_{item['variant']}_{idx}"
                streamlit_backend.session_state.setdefault(name_key, pdf_path.name)
                streamlit_backend.write(f"**{item['label']}** (`{item['variant']}`)")
                streamlit_backend.code(str(pdf_path), language="text")
                save_name = streamlit_backend.text_input(
                    "保存文件名",
                    key=name_key,
                )
                col_a, col_b = streamlit_backend.columns(2)
                try:
                    with open(pdf_path, "rb") as f:
                        col_a.download_button(
                            label=f"浏览器下载{item['label']}",
                            data=f.read(),
                            file_name=sanitize_download_filename(save_name or pdf_path.name),
                            mime="application/pdf",
                            key=f"download_pdf_{idx}_{item['variant']}",
                        )
                except OSError:
                    col_a.warning(f"无法读取 {pdf_path}")
                if col_b.button(f"保存{item['label']}到目录", key=f"save_pdf_{idx}_{item['variant']}"):
                    try:
                        dest = copy_pdf_to_path(
                            str(pdf_path),
                            streamlit_backend.session_state.export_dir,
                            save_name or pdf_path.name,
                        )
                        streamlit_backend.success(f"已保存到 {dest}")
                    except Exception as exc:
                        streamlit_backend.error(f"保存失败: {exc}")
    else:
        streamlit_backend.info("还没有可下载的 PDF。")

    if failed:
        failed_paths = [item["project_dir"] for item in failed]
        retry_payload = {
            "params": dict(params),
            "inputs": {
                "paper_list": [],
                "project_items": failed_paths,
            },
            "all_existing": False,
            "title": f"重试 {len(failed_paths)} 个失败工程",
        }
        if streamlit_backend.button("重试失败工程", use_container_width=True):
            streamlit_backend.session_state.retry_payload = retry_payload
            streamlit_backend.rerun()

    _append_history(result=result, params=params, inputs=inputs, logs=streamlit_backend.session_state.current_run_logs)


def _render_history() -> None:
    history = streamlit_backend.session_state.job_history
    streamlit_backend.subheader("任务记录")
    if not history:
        streamlit_backend.caption("当前会话还没有任务记录。")
        return

    for index, item in enumerate(history):
        label = (
            f"{item['timestamp']} | "
            f"{len(item['completed_projects'])} 成功 / {len(item['failed_projects'])} 失败 | "
            f"{Path(item['output_dir']).name}"
        )
        with streamlit_backend.expander(label, expanded=index == 0):
            streamlit_backend.write("输入")
            if item["inputs"]["paper_list"]:
                streamlit_backend.code("\n".join(item["inputs"]["paper_list"]), language="text")
            if item["inputs"]["project_items"]:
                streamlit_backend.code("\n".join(item["inputs"]["project_items"]), language="text")

            streamlit_backend.write("输出目录")
            streamlit_backend.code(item["output_dir"], language="text")

            if item["pdfs"]:
                streamlit_backend.write("PDF 文件")
                for pdf in item["pdfs"]:
                    if isinstance(pdf, dict):
                        streamlit_backend.write(f"{pdf.get('label', '')} (`{pdf.get('variant', '')}`)")
                        streamlit_backend.code(pdf.get("path", ""), language="text")
                    else:
                        streamlit_backend.code(pdf, language="text")

            if item["failed_projects"]:
                failed_dirs = [entry["project_dir"] for entry in item["failed_projects"]]
                streamlit_backend.write("失败工程")
                streamlit_backend.code("\n".join(failed_dirs), language="text")
                if streamlit_backend.button(
                    "重试本次失败工程",
                    key=f"retry_history_{index}",
                    use_container_width=True,
                ):
                    streamlit_backend.session_state.retry_payload = {
                        "params": dict(item["params"]),
                        "inputs": {
                            "paper_list": [],
                            "project_items": failed_dirs,
                        },
                        "all_existing": False,
                        "title": f"重试 {item['timestamp']} 的失败工程",
                    }
                    streamlit_backend.rerun()

            streamlit_backend.write("近期日志")
            streamlit_backend.code("\n".join(item["logs"]), language="text")


def _run_streamlit_job(params: Dict[str, Any], inputs: Dict[str, List[str]], title: str) -> None:
    streamlit_backend.subheader(title)
    status_col, stats_col = streamlit_backend.columns([1.6, 1], gap="large")
    with status_col:
        project_text = streamlit_backend.empty()
        stage_text = streamlit_backend.empty()
        overall_bar = streamlit_backend.progress(0.0)
        stage_bar = streamlit_backend.progress(0.0)
    with stats_col:
        stats_placeholder = streamlit_backend.empty()

    log_placeholder = streamlit_backend.empty()
    results_placeholder = streamlit_backend.empty()

    state = {
        "logs": [],
        "raw_buffer": "",
        "project_text": project_text,
        "stage_text": stage_text,
        "overall_bar": overall_bar,
        "stage_bar": stage_bar,
        "completed_projects": 0,
        "total_projects": 0,
    }
    streamlit_backend.session_state.current_run_logs = state["logs"]

    def on_event(event: Dict[str, Any]) -> None:
        if event["type"] == "project_start":
            state["total_projects"] = event["total"]
            stats_placeholder.metric("工程", f"{event['index']}/{event['total']}")
            project_text.markdown(f"**工程** `{event['index']}/{event['total']}`  `{event['project_name']}`")
            if event["total"] > 0:
                overall_bar.progress((event["index"] - 1) / event["total"])
        elif event["type"] == "project_complete":
            state["completed_projects"] = event["index"]
            stats_placeholder.metric("工程", f"{event['index']}/{event['total']}")
            if event["total"] > 0:
                overall_bar.progress(event["index"] / event["total"])
        elif event["type"] == "project_error":
            stats_placeholder.metric("工程", f"{event['index']}/{event['total']}")
            stage_text.markdown(f"**阶段** `{event['project_name']}` 出错：{event['error']}")

    overrides = {
        "paper_list": inputs["paper_list"],
        "model": params["model"],
        "url": params["url"],
        "key": params["key"],
        "repair_model": params["repair_model"],
        "repair_url": params["repair_url"],
        "repair_key": params["repair_key"],
        "source": params["source"],
        "output": params["output"],
        "source_language": params["source_language"],
        "target_language": params["target_language"],
        "mode": params["mode"],
        "user_term": params["user_term"],
        "update_term": params["update_term"],
    }

    writer = StreamlitLogWriter(log_placeholder, state)
    previous_backend = get_progress_backend()
    set_progress_backend(streamlit_backend)

    try:
        with redirect_stdout(writer), redirect_stderr(writer):
            result = run_translation(
                config_path=params["config_path"],
                overrides=overrides,
                project_items=inputs["project_items"],
                all_existing=params["all_existing"],
                event_callback=on_event,
                fresh=params.get("fresh", False),
            )
    except Exception as exc:
        stage_text.markdown(f"**阶段** 失败：{exc}")
        results_placeholder.error(f"运行失败：{exc}")
        return
    finally:
        writer.flush()
        set_progress_backend(previous_backend)

    stage_bar.progress(1.0)
    stage_text.markdown("**阶段** 已完成")
    results_placeholder.success(
        f"成功 {len(result['completed_projects'])} 个，失败 {len(result['failed_projects'])} 个。"
    )
    _render_result_files(result=result, params=params, inputs=inputs)


def main() -> None:
    _ensure_session_state()
    _inject_style()
    streamlit_backend.markdown(
        """
        <div class="app-shell">
            <div class="hero-title">LaTeXTrans 翻译工作台</div>
            <p class="hero-subtitle">
                翻译 arXiv 或本地 LaTeX 工程，可查看进度与日志、调整运行参数，并保留本次会话的任务记录。
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    default_config_path = "config/default.toml"
    defaults = _load_defaults(default_config_path)
    params = _sidebar_form(defaults)
    inputs = _collect_inputs()
    _render_save_directory_picker()

    streamlit_backend.caption(
        "填写 arXiv 序号、本地工程，或勾选处理全部已有工程。结果、失败任务和近期记录会保留在本次会话中。"
    )

    retry_payload = streamlit_backend.session_state.pop("retry_payload", None)
    if retry_payload:
        resume_params = dict(retry_payload["params"])
        resume_params["fresh"] = False
        streamlit_backend.session_state.last_inputs = dict(retry_payload["inputs"])
        streamlit_backend.session_state.last_params = resume_params
        _run_streamlit_job(
            params=resume_params,
            inputs=retry_payload["inputs"],
            title=retry_payload["title"],
        )
        _render_history()
        return

    run_clicked = streamlit_backend.button("开始翻译", type="primary", use_container_width=True)
    if run_clicked:
        if not (inputs["paper_list"] or inputs["project_items"] or params["all_existing"]):
            streamlit_backend.error("还没有输入。请填写 arXiv 序号、本地工程，或勾选处理全部已有工程。")
        else:
            config_candidate = Path(params["config_path"])
            if not config_candidate.exists():
                streamlit_backend.error(f"找不到配置文件：{params['config_path']}")
            else:
                streamlit_backend.session_state.last_inputs = dict(inputs)
                streamlit_backend.session_state.last_params = dict(params)
                _run_streamlit_job(params=params, inputs=inputs, title="当前任务")

    last_inputs = streamlit_backend.session_state.get("last_inputs")
    last_params = streamlit_backend.session_state.get("last_params")
    continue_clicked = streamlit_backend.button("继续上次任务", use_container_width=True)
    if continue_clicked:
        if not last_inputs or not last_params:
            streamlit_backend.error("没有可继续的上次任务。请先完整跑过一次，或刷新后重新填写输入。")
        else:
            resume_params = dict(last_params)
            resume_params["fresh"] = False
            _run_streamlit_job(
                params=resume_params,
                inputs=last_inputs,
                title="继续上次任务",
            )

    _render_history()


if __name__ == "__main__":
    main()
