"""Update the git.repo_url field from stdin without logging the credential."""

from __future__ import annotations

import os
import re
import sys
import tempfile
import errno


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else "/app/config.yaml"
    url = sys.stdin.readline().rstrip("\n")
    if not url or "\n" in url or "\r" in url:
        raise SystemExit("missing or invalid remote URL")

    with open(path, encoding="utf-8") as handle:
        lines = handle.readlines()

    output: list[str] = []
    in_git = False
    changed = False
    for line in lines:
        if re.match(r"^git:\s*$", line):
            in_git = True
        elif in_git and line.strip() and not line.startswith((" ", "\t")):
            in_git = False

        if in_git and re.match(r"^\s+repo_url:", line):
            escaped = url.replace("\\", "\\\\").replace('"', '\\"')
            output.append(f'  repo_url: "{escaped}"\n')
            changed = True
        else:
            output.append(line)

    if not changed:
        raise SystemExit("git.repo_url not found")

    directory = os.path.dirname(path) or "."
    fd, temporary = tempfile.mkstemp(prefix=".config.", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.writelines(output)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.replace(temporary, path)
        except OSError as error:
            # A single-file Docker bind mount rejects rename(2) with EBUSY.
            # The caller must have made a backup first; then write the
            # already-rendered content in place and fsync it.
            if error.errno != errno.EBUSY:
                raise
            with open(path, "w", encoding="utf-8") as handle:
                handle.writelines(output)
                handle.flush()
                os.fsync(handle.fileno())
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)

    print("GIT_REMOTE_UPDATED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
