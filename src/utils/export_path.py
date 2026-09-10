from typing import Callable, Optional
import re
import shutil
import subprocess
from pathlib import Path


def default_export_directory(fallback: str = "", users_root: Optional[str] = None) -> str:
    root = Path(users_root) if users_root else Path("/mnt/c/Users")
    skip = {"Public", "Default", "Default User", "All Users"}
    if root.is_dir():
        for child in sorted(root.iterdir()):
            if child.name in skip or not child.is_dir():
                continue
            downloads = child / "Downloads"
            if downloads.is_dir():
                return str(downloads)
    home_downloads = Path.home() / "Downloads"
    if home_downloads.is_dir():
        return str(home_downloads)
    return fallback


def sanitize_download_filename(filename: str) -> str:
    name = Path(str(filename).strip()).name
    if not name:
        raise ValueError("Filename is empty.")
    if not name.lower().endswith(".pdf"):
        name = f"{name}.pdf"
    return name


def normalize_user_directory(path: str) -> Path:
    raw = str(path).strip().strip('"').strip("'")
    if not raw:
        raise ValueError("Save directory is empty.")
    windows_drive = re.match(r"^([A-Za-z]):[\\/](.*)$", raw)
    if windows_drive:
        drive = windows_drive.group(1).lower()
        rest = windows_drive.group(2).replace("\\", "/")
        return Path(f"/mnt/{drive}/{rest}")
    return Path(raw).expanduser()


def default_export_dir_state_file() -> Path:
    return Path.home() / ".latextrans" / "export_dir"


def persist_export_directory(directory: str, state_file: Optional[str] = None) -> str:
    path = Path(state_file) if state_file else default_export_dir_state_file()
    normalized = str(normalize_user_directory(directory))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(normalized + "\n", encoding="utf-8")
    return normalized


def load_persisted_export_directory(state_file: Optional[str] = None) -> Optional[str]:
    path = Path(state_file) if state_file else default_export_dir_state_file()
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return None
    return str(normalize_user_directory(text))


def parse_picked_directory(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    text = str(raw).strip()
    if not text:
        return None
    last_line = text.splitlines()[-1].strip().strip('"').strip("'")
    if not last_line:
        return None
    return str(normalize_user_directory(last_line))


def decode_picker_bytes(raw: Optional[bytes]) -> Optional[str]:
    if not raw:
        return None
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        return raw.decode("utf-16")
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("gbk")


def load_directory_from_picker_output(raw: Optional[bytes]) -> Optional[str]:
    text = decode_picker_bytes(raw)
    parsed = parse_picked_directory(text)
    if not parsed:
        return None
    sidecar = Path(parsed)
    if sidecar.is_file():
        content = sidecar.read_text(encoding="utf-8").strip()
        if content:
            return str(normalize_user_directory(content))
    return parsed


def _run_folder_picker() -> Optional[bytes]:
    powershell = shutil.which("powershell.exe") or shutil.which("pwsh.exe")
    if powershell:
        script = (
            "Add-Type -AssemblyName System.Windows.Forms;"
            "$form = New-Object System.Windows.Forms.Form;"
            "$form.TopMost = $true;"
            "$dialog = New-Object System.Windows.Forms.FolderBrowserDialog;"
            "$dialog.Description = '选择保存目录';"
            "$dialog.ShowNewFolderButton = $true;"
            "$result = $dialog.ShowDialog($form);"
            "$form.Dispose();"
            "if ($result -eq [System.Windows.Forms.DialogResult]::OK) {"
            "  $out = Join-Path $env:TEMP 'latextrans_picked_dir.txt';"
            "  $utf8 = New-Object System.Text.UTF8Encoding $false;"
            "  [System.IO.File]::WriteAllText($out, $dialog.SelectedPath, $utf8);"
            "  [Console]::Out.WriteLine($out)"
            "}"
        )
        completed = subprocess.run(
            [powershell, "-NoProfile", "-STA", "-NonInteractive", "-Command", script],
            capture_output=True,
            timeout=600,
        )
        return completed.stdout
    zenity = shutil.which("zenity")
    if zenity:
        completed = subprocess.run(
            [zenity, "--file-selection", "--directory", "--title=选择保存目录"],
            capture_output=True,
            timeout=600,
        )
        if completed.returncode == 0:
            return completed.stdout
    return None


def pick_save_directory(runner: Optional[Callable[[], Optional[bytes]]] = None) -> Optional[str]:
    raw = (runner or _run_folder_picker)()
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    return load_directory_from_picker_output(raw)


def resolve_save_path(directory: str, filename: str) -> Path:
    dest_dir = normalize_user_directory(directory)
    return dest_dir / sanitize_download_filename(filename)


def copy_pdf_to_path(source: str, directory: str, filename: str) -> Path:
    dest = resolve_save_path(directory, filename)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, dest)
    return dest
