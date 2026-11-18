"""Validate single-line Conventional Commit messages."""

from __future__ import annotations

from pathlib import Path
import re
import sys

PATTERN = re.compile(r"(?:feat|fix|docs|test|refactor|build|chore|ci|perf|style)(?:\([a-z0-9-]+\))?!?: .+\Z")


def validate(message: str) -> None:
    if len(message.splitlines()) != 1 or message.endswith(("\n", "\r")):
        raise ValueError("Commit messages must be a single line")
    if len(message) > 100:
        raise ValueError("Commit messages must be 100 characters or fewer")
    if not PATTERN.fullmatch(message):
        raise ValueError("Use a single-line Conventional Commit: feat(scope): description")


def main() -> int:
    if len(sys.argv) == 3 and sys.argv[1] == "--subject":
        raw = sys.argv[2]
    elif len(sys.argv) == 2:
        raw = Path(sys.argv[1]).read_text(encoding="utf-8").removesuffix("\n")
    else:
        print("Usage: lint_commit.py <commit-message-file> | --subject <subject>", file=sys.stderr)
        return 2
    try:
        validate(raw)
    except (ValueError, OSError) as exc:
        print(f"Commit lint failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
