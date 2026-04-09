# oc-session

CLI to explore and reopen OpenCode sessions with an fzf picker and tmux menus.

## Requirements

- `opencode` in PATH
- `fzf` in PATH (required)
- `tmux` (menus are shown inside tmux; falls back to a basic prompt if not in tmux)

## Install (uv)

```sh
cd ~/programs/oc-session
uv run oc-session --help
```

To install the CLI into your PATH (editable, recommended for local dev):

```sh
cd ~/programs/oc-session
uv tool install --editable .
uv tool update-shell
```

If `oc-session` is still not on PATH, add the uv tool bin dir to your shell:

```sh
export PATH="$(uv tool dir --bin):$PATH"
```

## Usage

```sh
# Interactive picker (default)
oc-session

# List sessions (non-interactive)
oc-session list

# Open a specific session
oc-session open ses_abc123...

# Rebuild index
oc-session index --rebuild

```

## Behavior

- Opens in the original session directory by default.
- If the original directory exists but differs from your current directory, a tmux menu lets you choose:
  - Open original (default)
  - Fork into current directory
  - Cancel
- If the original directory is missing, it prompts to fork into current directory (default cancel).
- Sessions from extra data roots are treated as remote and always prompt; default is fork.
- Synced sources display the originating hostname when available.

## Paths and display

- Paths are abbreviated relative to the current working directory when possible.
- If not under the current directory, paths are shown relative to `~` when possible.

## Data sources

- Local SQLite: `~/.local/share/opencode/opencode.db`
- GitHub sync repo (opencode-github-sync): `~/.config/opencode/_data/opencode.db`

## Syncing

Works well with the opencode-github-sync tool: https://github.com/diodotos/opencode-github-sync
It stores data under `~/.config/opencode/_data` (or `$SYNC_CONFIG_ROOT/_data`).
oc-session reads that location automatically; add more with `OC_SESSION_EXTRA_DATA_ROOTS`.
If a `_data/session-origin.json` file is present, oc-session uses it to show per-session host/platform.

## Environment variables

- `OC_SESSION_DB_PATH` override local DB path
- `OC_SESSION_INDEX_PATH` override index path
- `OC_SESSION_MAX_CHARS` cap indexed text per session (default 200000)
- `OC_SESSION_TTL_SECONDS` index rebuild TTL in seconds (default 300)
- `OC_SESSION_EXTRA_DATA_ROOTS` extra data roots (pathsep-separated), each containing `opencode.db`
