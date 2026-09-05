#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from tournament.freeze import create_forward_marker


def main() -> int:
    root = Path(__file__).resolve().parents[1]

    # There is intentionally no --started-at/backdate option. The official
    # forward clock may begin only at the real current UTC time after the live
    # host/repository/network preflight succeeds.
    subprocess.run(
        [
            sys.executable,
            str(root / "scripts" / "preflight_forward.py"),
        ],
        cwd=str(root),
        check=True,
    )

    marker = create_forward_marker(root)
    print(json.dumps(marker, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
