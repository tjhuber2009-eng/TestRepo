from __future__ import annotations

from collections.abc import Mapping, Sequence


def verify_append_only_forward_data(
    staged: Sequence[str],
    *,
    statuses: Mapping[str, str],
    stats: Mapping[str, tuple[int, int]],
) -> None:
    """Validate staged forward data without allowing history rewrites.

    JSONL audit/source files may only be created or have lines appended.
    The one-shot start marker may only be added once and never modified.
    leaderboard.json is explicitly derived and may be replaced.
    """
    data_prefix = "prediction_market_tournament/data/"

    for path in staged:
        relative = path.removeprefix(data_prefix)
        status = statuses.get(path, "")
        added, deleted = stats.get(path, (0, 0))

        if relative == "leaderboard.json":
            if status.startswith("D"):
                raise RuntimeError("derived leaderboard may not be deleted")
            continue

        if relative == "forward_start_v1.json":
            if status != "A":
                raise RuntimeError(
                    "forward start marker is immutable after its first commit"
                )
            continue

        if relative.endswith(".jsonl"):
            if status.startswith(("D", "R")) or deleted != 0:
                raise RuntimeError(
                    f"forward JSONL must be append-only: {relative} "
                    f"status={status!r} deleted_lines={deleted}"
                )
            if added <= 0:
                raise RuntimeError(
                    f"forward JSONL change added no lines: {relative}"
                )
            continue

        raise RuntimeError(
            f"unrecognized mutable forward-data artifact: {relative}"
        )
