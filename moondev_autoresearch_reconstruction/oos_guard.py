"""Fail closed if continuous AUTORESEARCH ever admits 2023+ market data."""

import csv
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
CUTOFF = "2023-01-01T00:00:00"


def main():
    violations = []

    for path in sorted((HERE / "data").glob("*.csv")):
        with path.open(encoding="utf-8", newline="") as f:
            rows = csv.DictReader(f)
            last = None
            for row in rows:
                stamp = row.get("Date") or row.get("Datetime") or row.get("timestamp")
                if stamp:
                    last = stamp
                    if stamp[:10] >= "2023-01-01":
                        violations.append(f"{path.name}: contains {stamp}")
                        break
        if last is None:
            violations.append(f"{path.name}: no timestamped rows")

    state = HERE / "continuous_state"
    if state.exists():
        for path in state.glob("tracks/*/*.json"):
            try:
                x = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if x.get("oos_opened") is True:
                violations.append(f"{path}: oos_opened=true")
            end = x.get("hidden_validation_end")
            if isinstance(end, str) and end[:10] >= "2023-01-01":
                violations.append(f"{path}: hidden_validation_end={end}")

    if violations:
        print("OOS GUARD FAIL")
        for item in violations:
            print("-", item)
        raise SystemExit(2)

    print("OOS GUARD PASS: no 2023+ market rows or OOS-open flags found")


if __name__ == "__main__":
    main()
