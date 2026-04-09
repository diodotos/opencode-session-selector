from __future__ import annotations

import argparse
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from .indexer import build_index, ensure_index, get_session, query_index
from .models import Config
from .opener import open_session
from .utils import abbreviate_path, format_timestamp, is_remote_source, read_env_int, single_line


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    config = _load_config(args)

    command = args.command or "search"

    if command == "index":
        build_index(config)
        print("Index rebuilt.")
        return 0

    if command == "list":
        ensure_index(config, force=args.rebuild)
        rows = query_index(config, args.query or "", args.limit)
        _print_rows(rows)
        return 0

    if command == "open":
        ensure_index(config, force=args.rebuild)
        session = get_session(config, args.session_id)
        if not session:
            print(f"Session not found: {args.session_id}")
            return 1
        return open_session(session, config)

    if command == "inspect":
        ensure_index(config, force=args.rebuild)
        session = get_session(config, args.session_id)
        if not session:
            print(f"Session not found: {args.session_id}")
            return 1
        _print_session(session, "")
        return 0

    if command == "_query":
        ensure_index(config, force=args.rebuild)
        rows = query_index(config, args.query or "", args.limit)
        _print_rows(rows)
        return 0

    if command == "_preview":
        ensure_index(config, force=args.rebuild)
        session = get_session(config, args.session_id)
        if not session:
            print("Session not found.")
            return 1
        _print_session(session, args.query)
        return 0

    return _run_fzf(config)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="oc-session")
    parser.add_argument("--db-path", default=os.getenv("OC_SESSION_DB_PATH"))
    parser.add_argument("--index-path", default=os.getenv("OC_SESSION_INDEX_PATH"))
    parser.add_argument("--max-chars", type=int, default=read_env_int("OC_SESSION_MAX_CHARS", 200000))
    parser.add_argument("--ttl-seconds", type=int, default=read_env_int("OC_SESSION_TTL_SECONDS", 300))
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--rebuild", action="store_true", help="Force index rebuild")

    subparsers = parser.add_subparsers(dest="command")

    list_parser = subparsers.add_parser("list", help="List sessions")
    list_parser.add_argument("--query", default="")

    open_parser = subparsers.add_parser("open", help="Open a session by ID")
    open_parser.add_argument("session_id")

    inspect_parser = subparsers.add_parser("inspect", help="Inspect a session")
    inspect_parser.add_argument("session_id")

    index_parser = subparsers.add_parser("index", help="Rebuild the index")
    index_parser.add_argument("--rebuild", action="store_true")

    query_parser = subparsers.add_parser("_query")
    query_parser.add_argument("--query", default="")

    preview_parser = subparsers.add_parser("_preview")
    preview_parser.add_argument("--session", dest="session_id", required=True)
    preview_parser.add_argument("--query", default="")

    return parser.parse_args(argv)


def _load_config(args: argparse.Namespace) -> Config:
    db_path = Path(args.db_path or "~/.local/share/opencode/opencode.db").expanduser()
    index_path = Path(
        args.index_path or "~/.local/state/oc-session/index.sqlite"
    ).expanduser()
    extra_data_roots = _resolve_extra_data_roots(db_path)
    return Config(
        db_path=db_path,
        index_path=index_path,
        max_chars=args.max_chars,
        ttl_seconds=args.ttl_seconds,
        limit=args.limit,
        extra_data_roots=extra_data_roots,
    )


def _resolve_extra_data_roots(db_path: Path) -> list[Path]:
    extra_roots: list[Path] = []
    raw = os.getenv("OC_SESSION_EXTRA_DATA_ROOTS", "")
    if raw:
        for part in raw.split(os.pathsep):
            if not part.strip():
                continue
            extra_roots.append(Path(part.strip()).expanduser())

    sync_config_root = os.getenv("SYNC_CONFIG_ROOT")
    if sync_config_root:
        candidate = Path(sync_config_root).expanduser() / "_data"
        extra_roots.append(candidate)
    else:
        default_candidate = Path("~/.config/opencode/_data").expanduser()
        extra_roots.append(default_candidate)

    unique: list[Path] = []
    seen = set()
    for root in extra_roots:
        if root == db_path.parent:
            continue
        key = str(root.resolve()) if root.exists() else str(root)
        if key in seen:
            continue
        seen.add(key)
        unique.append(root)
    return unique


def _run_fzf(config: Config) -> int:
    if not shutil.which("fzf"):
        print("fzf is required but not found in PATH.")
        return 1

    ensure_index(config)
    oc_session_cmd = shutil.which("oc-session") or sys.argv[0]

    env = os.environ.copy()
    env["OC_SESSION_DB_PATH"] = str(config.db_path)
    env["OC_SESSION_INDEX_PATH"] = str(config.index_path)
    env["OC_SESSION_MAX_CHARS"] = str(config.max_chars)
    env["OC_SESSION_TTL_SECONDS"] = str(config.ttl_seconds)

    base_cmd = (
        f"{shlex.quote(oc_session_cmd)} --limit {config.limit} _query --query \"{{q}}\""
    )
    preview_cmd = f"{shlex.quote(oc_session_cmd)} _preview --session {{1}} --query \"{{q}}\""

    fzf_cmd = [
        "fzf",
        "--ansi",
        "--phony",
        "--delimiter", "\t",
        "--with-nth", "2,3,4,5",
        "--prompt", "session> ",
        "--bind", f"start:reload:{base_cmd}",
        "--bind", f"change:reload:{base_cmd}",
        "--preview", preview_cmd,
        "--preview-window", "down:60%:wrap",
    ]

    proc = subprocess.run(
        fzf_cmd,
        input="",
        text=True,
        capture_output=True,
        env=env,
    )

    selected = proc.stdout.strip()
    if not selected:
        return proc.returncode
    session_id = selected.split("\t", 1)[0]
    session = get_session(config, session_id)
    if not session:
        print(f"Session not found: {session_id}")
        return 1
    return open_session(session, config)


def _print_rows(rows) -> None:
    cwd = Path.cwd()
    for row in rows:
        ts = format_timestamp(row.updated_at or row.created_at)
        relpath = abbreviate_path(row.directory, cwd)
        title = single_line(row.title or row.last_preview or "(no title)", 200)
        ts_col = _fit_column(ts, 16)
        source_col = _format_source_tag(row.source, 10)
        path_col = _fit_column(relpath, 44, tail=True)
        line = f"{row.session_id}\t{ts_col} | \t{source_col} | \t{path_col} | \t{title}"
        print(line)


def _print_session(session, query: str) -> None:
    reset = "\x1b[0m"
    bold = "\x1b[1m"
    dim = "\x1b[2m"
    cyan = "\x1b[36m"
    yellow = "\x1b[33m"

    print(f"{bold}{cyan}Session{reset}: {session.session_id}")
    print(f"{bold}Title{reset}: {session.title or '-'}")
    print(f"{bold}Directory{reset}: {session.directory or '-'}")
    print(f"{bold}Created{reset}: {format_timestamp(session.created_at)}")
    print(f"{bold}Updated{reset}: {format_timestamp(session.updated_at)}")
    source_label, source_host, source_platform = _parse_source(session.source)
    print(f"{bold}Source{reset}: {session.source or '-'}")
    if source_host:
        print(f"{bold}Origin host{reset}: {source_host}")
    if source_platform:
        print(f"{bold}Origin platform{reset}: {source_platform}")
    if is_remote_source(session.source):
        print(f"{yellow}Origin: remote session (safety prompts enabled){reset}")
        if session.directory:
            try:
                if Path(session.directory).expanduser().exists():
                    print(
                        f"{yellow}Warning: path exists locally but session is remote{reset}"
                    )
            except OSError:
                pass
    print(f"{bold}Message count{reset}: {session.message_count}")

    content = session.content or ""
    if not content and session.last_preview:
        content = session.last_preview

    if not content:
        print(f"{dim}No content available for preview.{reset}")
        return

    tokens = _tokens(query)
    excerpt = _extract_snippets(content, tokens, max_chars=12000)
    excerpt = _highlight(excerpt, tokens)
    print(f"{bold}{cyan}Content (excerpt){reset}:")
    print(excerpt)


def _tokens(query: str) -> list[str]:
    if not query:
        return []
    raw_tokens = [tok.strip() for tok in re.split(r"\s+", query) if tok.strip()]
    tokens: list[str] = []
    seen = set()
    for token in raw_tokens:
        cleaned = "".join(ch for ch in token if ch.isalnum() or ch in {"_", "-"})
        if len(cleaned) < 2:
            continue
        key = cleaned.lower()
        if key not in seen:
            tokens.append(cleaned)
            seen.add(key)
    return tokens[:8]


def _extract_snippets(
    content: str, tokens: list[str], max_chars: int
) -> str:
    if not tokens:
        return content[:max_chars]

    lower = content.lower()
    positions: list[int] = []
    for token in tokens:
        idx = lower.find(token.lower())
        if idx != -1:
            positions.append(idx)

    if not positions:
        return content[:max_chars]

    positions = sorted(set(positions))[:3]
    window = 900
    intervals: list[tuple[int, int]] = []
    for pos in positions:
        start = max(0, pos - window)
        end = min(len(content), pos + window)
        intervals.append((start, end))

    intervals.sort()
    merged: list[tuple[int, int]] = []
    for start, end in intervals:
        if not merged:
            merged.append((start, end))
            continue
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))

    snippets = [content[start:end].strip() for start, end in merged]
    result = "\n...\n".join(snippets)
    return result[:max_chars]


def _highlight(text: str, tokens: list[str]) -> str:
    if not tokens:
        return text
    green = "\x1b[32m"
    reset = "\x1b[0m"
    ordered = sorted(tokens, key=len, reverse=True)
    for token in ordered:
        pattern = re.compile(re.escape(token), re.IGNORECASE)
        text = pattern.sub(lambda match: f"{green}{match.group(0)}{reset}", text)
    return text


def _format_source_tag(source: str, width: int) -> str:
    reset = "\x1b[0m"
    green = "\x1b[32m"
    yellow = "\x1b[33m"
    blue = "\x1b[34m"

    label = _source_label(source)
    padded = _fit_column(label, width)
    if label == "local":
        return f"{green}{padded}{reset}"
    if label == "synced":
        return f"{blue}{padded}{reset}"
    return f"{yellow}{padded}{reset}"


def _source_label(source: str) -> str:
    label, host, platform = _parse_source(source)
    if label == "sync" and host:
        if platform:
            return f"sync:{host}/{platform}"
        return f"sync:{host}"
    return label


def _parse_source(source: str) -> tuple[str, str, str]:
    if source == "local":
        return "local", "", ""
    if source == "synced":
        return "synced", "", ""
    if source.startswith("sync:"):
        remainder = source.split("sync:", 1)[1]
        if "/" in remainder:
            host, platform = remainder.split("/", 1)
            return "sync", host, platform
        return "sync", remainder, ""
    if source.startswith("github-sync:"):
        return "sync", "", ""
    return "remote", "", ""


def _fit_column(value: str, width: int, tail: bool = False) -> str:
    if width <= 0:
        return ""
    if len(value) <= width:
        return value.ljust(width)
    if width <= 3:
        return value[:width]
    if tail:
        return "..." + value[-(width - 3) :]
    return value[: width - 3] + "..."


if __name__ == "__main__":
    raise SystemExit(main())
