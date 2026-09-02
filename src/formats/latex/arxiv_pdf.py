from typing import Callable, Optional
import os
import re

import requests


def arxiv_pdf_url(arxiv_id: str) -> str:
    return f"https://arxiv.org/pdf/{arxiv_id}.pdf"


def infer_arxiv_id(path_or_name: str) -> Optional[str]:
    name = os.path.basename(os.path.abspath(path_or_name) if os.path.isabs(path_or_name) else path_or_name)
    if re.match(r"^\d{4}\.\d{5,7}(?:v\d+)?$", name):
        return name
    if re.match(r"^[\w\-]+/\d{7}(?:v\d+)?$", name):
        return name
    match = re.search(r"(\d{4}\.\d{5,7}(?:v\d+)?)", name)
    return match.group(1) if match else None


def is_pdf_bytes(data: bytes) -> bool:
    return bool(data) and data.lstrip().startswith(b"%PDF")


def _default_fetch(url: str) -> bytes:
    headers = {"User-Agent": "Mozilla/5.0"}
    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()
    return response.content


def download_arxiv_pdf(
    arxiv_id: str,
    dest_path: str,
    fetch: Optional[Callable[[str], bytes]] = None,
) -> Optional[str]:
    getter = fetch or _default_fetch
    try:
        payload = getter(arxiv_pdf_url(arxiv_id))
    except Exception:
        return None
    if not is_pdf_bytes(payload):
        return None
    os.makedirs(os.path.dirname(os.path.abspath(dest_path)) or ".", exist_ok=True)
    with open(dest_path, "wb") as handle:
        handle.write(payload)
    return dest_path
