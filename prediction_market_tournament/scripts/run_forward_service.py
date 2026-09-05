#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from pathlib import Path

from tournament.freeze import load_frozen_spec, require_forward_started
from tournament.ledger import append_jsonl


class ServiceLock(AbstractContextManager):
    """Cross-platform non-blocking singleton lock held for process lifetime."""

    def __init__(self, path: Path):
        self.path = path
        self.handle = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+b")
        self.handle.seek(0, os.SEEK_END)
        if self.handle.tell() == 0:
            self.handle.write(b"0")
            self.handle.flush()
        self.handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(
                    self.handle.fileno(),
                    fcntl.LOCK_EX | fcntl.LOCK_NB,
                )
        except OSError as exc:
            self.handle.close()
            self.handle = None
            raise RuntimeError("another PMT forward service is already running") from exc
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.handle is None:
            return False
        try:
            self.handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        finally:
            self.handle.close()
            self.handle = None
        return False


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _run_script(
    root: Path,
    script_name: str,
    *,
    capture_stdout: bool = False,
) -> tuple[int, bytes, bytes]:
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(root / "scripts" / script_name),
        cwd=str(root),
        stdout=(
            asyncio.subprocess.PIPE
            if capture_stdout
            else asyncio.subprocess.DEVNULL
        ),
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    return process.returncode, stdout or b"", stderr or b""


async def _periodic_script(
    root: Path,
    log_path: Path,
    script_name: str,
    interval_seconds: float,
) -> None:
    while True:
        started = datetime.now(timezone.utc)
        try:
            code, _, stderr = await _run_script(root, script_name)
            append_jsonl(
                log_path,
                {
                    "kind": "service_periodic_run",
                    "observed_at": _now(),
                    "script": script_name,
                    "returncode": code,
                    "stderr_tail": stderr.decode(
                        "utf-8", errors="replace"
                    )[-2000:],
                },
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            append_jsonl(
                log_path,
                {
                    "kind": "service_periodic_error",
                    "observed_at": _now(),
                    "script": script_name,
                    "error": f"{type(exc).__name__}:{exc}",
                },
            )

        elapsed = (datetime.now(timezone.utc) - started).total_seconds()
        await asyncio.sleep(max(1.0, interval_seconds - elapsed))


async def _leaderboard_loop(
    root: Path,
    log_path: Path,
    interval_seconds: float,
) -> None:
    destination = root / "data" / "leaderboard.json"
    while True:
        started = datetime.now(timezone.utc)
        try:
            code, stdout, stderr = await _run_script(
                root,
                "build_leaderboard.py",
                capture_stdout=True,
            )
            if code == 0:
                payload = json.loads(stdout.decode("utf-8"))
                tmp = destination.with_suffix(".json.tmp")
                tmp.parent.mkdir(parents=True, exist_ok=True)
                tmp.write_text(
                    json.dumps(
                        payload,
                        sort_keys=True,
                        indent=2,
                        allow_nan=False,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                os.replace(tmp, destination)
            append_jsonl(
                log_path,
                {
                    "kind": "service_leaderboard_run",
                    "observed_at": _now(),
                    "returncode": code,
                    "stderr_tail": stderr.decode(
                        "utf-8", errors="replace"
                    )[-2000:],
                },
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            append_jsonl(
                log_path,
                {
                    "kind": "service_leaderboard_error",
                    "observed_at": _now(),
                    "error": f"{type(exc).__name__}:{exc}",
                },
            )

        elapsed = (datetime.now(timezone.utc) - started).total_seconds()
        await asyncio.sleep(max(1.0, interval_seconds - elapsed))


async def _crypto_supervisor(
    root: Path,
    log_path: Path,
    restart_delay_seconds: float,
) -> None:
    while True:
        process = None
        try:
            append_jsonl(
                log_path,
                {
                    "kind": "service_crypto_start",
                    "observed_at": _now(),
                },
            )
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                str(root / "scripts" / "scan_crypto_live.py"),
                "--duration-seconds",
                "0",
                cwd=str(root),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await process.communicate()
            append_jsonl(
                log_path,
                {
                    "kind": "service_crypto_exit",
                    "observed_at": _now(),
                    "returncode": process.returncode,
                    "stderr_tail": (stderr or b"").decode(
                        "utf-8", errors="replace"
                    )[-4000:],
                },
            )
        except asyncio.CancelledError:
            if process is not None and process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=5)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()
            raise
        except Exception as exc:
            append_jsonl(
                log_path,
                {
                    "kind": "service_crypto_error",
                    "observed_at": _now(),
                    "error": f"{type(exc).__name__}:{exc}",
                },
            )
        await asyncio.sleep(restart_delay_seconds)


async def run_service(root: Path) -> None:
    marker = require_forward_started(root)
    spec, spec_sha = load_frozen_spec(root / "config" / "frozen_v1.json")
    service_cfg = spec["service"]
    log_path = root / "data" / "forward_service_log.jsonl"

    append_jsonl(
        log_path,
        {
            "kind": "service_started",
            "observed_at": _now(),
            "spec_sha256": spec_sha,
            "implementation_sha256": marker["implementation_sha256"],
            "started_at": marker["started_at"],
        },
    )

    tasks = [
        asyncio.create_task(
            _crypto_supervisor(
                root,
                log_path,
                float(service_cfg["crypto_restart_delay_seconds"]),
            )
        ),
        asyncio.create_task(
            _periodic_script(
                root,
                log_path,
                "scan_weather_all.py",
                float(service_cfg["weather_scan_interval_seconds"]),
            )
        ),
        asyncio.create_task(
            _periodic_script(
                root,
                log_path,
                "settle_signals.py",
                float(service_cfg["settlement_interval_seconds"]),
            )
        ),
        asyncio.create_task(
            _periodic_script(
                root,
                log_path,
                "scan_complete_sets.py",
                float(service_cfg["complete_set_scan_interval_seconds"]),
            )
        ),
        asyncio.create_task(
            _leaderboard_loop(
                root,
                log_path,
                float(service_cfg["leaderboard_interval_seconds"]),
            )
        ),
    ]

    try:
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        append_jsonl(
            log_path,
            {
                "kind": "service_stopped",
                "observed_at": _now(),
            },
        )


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    lock_path = root / "data" / "forward_service.lock"
    with ServiceLock(lock_path):
        try:
            asyncio.run(run_service(root))
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
