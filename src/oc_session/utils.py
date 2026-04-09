from __future__ import annotations

import os
import time
from datetime import datetime
from pathlib import Path


def now_ms() -> int:
    return int(time.time() * 1000)


def format_timestamp(ms: int) -> str:
    if not ms:
        return "-"
    dt = datetime.fromtimestamp(ms / 1000)
    return dt.strftime("%Y-%m-%d %H:%M")


def single_line(text: str, max_len: int = 160) -> str:
    if not text:
        return ""
    cleaned = " ".join(text.split())
    if len(cleaned) <= max_len:
        return cleaned
    return cleaned[: max_len - 3] + "..."


def cap_text(text: str, max_chars: int) -> str:
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    separator = "\n...\n"
    if max_chars <= len(separator):
        return text[:max_chars]
    head_len = (max_chars - len(separator)) // 2
    tail_len = max_chars - len(separator) - head_len
    head = text[:head_len]
    tail = text[-tail_len:]
    return head + separator + tail


def abbreviate_path(path_str: str, cwd: Path) -> str:
    if not path_str:
        return "-"
    try:
        path = Path(path_str).expanduser().resolve()
    except Exception:
        return path_str

    try:
        cwd_path = cwd.resolve()
        rel = path.relative_to(cwd_path)
        if str(rel) == ".":
            return "."
        return "./" + str(rel)
    except Exception:
        pass

    home = Path.home()
    try:
        rel_home = path.relative_to(home)
        return "~/" + str(rel_home)
    except Exception:
        pass

    parts = path.parts
    if len(parts) > 3:
        return f"{parts[0]}/.../{parts[-1]}"
    return str(path)


def ensure_parent_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def read_env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def is_remote_source(source: str) -> bool:
    return source != "local"
