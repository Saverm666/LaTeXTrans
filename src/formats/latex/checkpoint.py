import hashlib
import json
import os
import shutil
from datetime import datetime, timezone
from typing import Any, Dict, Optional


STAGES = ("parse", "translate", "repair", "compile")
MAP_FILES = (
    "sections_map.json",
    "captions_map.json",
    "envs_map.json",
    "newcommands_map.json",
    "inputs_map.json",
)
SNAPSHOT_STAGES = ("parse", "translate")
CHECKPOINT_FILENAME = "checkpoint.json"


def latextrans_dir(project_output_dir: str) -> str:
    return os.path.join(project_output_dir, ".latextrans")


def snapshot_dir(project_output_dir: str, stage: str) -> str:
    return os.path.join(latextrans_dir(project_output_dir), "snapshots", stage)


def checkpoint_path(project_output_dir: str) -> str:
    return os.path.join(latextrans_dir(project_output_dir), CHECKPOINT_FILENAME)


def config_fingerprint(config: Dict[str, Any]) -> str:
    llm_config = config.get("llm_config") or {}
    payload = {
        "source_language": config.get("source_language", "en"),
        "target_language": config.get("target_language", "ch"),
        "mode": config.get("mode", 0),
        "model": llm_config.get("model", ""),
        "repair_model": llm_config.get("repair_model", ""),
    }
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def previous_stage(stage: str) -> Optional[str]:
    if stage not in STAGES:
        return None
    index = STAGES.index(stage)
    if index == 0:
        return None
    return STAGES[index - 1]


def next_stage(stage: str) -> Optional[str]:
    if stage not in STAGES:
        return None
    index = STAGES.index(stage)
    if index >= len(STAGES) - 1:
        return None
    return STAGES[index + 1]


def resolve_resume_stage(
    checkpoint: Optional[Dict[str, Any]],
    fingerprint: str,
    fresh: bool = False,
) -> Optional[str]:
    if fresh or not checkpoint:
        return "parse"
    if checkpoint.get("config_fingerprint") != fingerprint:
        return "parse"

    stage = checkpoint.get("stage")
    status = checkpoint.get("status")
    if stage not in STAGES:
        return "parse"

    if status == "completed":
        return next_stage(stage)

    if stage == "compile":
        return "compile"

    rewind_to = previous_stage(stage)
    return rewind_to or "parse"


def read_checkpoint(project_output_dir: str) -> Optional[Dict[str, Any]]:
    path = checkpoint_path(project_output_dir)
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def save_checkpoint(project_output_dir: str, data: Dict[str, Any]) -> None:
    os.makedirs(latextrans_dir(project_output_dir), exist_ok=True)
    path = checkpoint_path(project_output_dir)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)


def write_checkpoint(
    project_output_dir: str,
    stage: str,
    status: str,
    fingerprint: str,
) -> Dict[str, Any]:
    data = {
        "stage": stage,
        "status": status,
        "config_fingerprint": fingerprint,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    save_checkpoint(project_output_dir, data)
    return data


def save_snapshot(project_output_dir: str, stage: str) -> None:
    if stage not in SNAPSHOT_STAGES:
        return
    dest = snapshot_dir(project_output_dir, stage)
    os.makedirs(dest, exist_ok=True)
    for name in MAP_FILES:
        src = os.path.join(project_output_dir, name)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(dest, name))


def restore_snapshot(project_output_dir: str, stage: str) -> None:
    src_dir = snapshot_dir(project_output_dir, stage)
    for name in MAP_FILES:
        src = os.path.join(src_dir, name)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(project_output_dir, name))


def snapshot_restore_stage(run_stage: str) -> Optional[str]:
    if run_stage == "translate":
        return "parse"
    if run_stage == "repair":
        return "translate"
    return None
