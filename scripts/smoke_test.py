#!/usr/bin/env python3
"""End-to-end smoke test against a temporary database.

Starts server.py on a free port with throwaway storage, exercises the main
API routes, and shuts the server down. Your real portfolio is never touched.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def request(port: int, method: str, path: str, payload=None):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if data else {},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            body = response.read().decode("utf-8", "replace")
            return response.status, body
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")


def wait_for_health(port: int, process: subprocess.Popen, timeout: float = 90) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"server exited with code {process.returncode}")
        try:
            status, _ = request(port, "GET", "/api/health")
            if status == 200:
                return
        except Exception:
            pass
        time.sleep(0.5)
    raise RuntimeError("server did not become healthy in time")


def check(condition: bool, label: str) -> None:
    if not condition:
        raise AssertionError(label)
    print(f"  OK   {label}")


def main() -> int:
    port = free_port()
    with tempfile.TemporaryDirectory(prefix="dividend-smoke-") as tmp:
        tmp_path = Path(tmp)
        env = os.environ.copy()
        env.update(
            {
                "HOST": "127.0.0.1",
                "PORT": str(port),
                "DIVIDEND_DB_DIR": str(tmp_path / "chroma"),
                "DIVIDEND_DATA_DIR": str(tmp_path / "data"),
                "ANONYMIZED_TELEMETRY": "False",
                "DIVIDEND_NO_VENV": "1",
            }
        )
        env.pop("DIVIDEND_CACHE_TTL", None)

        print(f"Starting server on port {port} with temporary storage...")
        process = subprocess.Popen(
            [sys.executable, str(ROOT / "server.py")],
            cwd=ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            wait_for_health(port, process)
            check(True, "server started and /api/health is 200")

            status, body = request(port, "GET", "/")
            check(status == 200 and "Dividend" in body, "app page renders")

            status, body = request(port, "GET", "/api/portfolio")
            payload = json.loads(body)
            check(status == 200 and payload["holdings"] == [], "empty portfolio")

            holding = {
                "ticker": "SMOKE",
                "name": "Smoke Test Ltd",
                "shares": 10,
                "quarters": {"2026-Q1": 10, "2026-Q2": 4},
                "dividends": [
                    {"date": "2026-05-01", "amount": 2.5},
                    {"date": "2025-05-01", "amount": 1.5},
                ],
                "price": 100,
            }
            status, body = request(port, "PUT", "/api/portfolio/SMOKE", holding)
            check(status == 200, "save holding")

            status, body = request(port, "GET", "/api/portfolio")
            payload = json.loads(body)
            check(
                status == 200
                and len(payload["holdings"]) == 1
                and payload["holdings"][0]["ticker"] == "SMOKE",
                "saved holding is returned",
            )

            status, body = request(port, "GET", "/api/analysis?year=2026")
            payload = json.loads(body)
            check(status == 200 and payload["totalIncome"] == 10.0, "analysis totals")
            projections = payload.get("projections") or {}
            check(
                status == 200
                and projections.get("base", {}).get("income") == payload.get("projectedNextYearIncome")
                and projections.get("conservative", {}).get("income") is not None,
                "analysis projection scenarios",
            )

            status, body = request(port, "GET", "/api/search?q=Smoke")
            check(status == 200, "semantic search responds")

            status, body = request(port, "GET", "/api/coach?year=2026")
            coach_payload = json.loads(body)
            check(
                status == 200
                and coach_payload.get("briefing")
                and isinstance(coach_payload.get("tasks"), list)
                and isinstance(coach_payload.get("events"), list)
                and coach_payload.get("ruleset", {}).get("id") == "v1",
                "coach briefing responds",
            )

            status, body = request(port, "DELETE", "/api/portfolio/SMOKE")
            check(status == 200, "delete holding")

            status, body = request(port, "GET", "/api/portfolio")
            payload = json.loads(body)
            check(status == 200 and payload["holdings"] == [], "delete persisted")

            stale = dict(holding, updatedAt="2000-01-01T00:00:00+00:00")
            status, body = request(port, "PUT", "/api/portfolio/SMOKE", stale)
            payload = json.loads(body)
            check(
                status == 200 and payload.get("ignored") is True,
                "stale copy cannot resurrect a deleted holding",
            )

            status, _ = request(port, "GET", "/api/portfolio/BAD%20TICKER")
            check(status == 404, "unknown API route returns 404")

            status, _ = request(
                port, "PUT", "/api/portfolio/" + urllib.parse.quote("bad ticker"), {}
            )
            check(status in (400, 404), "invalid ticker is rejected")
        finally:
            process.terminate()
            try:
                output, _ = process.communicate(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                output, _ = process.communicate()
            if process.returncode not in (0, -15, 1) and process.returncode != 143:
                print(output)
        print("Smoke test passed.")
        return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Smoke test failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
