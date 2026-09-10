from typing import Any, Dict
from pathlib import Path

import toml


def merge_ui_into_config(config: Dict[str, Any], ui: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(config)
    merged["source_language"] = ui.get("source_language") or merged.get("source_language", "en")
    merged["target_language"] = ui.get("target_language") or merged.get("target_language", "ch")
    merged["mode"] = ui.get("mode", merged.get("mode", 0))
    merged["update_term"] = ui.get("update_term", merged.get("update_term", "False"))
    merged["user_term"] = ui.get("user_term", merged.get("user_term", ""))
    llm = dict(merged.get("llm_config") or {})
    llm["model"] = ui.get("model", "") or ""
    llm["base_url"] = ui.get("url", "") or ""
    llm["api_key"] = ui.get("key", "") or ""
    llm["repair_model"] = ui.get("repair_model", "") or ""
    llm["repair_base_url"] = ui.get("repair_url", "") or ""
    llm["repair_api_key"] = ui.get("repair_key", "") or ""
    merged["llm_config"] = llm
    return merged


def save_config_file(path: str, config: Dict[str, Any]) -> None:
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("w", encoding="utf-8") as handle:
        toml.dump(config, handle)
