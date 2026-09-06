"""One-look OOS evaluator for the current keeper."""

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--unlock",
        required=True,
        choices=["ONE_LOOK"],
        help="acknowledge that opening OOS spends the sealed holdout",
    )
    ap.parse_args()

    best = HERE / "strategy_best.py"
    strategy = HERE / "strategy.py"
    if not best.exists():
        raise SystemExit("No strategy_best.py yet. Run loop.py first.")
    shutil.copy(best, strategy)

    r = subprocess.run([sys.executable, "harness.py", "--oos"], cwd=HERE)
    if r.returncode != 0:
        raise SystemExit(r.returncode)

    with open(HERE / "last_run.json", encoding="utf-8") as f:
        result = json.load(f)

    print("\nLOCKED OOS RESULT")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
