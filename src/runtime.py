import os
import tarfile
import zipfile
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

import toml

from src.agents.coordinator_agent import CoordinatorAgent
from src.formats.latex.utils import (
    batch_download_arxiv_tex,
    extract_arxiv_ids,
    extract_compressed_files,
    get_arxiv_category,
    get_profect_dirs,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ProjectEventCallback = Callable[[Dict[str, Any]], None]


def resolve_path(path_value: str) -> Path:
    p = Path(path_value)
    if p.is_absolute():
        return p
    return (PROJECT_ROOT / p).resolve()


def is_local_archive(path: str) -> bool:
    p = Path(path)
    if not path or not p.is_file():
        return False
    lower = p.name.lower()
    return lower.endswith((".zip", ".tar", ".tar.gz", ".tgz"))


def archive_project_dir(archive_path: str, projects_dir: str) -> str:
    name = os.path.basename(archive_path)
    lower = name.lower()
    if lower.endswith(".tar.gz"):
        stem = name[:-7]
    elif lower.endswith(".tgz"):
        stem = name[:-4]
    elif lower.endswith(".tar"):
        stem = name[:-4]
    elif lower.endswith(".zip"):
        stem = name[:-4]
    else:
        stem = os.path.splitext(name)[0]
    return os.path.join(projects_dir, stem)


def ensure_unique_dir(base_dir: Path) -> Path:
    if not base_dir.exists():
        return base_dir
    index = 1
    while True:
        candidate = base_dir.parent / f"{base_dir.name}_{index}"
        if not candidate.exists():
            return candidate
        index += 1


def is_within_dir(base_dir: Path, target_path: Path) -> bool:
    try:
        target_path.resolve().relative_to(base_dir.resolve())
        return True
    except ValueError:
        return False


def safe_extract_zip(zip_ref: zipfile.ZipFile, target_dir: Path) -> None:
    for member in zip_ref.infolist():
        member_path = target_dir / member.filename
        if not is_within_dir(target_dir, member_path):
            raise ValueError(f"Unsafe zip member path: {member.filename}")
    zip_ref.extractall(target_dir)


def safe_extract_tar(tar_ref: tarfile.TarFile, target_dir: Path) -> None:
    for member in tar_ref.getmembers():
        member_path = target_dir / member.name
        if not is_within_dir(target_dir, member_path):
            raise ValueError(f"Unsafe tar member path: {member.name}")
    tar_ref.extractall(target_dir)


def extract_local_archive(archive_path: str, projects_dir: str) -> str:
    target_dir = ensure_unique_dir(Path(archive_project_dir(archive_path, projects_dir)))
    target_dir.mkdir(parents=True, exist_ok=True)

    if zipfile.is_zipfile(archive_path):
        with zipfile.ZipFile(archive_path, "r") as zip_ref:
            safe_extract_zip(zip_ref, target_dir)
        return str(target_dir)

    if tarfile.is_tarfile(archive_path):
        with tarfile.open(archive_path, "r:*") as tar_ref:
            safe_extract_tar(tar_ref, target_dir)
        return str(target_dir)

    raise ValueError(f"Unsupported archive format: {archive_path}")


def split_cli_items(values: Sequence[str]) -> List[str]:
    raw = " ".join(values)
    return [item.strip() for item in raw.split(",") if item.strip()]


def split_multivalue_text(value: str) -> List[str]:
    if not value:
        return []
    normalized = value.replace("\n", ",")
    return [item.strip() for item in normalized.split(",") if item.strip()]


def load_runtime_config(
    config_path: str = "config/default.toml",
    overrides: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    config = toml.load(config_path)
    overrides = overrides or {}

    llm_config = config.setdefault("llm_config", {})
    if overrides.get("url"):
        llm_config["base_url"] = overrides["url"]
    if overrides.get("model"):
        llm_config["model"] = overrides["model"]
    if overrides.get("key"):
        llm_config["api_key"] = overrides["key"]
    if overrides.get("repair_url"):
        llm_config["repair_base_url"] = overrides["repair_url"]
    if overrides.get("repair_model"):
        llm_config["repair_model"] = overrides["repair_model"]
    if overrides.get("repair_key"):
        llm_config["repair_api_key"] = overrides["repair_key"]

    for key in ("source", "output", "source_language", "target_language", "user_term"):
        if overrides.get(key):
            mapped_key = {
                "source": "tex_sources_dir",
                "output": "output_dir",
                "source_language": "source_language",
                "target_language": "target_language",
                "user_term": "user_term",
            }[key]
            config[mapped_key] = overrides[key]

    if overrides.get("mode") is not None:
        config["mode"] = overrides["mode"]
    if overrides.get("update_term") is not None:
        config["update_term"] = overrides["update_term"]

    extra_papers = overrides.get("paper_list") or []
    if extra_papers:
        config.setdefault("paper_list", [])
        config["paper_list"].extend(extra_papers)

    return config


def prepare_projects(
    config: Dict[str, Any],
    project_items: Optional[Iterable[str]] = None,
    all_existing: bool = False,
) -> tuple[List[str], Dict[str, Any], str, str]:
    input_items = config.get("paper_list", [])
    projects_dir = str(resolve_path(config.get("tex_sources_dir", "tex source")))
    output_dir = str(resolve_path(config.get("output_dir", "outputs")))

    os.makedirs(projects_dir, exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)

    paper_list = extract_arxiv_ids(input_items)
    project_items = [item for item in (project_items or []) if item]

    if paper_list or project_items:
        projects: List[str] = []

        if paper_list:
            projects.extend(batch_download_arxiv_tex(paper_list, projects_dir))
            if not config.get("user_term"):
                config["category"] = get_arxiv_category(paper_list)
            extract_compressed_files(projects_dir)

        for project_path in project_items:
            resolved_project_path = str(resolve_path(project_path))
            if os.path.isdir(resolved_project_path):
                projects.append(os.path.abspath(resolved_project_path))
                continue
            if is_local_archive(resolved_project_path):
                try:
                    projects.append(extract_local_archive(resolved_project_path, projects_dir))
                except Exception as e:
                    print(f"[SKIP] Failed to extract local archive {project_path}: {e}")
                continue
            print(f"[SKIP] Invalid local project path: {project_path}")
    elif all_existing:
        print("No explicit inputs. Processing all existing projects in the specified directory.")
        extract_compressed_files(projects_dir)
        projects = get_profect_dirs(projects_dir)
        if not projects:
            raise ValueError("No projects found. Check 'tex_sources_dir' and 'paper_list' in config.")
    else:
        raise ValueError("No input provided. Use --arxiv or --project. To process existing projects, pass --all-existing.")

    projects = [os.path.abspath(p) for p in projects if isinstance(p, (str, os.PathLike))]
    projects = list(dict.fromkeys(projects))
    if not projects:
        raise ValueError("No valid TeX projects available for processing.")

    return projects, config, projects_dir, output_dir


def run_projects(
    config: Dict[str, Any],
    projects: Sequence[str],
    output_dir: str,
    event_callback: Optional[ProjectEventCallback] = None,
    fresh: bool = False,
) -> Dict[str, List[Dict[str, Any]]]:
    completed_projects: List[Dict[str, Any]] = []
    failed_projects: List[Dict[str, Any]] = []
    total_projects = len(projects)
    for idx, project_dir in enumerate(projects, start=1):
        project_name = os.path.basename(project_dir)
        print(f"[{idx}/{total_projects}] Processing {project_name}")
        if event_callback:
            event_callback(
                {
                    "type": "project_start",
                    "index": idx,
                    "total": total_projects,
                    "project_name": project_name,
                    "project_dir": project_dir,
                }
            )

        try:
            latex_trans = CoordinatorAgent(
                config=config,
                project_dir=project_dir,
                output_dir=output_dir,
                fresh=fresh,
            )
            latex_trans.workflow_latextrans()
        except Exception as e:
            print(f"Error processing project {project_name}: {e}")
            failed_projects.append(
                {
                    "index": idx,
                    "total": total_projects,
                    "project_name": project_name,
                    "project_dir": project_dir,
                    "error": str(e),
                }
            )
            if event_callback:
                event_callback(
                    {
                        "type": "project_error",
                        "index": idx,
                        "total": total_projects,
                        "project_name": project_name,
                        "project_dir": project_dir,
                        "error": str(e),
                    }
                )
            continue

        completed_projects.append(
            {
                "index": idx,
                "total": total_projects,
                "project_name": project_name,
                "project_dir": project_dir,
            }
        )
        if event_callback:
            event_callback(
                {
                    "type": "project_complete",
                    "index": idx,
                    "total": total_projects,
                    "project_name": project_name,
                    "project_dir": project_dir,
                }
            )
    return {
        "completed_projects": completed_projects,
        "failed_projects": failed_projects,
    }


def run_translation(
    config_path: str = "config/default.toml",
    overrides: Optional[Dict[str, Any]] = None,
    project_items: Optional[Iterable[str]] = None,
    all_existing: bool = False,
    event_callback: Optional[ProjectEventCallback] = None,
    fresh: bool = False,
) -> Dict[str, Any]:
    config = load_runtime_config(config_path=config_path, overrides=overrides)
    projects, config, projects_dir, output_dir = prepare_projects(
        config=config,
        project_items=project_items,
        all_existing=all_existing,
    )
    project_status = run_projects(
        config=config,
        projects=projects,
        output_dir=output_dir,
        event_callback=event_callback,
        fresh=fresh,
    )
    return {
        "config": config,
        "projects": projects,
        "projects_dir": projects_dir,
        "output_dir": output_dir,
        "completed_projects": project_status["completed_projects"],
        "failed_projects": project_status["failed_projects"],
    }
