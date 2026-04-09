from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Literal

from .models import Config, SessionRow
from .utils import is_remote_source


Action = Literal["open", "fork", "cancel"]


def open_session(session: SessionRow, config: Config) -> int:
    opencode_cmd = shutil.which("opencode")
    if not opencode_cmd:
        raise RuntimeError("opencode not found in PATH")

    cwd = Path.cwd()
    directory = Path(session.directory).expanduser() if session.directory else None
    original_exists = directory.exists() if directory else False
    is_remote = is_remote_source(session.source)

    if not is_remote and original_exists and directory and _same_path(directory, cwd):
        return _run_opencode(opencode_cmd, directory, session.session_id, fork=False)

    if original_exists and directory:
        if is_remote:
            choice = _choose_action(
                title="Remote session detected",
                options=[
                    ("f", "Fork into current directory (default)", "fork"),
                    ("o", "Open original context", "open"),
                    ("c", "Cancel", "cancel"),
                ],
                default="fork",
            )
            if choice == "fork":
                return _run_opencode(opencode_cmd, cwd, session.session_id, fork=True)
            if choice == "open":
                return _run_opencode(opencode_cmd, directory, session.session_id, fork=False)
            return 1

        choice = _choose_action(
            title="Open session context",
            options=[
                ("o", "Open original context (default)", "open"),
                ("f", "Fork into current directory", "fork"),
                ("c", "Cancel", "cancel"),
            ],
            default="open",
        )
        if choice == "fork":
            return _run_opencode(opencode_cmd, cwd, session.session_id, fork=True)
        if choice == "open":
            return _run_opencode(opencode_cmd, directory, session.session_id, fork=False)
        return 1

    if is_remote:
        choice = _choose_action(
            title="Remote session: original context not found",
            options=[
                ("f", "Fork into current directory (default)", "fork"),
                ("c", "Cancel", "cancel"),
            ],
            default="fork",
        )
    else:
        choice = _choose_action(
            title="Original context not found",
            options=[
                ("f", "Fork into current directory", "fork"),
                ("c", "Cancel (default)", "cancel"),
            ],
            default="cancel",
        )
    if choice == "fork":
        return _run_opencode(opencode_cmd, cwd, session.session_id, fork=True)
    return 1


def _run_opencode(cmd: str, directory: Path, session_id: str, fork: bool) -> int:
    args = [cmd, str(directory), "--session", session_id]
    if fork:
        args.append("--fork")
    return subprocess.run(args, check=False).returncode


def _choose_action(title: str, options: list[tuple[str, str, Action]], default: Action) -> Action:
    if _tmux_available():
        choice = _tmux_menu(title, options)
        if choice:
            return choice
    return _basic_prompt(title, options, default)


def _tmux_available() -> bool:
    return bool(os.getenv("TMUX")) and shutil.which("tmux") is not None


def _tmux_menu(title: str, options: list[tuple[str, str, Action]]) -> Action | None:
    with tempfile.NamedTemporaryFile(prefix="oc-session-", delete=False) as temp:
        temp_path = temp.name

    cmd = ["tmux", "display-menu", "-T", title]
    for key, label, action in options:
        command = f"run-shell 'printf {action} > {temp_path}'"
        cmd.extend([label, key, command])

    subprocess.run(cmd, check=False)
    choice = _wait_for_choice(temp_path)
    try:
        Path(temp_path).unlink(missing_ok=True)
    except Exception:
        pass
    return choice


def _wait_for_choice(path: str, timeout: float = 60.0) -> Action | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            content = Path(path).read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            content = ""
        if content in {"open", "fork", "cancel"}:
            return content  # type: ignore[return-value]
        time.sleep(0.1)
    return None


def _basic_prompt(
    title: str, options: list[tuple[str, str, Action]], default: Action
) -> Action:
    print(f"{title}:")
    for key, label, _ in options:
        print(f"  [{key}] {label}")
    prompt = f"Choice (default {default}): "
    raw = input(prompt).strip().lower()
    if not raw:
        return default
    for key, _, action in options:
        if raw == key:
            return action
    return default


def _same_path(left: Path, right: Path) -> bool:
    try:
        return left.resolve() == right.resolve()
    except Exception:
        return str(left) == str(right)
