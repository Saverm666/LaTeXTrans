import argparse
import os
import tarfile
import zipfile
from pathlib import Path

import toml

from src.agents.coordinator_agent import CoordinatorAgent
from src.formats.latex.prompts import *
from src.formats.latex.utils import (
    batch_download_arxiv_tex,
    extract_arxiv_ids,
    extract_compressed_files,
    get_arxiv_category,
    get_profect_dirs,
)

PROJECT_ROOT = Path(__file__).resolve().parent


def _resolve_path(path_value: str) -> Path:
    p = Path(path_value)
    if p.is_absolute():
        return p
    return (PROJECT_ROOT / p).resolve()


def _is_local_archive(path: str) -> bool:
    p = Path(path)
    if not path or not p.is_file():
        return False
    lower = p.name.lower()
    return lower.endswith((".zip", ".tar", ".tar.gz", ".tgz"))


def _archive_project_dir(archive_path: str, projects_dir: str) -> str:
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

def _ensure_unique_dir(base_dir: Path) -> Path:
    if not base_dir.exists():
        return base_dir
    index = 1
    while True:
        candidate = base_dir.parent / f"{base_dir.name}_{index}"
        if not candidate.exists():
            return candidate
        index += 1


def _is_within_dir(base_dir: Path, target_path: Path) -> bool:
    try:
        target_path.resolve().relative_to(base_dir.resolve())
        return True
    except ValueError:
        return False


def _safe_extract_zip(zip_ref: zipfile.ZipFile, target_dir: Path) -> None:
    for member in zip_ref.infolist():
        member_path = target_dir / member.filename
        if not _is_within_dir(target_dir, member_path):
            raise ValueError(f"Unsafe zip member path: {member.filename}")
    zip_ref.extractall(target_dir)


def _safe_extract_tar(tar_ref: tarfile.TarFile, target_dir: Path) -> None:
    for member in tar_ref.getmembers():
        member_path = target_dir / member.name
        if not _is_within_dir(target_dir, member_path):
            raise ValueError(f"Unsafe tar member path: {member.name}")
    tar_ref.extractall(target_dir)


def _extract_local_archive(archive_path: str, projects_dir: str) -> str:
    target_dir = _ensure_unique_dir(Path(_archive_project_dir(archive_path, projects_dir)))
    target_dir.mkdir(parents=True, exist_ok=True)

    if zipfile.is_zipfile(archive_path):
        with zipfile.ZipFile(archive_path, "r") as zip_ref:
            _safe_extract_zip(zip_ref, target_dir)
        return str(target_dir)

    if tarfile.is_tarfile(archive_path):
        with tarfile.open(archive_path, "r:*") as tar_ref:
            _safe_extract_tar(tar_ref, target_dir)
        return str(target_dir)

    raise ValueError(f"Unsupported archive format: {archive_path}")


def main():
    """
    Main function to run the LaTeXTrans application.
    Allows overriding paper_list from command-line arguments.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=str, default="config/default.toml", help="Path to the config TOML file.")
    parser.add_argument("--model", type=str, default="", help="Model for translating.")
    parser.add_argument("--url", type=str, default="", help="Model url.")
    parser.add_argument("--key", type=str, default="", help="Model key.")
    parser.add_argument("--repair-model", type=str, default="", help="Model used only for validation-error repair.")
    parser.add_argument("--repair-url", type=str, default="", help="Endpoint for the validation-error repair model.")
    parser.add_argument("--repair-key", type=str, default="", help="API key for the validation-error repair model.")
    parser.add_argument("--arxiv", nargs="+", default=[], help="arXiv ID(s), comma-separated.")
    parser.add_argument(
        "--project",
        nargs="+",
        default=[],
        help="Local project path(s) or archive path(s), comma-separated.",
    )
    parser.add_argument("--output", type=str, default="", help="output directory.")
    parser.add_argument("--source", type=str, default="", help="tex source directory.")
    parser.add_argument(
        "--all-existing",
        action="store_true",
        help="Process all existing projects under tex source directory when no --arxiv/--project is provided.",
    )
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="Ignore checkpoints and rerun from parse.",
    )

    args = parser.parse_args()
    config = toml.load(args.config)

    if args.url:
        config["llm_config"]["base_url"] = args.url
    if args.arxiv:
        arxiv_raw = " ".join(args.arxiv)
        arxiv_items = [item.strip() for item in arxiv_raw.split(",") if item.strip()]
        config["paper_list"].extend(arxiv_items)

    project_items = []
    if args.project:
        project_raw = " ".join(args.project)
        project_items = [item.strip() for item in project_raw.split(",") if item.strip()]

    if args.model:
        config["llm_config"]["model"] = args.model
    if args.key:
        config["llm_config"]["api_key"] = args.key
    if args.repair_model:
        config["llm_config"]["repair_model"] = args.repair_model
    if args.repair_url:
        config["llm_config"]["repair_base_url"] = args.repair_url
    if args.repair_key:
        config["llm_config"]["repair_api_key"] = args.repair_key
    if args.source:
        config["tex_sources_dir"] = args.source
    if args.output:
        config["output_dir"] = args.output

    input_items = config.get("paper_list", [])
    projects_dir = str(_resolve_path(config.get("tex_sources_dir", "tex source")))
    output_dir = str(_resolve_path(config.get("output_dir", "outputs")))

    os.makedirs(projects_dir, exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)

    paper_list = extract_arxiv_ids(input_items)

    if paper_list or project_items:
        projects = []

        if paper_list:
            projects.extend(batch_download_arxiv_tex(paper_list, projects_dir))
            if not config.get("user_term"):
                config["category"] = get_arxiv_category(paper_list)
            # Keep legacy behavior for downloaded arXiv sources.
            extract_compressed_files(projects_dir)

        for project_path in project_items:
            resolved_project_path = str(_resolve_path(project_path))
            if os.path.isdir(resolved_project_path):
                projects.append(os.path.abspath(resolved_project_path))
                continue
            if _is_local_archive(resolved_project_path):
                try:
                    projects.append(_extract_local_archive(resolved_project_path, projects_dir))
                except Exception as e:
                    print(f"[SKIP] Failed to extract local archive {project_path}: {e}")
                continue
            print(f"[SKIP] Invalid local project path: {project_path}")
    elif args.all_existing:
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

    total_projects = len(projects)
    for idx, project_dir in enumerate(projects, start=1):
        print(f"[{idx}/{total_projects}] Processing {os.path.basename(project_dir)}")

        try:
            latex_trans = CoordinatorAgent(
                config=config,
                project_dir=project_dir,
                output_dir=output_dir,
                fresh=args.fresh,
            )
            latex_trans.workflow_latextrans()
        except Exception as e:
            print(f"Error processing project {os.path.basename(project_dir)}: {e}")
            continue


if __name__ == "__main__":
    main()
