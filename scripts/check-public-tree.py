#!/usr/bin/env python3
"""Reject private files, absolute home paths, and obvious credentials in a public tree."""
import argparse
import json
import os
from pathlib import Path
import re
import sys

SKIP_DIRS = {".git", "__pycache__", ".venv", "venv", "dist", "build"}
PRIVATE_SUFFIXES = (".local.json", ".local.jsonl", ".local.log", ".db", ".sqlite", ".sqlite3", ".pem", ".key", ".pem", ".sig")
PRIVATE_NAMES = {"auth.json", ".env", "config.toml", "routed-models.json", "runtime-compatibility.local.json"}
ABSOLUTE_HOME = re.compile(r"/home/[A-Za-z0-9._-]+/")
SECRET_ASSIGNMENT = re.compile(r"(?i)\b(api[_-]?key|bearer(?:_token)?|experimental_bearer_token)\b\s*[=:]\s*[\"']([^\"']+)[\"']")
SAFE_MARKERS = ("<", "your_", "example", "placeholder", "fixture", "local-test", "isolated-secret", "replace_")


def iter_files(root):
    for current, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for name in sorted(files):
            yield Path(current) / name


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", default=".", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    problems = []
    for path in iter_files(root):
        rel = path.relative_to(root)
        if path.is_symlink():
            problems.append({"file": str(rel), "kind": "symlink"})
            continue
        if path.name in PRIVATE_NAMES or path.name.endswith(PRIVATE_SUFFIXES):
            problems.append({"file": str(rel), "kind": "private_filename"})
        try:
            if path.stat().st_size > 2 * 1024 * 1024:
                continue
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if ABSOLUTE_HOME.search(text):
            problems.append({"file": str(rel), "kind": "absolute_home_path"})
        for match in SECRET_ASSIGNMENT.finditer(text):
            value = match.group(2)
            if not any(marker in value.lower() for marker in SAFE_MARKERS):
                problems.append({"file": str(rel), "kind": "possible_secret"})
                break
    print(json.dumps({"status": "BLOCKED" if problems else "CLEAN", "root": str(root),
                      "problems": problems}, ensure_ascii=False, indent=2))
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
