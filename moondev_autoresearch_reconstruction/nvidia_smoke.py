"""Minimal NVIDIA NIM connectivity test. Does not touch strategy or backtest state."""

import json
import os
import sys
import time

BASE_URL = "https://integrate.api.nvidia.com/v1"
DEFAULT_MODEL = "nvidia/nemotron-3-super-120b-a12b"


def main():
    key = os.environ.get("NVIDIA_API_KEY")
    if not key:
        print("NVIDIA_API_KEY is not set.", file=sys.stderr)
        return 2

    try:
        from openai import OpenAI
    except ImportError:
        print("Missing openai package. Run: pip install -r requirements.txt", file=sys.stderr)
        return 2

    model = os.environ.get("NVIDIA_MODEL", DEFAULT_MODEL)
    client = OpenAI(base_url=BASE_URL, api_key=key, timeout=60.0)
    t0 = time.time()
    response = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "user",
                "content": 'Return JSON only: {"ok":true,"message":"nvidia nim reachable"}',
            }
        ],
        temperature=0,
        max_tokens=100,
    )
    text = (response.choices[0].message.content or "").strip()
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        print(f"API reachable, but response was not strict JSON: {text[:300]!r}", file=sys.stderr)
        return 1

    print(f"model={model}")
    print(f"endpoint={BASE_URL}")
    print(f"duration_seconds={time.time() - t0:.3f}")
    print(json.dumps(obj, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
