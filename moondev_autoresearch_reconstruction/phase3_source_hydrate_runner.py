"""Hydrate Phase-3 candidates with additional public source text.

This stage only retrieves public evidence from URLs already present in the
free-source discovery record. It does not infer rules, optimize parameters,
or touch hidden/final OOS data.
"""
from __future__ import annotations

from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen
import argparse
import json
import re

HERE = Path(__file__).resolve().parent
STATE = HERE / "phase3_state"
QUEUE = STATE / "hydration_queue.json"
RESULTS = STATE / "hydrated_sources.jsonl"
PROGRESS = STATE / "hydration_progress.json"
CURSOR = STATE / "hydration_cursor.json"
LANE = "phase3_source_hydration"
PROTOCOL = "nested_chronological_v3"
MAX_TEXT = 50000


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def key_for(row):
    return (row.get("source_url") or row.get("source_title") or "").strip().lower()


def prior_results():
    out = {}
    if not RESULTS.exists():
        return out
    for line in RESULTS.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except Exception:
            continue
        k = key_for(row)
        if k:
            out[k] = row
    return out


def append_result(row):
    STATE.mkdir(parents=True, exist_ok=True)
    with RESULTS.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")


class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag.lower() in {"script", "style", "noscript", "svg"}:
            self.skip += 1

    def handle_endtag(self, tag):
        if tag.lower() in {"script", "style", "noscript", "svg"} and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip:
            t = " ".join(data.split())
            if t:
                self.parts.append(t)


def github_readme_candidates(url):
    m = re.match(r"https?://github\.com/([^/]+)/([^/#?]+)", url, re.I)
    if not m:
        return []
    owner, repo = m.group(1), m.group(2).removesuffix(".git")
    return [
        f"https://raw.githubusercontent.com/{owner}/{repo}/main/README.md",
        f"https://raw.githubusercontent.com/{owner}/{repo}/master/README.md",
        f"https://raw.githubusercontent.com/{owner}/{repo}/main/README.rst",
        f"https://raw.githubusercontent.com/{owner}/{repo}/master/README.rst",
    ]


def normalize_target(url):
    url = str(url or "").strip().rstrip(".,);]}>")
    if not url.startswith(("http://", "https://")):
        return []
    if "github.com/" in url and "/blob/" in url:
        m = re.match(r"https?://github\.com/([^/]+)/([^/]+)/blob/([^/]+)/(.*)", url, re.I)
        if m:
            return [f"https://raw.githubusercontent.com/{m.group(1)}/{m.group(2)}/{m.group(3)}/{m.group(4)}"]
    if re.match(r"https?://github\.com/[^/]+/[^/#?]+/?$", url, re.I):
        return github_readme_candidates(url)
    return [url]


def fetch_text(url, timeout=12):
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return "", "unsupported_scheme"
    if parsed.path.lower().endswith((".pdf", ".zip", ".gz", ".tar", ".tgz", ".7z")):
        return "", "binary_skipped"
    req = Request(
        url,
        headers={
            "User-Agent": "AUTORESEARCH/1.0 public-source-hydration",
            "Accept": "text/plain,text/html,application/xhtml+xml,application/json;q=0.8,*/*;q=0.2",
        },
    )
    try:
        with urlopen(req, timeout=timeout) as resp:
            ctype = (resp.headers.get("Content-Type") or "").lower()
            raw = resp.read(180000)
            if "pdf" in ctype or "zip" in ctype or b"\x00" in raw[:4096]:
                return "", "binary_skipped"
            text = raw.decode("utf-8", errors="replace")
            if "html" in ctype or "<html" in text[:1000].lower():
                parser = TextExtractor()
                parser.feed(text)
                text = "\n".join(parser.parts)
            text = re.sub(r"\n{3,}", "\n\n", text)
            text = text.strip()
            return text[:MAX_TEXT], ("ok" if text else "empty")
    except Exception as exc:
        return "", f"{type(exc).__name__}: {str(exc)[:300]}"


def hydrate(row):
    targets = []
    for raw in list(row.get("fetch_targets") or []) + [row.get("source_url")]:
        for u in normalize_target(raw):
            if u and u not in targets:
                targets.append(u)
    evidence = []
    attempts = []
    for url in targets[:10]:
        text, status = fetch_text(url)
        attempts.append({"url": url, "status": status, "chars": len(text)})
        if text:
            evidence.append(f"SOURCE URL: {url}\n{text}")
        if sum(len(x) for x in evidence) >= MAX_TEXT:
            break
    hydrated = "\n\n--- PUBLIC SOURCE ---\n\n".join(evidence)[:MAX_TEXT]
    return hydrated, attempts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-candidates", type=int, default=20)
    args = ap.parse_args()
    if not QUEUE.exists():
        raise SystemExit("Phase-3 hydration queue missing")

    queue = load_json(QUEUE).get("candidates", [])
    results = prior_results()
    cursor = int(load_json(CURSOR).get("next_index", 0) or 0) if CURSOR.exists() else 0
    processed = 0
    scanned = 0

    while processed < max(1, args.max_candidates) and scanned < len(queue):
        idx = cursor % max(len(queue), 1)
        row = queue[idx]
        cursor = (idx + 1) % max(len(queue), 1)
        scanned += 1
        k = key_for(row)
        if not k or k in results:
            continue
        text, attempts = hydrate(row)
        result = {
            "ts": now(),
            "lane": LANE,
            "protocol": PROTOCOL,
            "source_title": row.get("source_title"),
            "source_url": row.get("source_url"),
            "source_type": row.get("source_type"),
            "archetype": row.get("archetype"),
            "hydration_status": "hydrated" if text else "attempted_no_text",
            "hydrated_text": text,
            "hydrated_chars": len(text),
            "fetch_attempts": attempts,
            "phase1_registry_mutated": False,
            "hidden_validation_opened": False,
            "final_oos_opened": False,
        }
        append_result(result)
        results[k] = result
        processed += 1

    save_json(CURSOR, {"next_index": cursor, "queue_count": len(queue), "updated_at": now()})
    vals = list(results.values())
    complete = len(vals) >= len(queue)
    progress = {
        "updated_at": now(),
        "lane": LANE,
        "protocol": PROTOCOL,
        "stage": "hydration_complete" if complete else "hydrating",
        "queue_count": len(queue),
        "processed_count": len(vals),
        "hydrated_count": sum(1 for x in vals if x.get("hydration_status") == "hydrated"),
        "attempted_no_text_count": sum(1 for x in vals if x.get("hydration_status") == "attempted_no_text"),
        "completion_pct": round(100.0 * len(vals) / max(len(queue), 1), 2),
        "all_hydrated_or_attempted": complete,
        "next_stage": "reconstruct_with_hydrated_evidence" if complete else "continue_source_hydration",
        "phase1_registry_mutated": False,
        "hidden_validation_opened": False,
        "final_oos_opened": False,
    }
    save_json(PROGRESS, progress)
    print(json.dumps(progress, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
