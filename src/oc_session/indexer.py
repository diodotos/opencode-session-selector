from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from dataclasses import replace
from pathlib import Path
from typing import Iterable

from .models import Config, SessionData, SessionRow
from .utils import cap_text, now_ms, single_line


def ensure_index(config: Config, force: bool = False) -> None:
    if force or not config.index_path.exists() or _is_stale(config):
        build_index(config)


def build_index(config: Config) -> None:
    sources: list[dict[str, SessionData]] = []
    sources.append(_load_local_sessions(config.db_path, config.max_chars, "local"))
    for root in config.extra_data_roots:
        db_path = root / "opencode.db"
        label = _source_for_data_root(root)
        session_origin = _load_session_origin(root)
        sources.append(
            _load_local_sessions(
                db_path,
                config.max_chars,
                label,
                session_origin=session_origin,
            )
        )
    merged = _merge_sources(sources)

    config.index_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.index_path)
    try:
        _init_index(conn)
        conn.execute("DELETE FROM session")
        conn.execute("DELETE FROM session_fts")
        for session in merged.values():
            conn.execute(
                """
                INSERT OR REPLACE INTO session (
                    session_id, title, directory, created_at, updated_at,
                    source, message_count, last_preview, content
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session.session_id,
                    session.title,
                    session.directory,
                    session.created_at,
                    session.updated_at,
                    session.source,
                    session.message_count,
                    session.last_preview,
                    session.content,
                ),
            )
            conn.execute(
                "INSERT INTO session_fts (session_id, title, content) VALUES (?, ?, ?)",
                (session.session_id, session.title, session.content),
            )

        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
            ("built_at", str(now_ms())),
        )
        conn.commit()
    finally:
        conn.close()


def query_index(config: Config, query: str, limit: int) -> list[SessionRow]:
    conn = sqlite3.connect(config.index_path)
    conn.row_factory = sqlite3.Row
    try:
        if not query.strip():
            rows = conn.execute(
                "SELECT * FROM session ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [_row_to_session(row) for row in rows]

        fts_query = _build_fts_query(query)
        if not fts_query:
            rows = conn.execute(
                "SELECT * FROM session ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [_row_to_session(row) for row in rows]
        try:
            rows = conn.execute(
                """
                SELECT s.*, bm25(session_fts) AS rank
                FROM session_fts
                JOIN session s ON s.session_id = session_fts.session_id
                WHERE session_fts MATCH ?
                LIMIT ?
                """,
                (fts_query, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            rows = _fallback_search(conn, query, limit)
            return [_row_to_session(row) for row in rows]

        ranked = []
        now = now_ms()
        for row in rows:
            updated_at = row["updated_at"] or row["created_at"] or 0
            recency = _recency_boost(now, updated_at)
            score = -float(row["rank"]) + recency
            ranked.append((score, row))
        ranked.sort(key=lambda item: item[0], reverse=True)
        return [_row_to_session(row) for _, row in ranked]
    finally:
        conn.close()


def get_session(config: Config, session_id: str) -> SessionRow | None:
    conn = sqlite3.connect(config.index_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM session WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        return _row_to_session(row) if row else None
    finally:
        conn.close()


def _init_index(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS session (
            session_id TEXT PRIMARY KEY,
            title TEXT,
            directory TEXT,
            created_at INTEGER,
            updated_at INTEGER,
            source TEXT,
            message_count INTEGER,
            last_preview TEXT,
            content TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS session_fts
        USING fts5(session_id UNINDEXED, title, content, tokenize='unicode61')
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """
    )


def _is_stale(config: Config) -> bool:
    if not config.index_path.exists():
        return True
    conn = sqlite3.connect(config.index_path)
    try:
        row = conn.execute("SELECT value FROM meta WHERE key = 'built_at'").fetchone()
        if not row:
            return True
        built_at = int(row[0])
        age_ms = now_ms() - built_at
        return age_ms > config.ttl_seconds * 1000
    finally:
        conn.close()


def _build_fts_query(query: str) -> str:
    tokens = []
    for chunk in query.split():
        cleaned = "".join(ch for ch in chunk if ch.isalnum() or ch in {"_", "-"})
        if cleaned:
            tokens.append(cleaned + "*")
    return " ".join(tokens) if tokens else ""


def _fallback_search(
    conn: sqlite3.Connection, query: str, limit: int
) -> Iterable[sqlite3.Row]:
    rows = conn.execute("SELECT * FROM session").fetchall()
    lowered = query.lower()
    matches = []
    for row in rows:
        text = (row["title"] or "") + " " + (row["content"] or "")
        if lowered in text.lower():
            matches.append(row)
    matches.sort(key=lambda row: row["updated_at"] or 0, reverse=True)
    return matches[:limit]


def _recency_boost(now: int, updated_at: int) -> float:
    if not updated_at:
        return 0.0
    days_ago = max((now - updated_at) / 86_400_000, 0.0)
    return 1.0 / (1.0 + days_ago)


def _row_to_session(row: sqlite3.Row) -> SessionRow:
    return SessionRow(
        session_id=row["session_id"],
        title=row["title"] or "",
        directory=row["directory"] or "",
        created_at=row["created_at"] or 0,
        updated_at=row["updated_at"] or 0,
        source=row["source"] or "",
        message_count=row["message_count"] or 0,
        last_preview=row["last_preview"] or "",
        content=row["content"] or "",
    )


def _merge_sources(sources: list[dict[str, SessionData]]) -> dict[str, SessionData]:
    merged: dict[str, SessionData] = {}
    for source in sources:
        for session_id, data in source.items():
            if session_id not in merged:
                merged[session_id] = data
                continue
            existing = merged[session_id]
            if (data.updated_at or data.created_at) > (existing.updated_at or existing.created_at):
                base = data
                other = existing
            else:
                base = existing
                other = data

            if not base.content and other.content:
                base = replace(base, content=other.content)
            if not base.last_preview and other.last_preview:
                base = replace(base, last_preview=other.last_preview)
            if not base.title and other.title:
                base = replace(base, title=other.title)
            if not base.directory and other.directory:
                base = replace(base, directory=other.directory)
            merged[session_id] = base
    return merged


def _load_local_sessions(
    db_path: Path,
    max_chars: int,
    source: str,
    session_origin: dict[str, tuple[str, str]] | None = None,
) -> dict[str, SessionData]:
    if not db_path.exists() or _is_lfs_pointer(db_path):
        return {}
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        session_rows = conn.execute(
            "SELECT id, title, directory, time_created, time_updated FROM session"
        ).fetchall()
        sessions: dict[str, SessionData] = {}
        session_origin = session_origin or {}
        for row in session_rows:
            session_id = row["id"]
            session_source = _session_source(session_id, source, session_origin)
            sessions[session_id] = SessionData(
                session_id=session_id,
                title=row["title"] or "",
                directory=row["directory"] or "",
                created_at=row["time_created"] or 0,
                updated_at=row["time_updated"] or 0,
                source=session_source,
                message_count=0,
                last_preview="",
                content="",
            )

        message_counts = conn.execute(
            "SELECT session_id, COUNT(*) AS count FROM message GROUP BY session_id"
        ).fetchall()
        for row in message_counts:
            session_id = row["session_id"]
            if session_id in sessions:
                sessions[session_id].message_count = row["count"]

        text_parts: dict[str, list[tuple[int, str]]] = defaultdict(list)
        part_rows = conn.execute(
            "SELECT session_id, time_created, data FROM part"
        )
        for row in part_rows:
            data = _safe_json(row["data"])
            if not data or data.get("type") != "text":
                continue
            text = data.get("text") or ""
            if not text:
                continue
            text_parts[row["session_id"]].append((row["time_created"] or 0, text))

        for session_id, entries in text_parts.items():
            if session_id not in sessions:
                continue
            entries.sort(key=lambda item: item[0])
            texts = [text for _, text in entries]
            content = cap_text("\n".join(texts), max_chars)
            last_preview = single_line(texts[-1], 220) if texts else ""
            sessions[session_id].content = content
            sessions[session_id].last_preview = last_preview

        return sessions
    finally:
        conn.close()


def _is_lfs_pointer(db_path: Path) -> bool:
    try:
        with db_path.open("rb") as handle:
            header = handle.read(64)
    except OSError:
        return True
    return header.startswith(b"version https://git-lfs.github.com/spec/v1")


def _source_for_data_root(root: Path) -> str:
    meta_path = root / "db-meta.json"
    hostname = _read_hostname(meta_path)
    if hostname:
        return f"sync:{hostname}"
    return "sync:unknown"


def _load_session_origin(root: Path) -> dict[str, tuple[str, str]]:
    origin_path = root / "session-origin.json"
    if not origin_path.exists():
        return {}
    try:
        data = _safe_json(origin_path.read_text(encoding="utf-8"))
    except OSError:
        return {}
    if not isinstance(data, dict):
        return {}
    sessions = data.get("sessions")
    if not isinstance(sessions, dict):
        return {}

    fallback_host = data.get("host") if isinstance(data.get("host"), str) else ""
    fallback_platform = (
        data.get("platform") if isinstance(data.get("platform"), str) else ""
    )

    origins: dict[str, tuple[str, str]] = {}
    for session_id, meta in sessions.items():
        if not isinstance(session_id, str) or not isinstance(meta, dict):
            continue
        host = meta.get("host") if isinstance(meta.get("host"), str) else ""
        platform = (
            meta.get("platform") if isinstance(meta.get("platform"), str) else ""
        )
        if not host:
            host = fallback_host
        if not platform:
            platform = fallback_platform
        host = host.strip()
        platform = platform.strip()
        if host:
            origins[session_id] = (host, platform)
    return origins


def _session_source(
    session_id: str,
    fallback: str,
    session_origin: dict[str, tuple[str, str]],
) -> str:
    origin = session_origin.get(session_id)
    if not origin:
        return fallback
    host, platform = origin
    if not host:
        return fallback
    if platform:
        return f"sync:{host}/{platform}"
    return f"sync:{host}"


def _read_hostname(meta_path: Path) -> str | None:
    if not meta_path.exists():
        return None
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    hostname = data.get("hostname")
    if isinstance(hostname, str) and hostname.strip():
        return hostname.strip()
    return None


def _safe_json(raw: str) -> dict | None:
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
