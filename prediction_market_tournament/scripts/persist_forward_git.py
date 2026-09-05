#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from tournament.freeze import require_forward_started


def _git(repo_root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo_root), *args],
        check=check,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    repo_root = root.parent
    marker = require_forward_started(root)

    branch = _git(repo_root, "branch", "--show-current").stdout.strip()
    if branch != "prediction-market-tournament":
        raise SystemExit(
            f"refusing data persistence from unexpected branch: {branch!r}"
        )

    pre_staged = [
        line
        for line in _git(
            repo_root, "diff", "--cached", "--name-only"
        ).stdout.splitlines()
        if line.strip()
    ]
    if pre_staged:
        raise SystemExit(
            "refusing to touch Git index with pre-existing staged files: "
            + ", ".join(pre_staged)
        )

    data_path = "prediction_market_tournament/data"
    add = _git(
        repo_root,
        "add",
        "-A",
        "--",
        data_path,
        f":(exclude){data_path}/forward_service.lock",
        f":(exclude){data_path}/*.tmp",
        check=False,
    )
    if add.returncode != 0:
        raise SystemExit(add.stderr.strip() or "git add failed")

    staged = [
        line
        for line in _git(
            repo_root, "diff", "--cached", "--name-only"
        ).stdout.splitlines()
        if line.strip()
    ]
    if not staged:
        print(json.dumps({"changed": False, "pushed": False}))
        return 0

    invalid = [
        path
        for path in staged
        if not path.startswith(data_path + "/")
        or path.endswith("/forward_service.lock")
        or path.endswith(".tmp")
    ]
    if invalid:
        _git(repo_root, "reset", "--", data_path, check=False)
        raise SystemExit(
            "refusing non-forward-data staged paths: " + ", ".join(invalid)
        )

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    message = (
        f"PMT forward data {timestamp} "
        f"spec={str(marker['spec_sha256'])[:12]} "
        f"impl={str(marker['implementation_sha256'])[:12]}"
    )
    commit = _git(repo_root, "commit", "-m", message, check=False)
    if commit.returncode != 0:
        raise SystemExit(commit.stderr.strip() or commit.stdout.strip())

    push = _git(
        repo_root,
        "push",
        "origin",
        "HEAD:prediction-market-tournament",
        check=False,
    )
    result = {
        "changed": True,
        "committed": True,
        "pushed": push.returncode == 0,
        "paths": staged,
        "push_stderr": push.stderr[-2000:],
    }
    print(json.dumps(result, sort_keys=True))
    return 0 if push.returncode == 0 else 4


if __name__ == "__main__":
    raise SystemExit(main())
