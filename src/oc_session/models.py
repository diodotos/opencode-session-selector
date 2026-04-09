from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class Config:
    db_path: Path
    index_path: Path
    max_chars: int
    ttl_seconds: int
    limit: int
    extra_data_roots: list[Path]


@dataclass
class SessionRow:
    session_id: str
    title: str
    directory: str
    created_at: int
    updated_at: int
    source: str
    message_count: int
    last_preview: str
    content: str = ""


@dataclass
class SessionData:
    session_id: str
    title: str
    directory: str
    created_at: int
    updated_at: int
    source: str
    message_count: int
    last_preview: str
    content: str
