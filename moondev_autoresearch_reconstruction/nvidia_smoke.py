"""Minimal NVIDIA NIM connectivity test. Does not touch strategy or backtest state."""

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
        print(
            "Missing openai package. Run: pip install -r requirements.txt",
            file=sys.stderr,
        )
        return 2

    model = os.environ.get("NVIDIA_MODEL", DEFAULT_MODEL)
    client = OpenAI(base_url=BASE_URL, api_key=key, timeout=60.0)
    t0 = time.time()

    response = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "user",
                "content": "Reply with the single word OK.",
            }
        ],
        temperature=0,
        max_tokens=32,
    )

    if not response.choices:
        print("API call succeeded but returned no choices.", file=sys.stderr)
        return 1

    choice = response.choices[0]
    text = (choice.message.content or "").strip()
    print(f"model={model}")
    print(f"endpoint={BASE_URL}")
    print(f"duration_seconds={time.time() - t0:.3f}")
    print(f"finish_reason={choice.finish_reason}")
    print(f"response_preview={text[:120]!r}")

    # Connectivity is established by a successful authenticated completion.
    # Do not require exact formatting from the model; the real loop has its
    # own response validation and repair protocol.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
