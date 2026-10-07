set shell := ["bash", "-cu"]
export PYTHONPATH := "src"
export UV_PROJECT_ENVIRONMENT := env_var("HOME") + "/.cache/uv-venvs/flighty-sync"

default:
    @just --list

# Execute the job with the caller's environment
run:
    uv run flighty-sync inspect

alias dev := run

test:
    uv run pytest

# All static analysis (read-only, CI-safe)
check:
    uv run ruff check . && uv run ruff format --check .

fmt:
    uv run ruff format . && uv run ruff check --fix .

# Tail the launchd logs (on the mini)
logs:
    tail -F "$HOME/Library/Application Support/FlightySync/launchd.log" "$HOME/Library/Application Support/FlightySync/launchd.err.log"

# --- project-specific recipes below (one-offs live in scripts/, run directly) ---
