#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from tournament.freeze import require_forward_started
from tournament.persistence_guard import verify_append_only_forward_data


def _git(
    repo_root: Path,
    *args: str,
    check: bool = True,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo_root), *args],
        check=check,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _push_branch(repo_root: Path) -> tuple[bool, str]:
    push = _git(
        repo_root,
        "push",
        "origin",
        "HEAD:prediction-market-tournament",
        check=False,
    )
    return push.returncode == 0, push.stderr[-2000:]


def _ahead_of_remote(repo_root: Path) -> int:
    fetch = _git(
        repo_root,
        "fetch",
        "origin",
        "prediction-market-tournament",
        check=False,
    )
    if fetch.returncode != 0:
        return -1
    count = _git(
        repo_root,
        "rev-list",
        "--count",
        "origin/prediction-market-tournament..HEAD",
        check=False,
    )
    if count.returncode != 0:
        return -1
    try:
        return int(count.stdout.strip() or "0")
    except ValueError:
        return -1


def _staged_status(repo_root: Path) -> dict[str, str]:
    rows = _git(
        repo_root,
        "diff",
        "--cached",
        "--name-status",
    ).stdout.splitlines()
    out: dict[str, str] = {}
    for row in rows:
        if not row.strip():
            continue
        fields = row.split("\t")
        if len(fields) >= 2:
            out[fields[-1]] = fields[0]
    return out


def _staged_numstat(repo_root: Path) -> dict[str, tuple[int, int]]:
    rows = _git(
        repo_root,
        "diff",
        "--cached",
        "--numstat",
    ).stdout.splitlines()
    out: dict[str, tuple[int, int]] = {}
    for row in rows:
        fields = row.split("\t")
        if len(fields) != 3:
            continue
        added, deleted, path = fields
        if added.isdigit() and deleted.isdigit():
            out[path] = (int(added), int(deleted))
    return out


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

    try:
        verify_append_only_forward_data(
            staged,
            statuses=_staged_status(repo_root),
            stats=_staged_numstat(repo_root),
        )
    except Exception:
        _git(repo_root, "reset", "--", data_path, check=False)
        raise

    committed = False
    if staged:
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        message = (
            f"PMT forward data {timestamp} "
            f"spec={str(marker['spec_sha256'])[:12]} "
            f"impl={str(marker['implementation_sha256'])[:12]}"
        )
        commit = _git(repo_root, "commit", "-m", message, check=False)
        if commit.returncode != 0:
            raise SystemExit(commit.stderr.strip() or commit.stdout.strip())
        committed = True

    ahead = _ahead_of_remote(repo_root)
    if ahead == 0:
        print(
            json.dumps(
                {
                    "changed": bool(staged),
                    "committed": committed,
                    "pushed": False,
                    "already_synced": True,
                    "paths": staged,
                },
                sort_keys=True,
            )
        )
        return 0

    pushed, push_stderr = _push_branch(repo_root)
    result = {
        "changed": bool(staged),
        "committed": committed,
        "pushed": pushed,
        "already_synced": False,
        "ahead_before_push": ahead,
        "paths": staged,
        "push_stderr": push_stderr,
    }
    print(json.dumps(result, sort_keys=True))
    return 0 if pushed else 4


if __name__ == "__main__":
    raise SystemExit(main())
