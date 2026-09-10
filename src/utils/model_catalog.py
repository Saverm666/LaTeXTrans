from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit, urlunsplit

import requests


class ModelCatalogError(RuntimeError):
    """Raised when an OpenAI-compatible model catalog cannot be loaded."""


def models_url(base_url: str) -> str:
    """Return the `/models` URL for a common OpenAI-compatible API URL."""
    value = (base_url or "").strip()
    if not value:
        raise ModelCatalogError("请先填写 Base URL。")

    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ModelCatalogError("Base URL 必须是有效的 http(s) 地址。")

    path = parsed.path.rstrip("/")
    known_suffixes = ("/chat/completions", "/completions", "/responses", "/models")
    for suffix in known_suffixes:
        if path.endswith(suffix):
            path = path[: -len(suffix)]
            break

    path = f"{path}/models" if path else "/models"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def fetch_model_ids(
    base_url: str,
    api_key: str = "",
    timeout: float = 15.0,
    request_get: Optional[Any] = None,
) -> List[str]:
    """Fetch and normalize model IDs from an OpenAI-compatible `/models` API."""
    endpoint = models_url(base_url)
    headers: Dict[str, str] = {"Accept": "application/json"}
    if api_key.strip():
        headers["Authorization"] = f"Bearer {api_key.strip()}"

    get = request_get or requests.get
    try:
        response = get(endpoint, headers=headers, timeout=timeout)
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        raise ModelCatalogError(f"请求模型列表失败：{exc}") from exc
    except ValueError as exc:
        raise ModelCatalogError("模型接口没有返回有效的 JSON。") from exc

    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        raise ModelCatalogError("模型接口响应中缺少 data 列表。")

    model_ids = {
        item.get("id", "").strip()
        for item in data
        if isinstance(item, dict) and isinstance(item.get("id"), str) and item.get("id", "").strip()
    }
    if not model_ids:
        raise ModelCatalogError("模型接口返回了空列表。")
    return sorted(model_ids, key=str.casefold)
