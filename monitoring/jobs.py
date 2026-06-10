"""In-process job system for long-running pipeline operations.

Provides submit/poll/evict primitives used by the dashboard pipeline routes.
Jobs run as daemon threads; stdout is streamed and stored in _JOBS.
"""

from __future__ import annotations

import re
import subprocess
import sys
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict

_JOBS: Dict[str, dict] = {}
_JOBS_LOCK = threading.Lock()
_MAX_LOG_LINES = 300

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

_ANSI = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def _clean(line: str) -> str:
    return _ANSI.sub("", line).rstrip()


def _run_job(job_id: str, cmd: list) -> None:
    with _JOBS_LOCK:
        _JOBS[job_id]["status"] = "running"
        _JOBS[job_id]["started_at"] = datetime.now(timezone.utc).isoformat()

    lines: list = []
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(_PROJECT_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        with _JOBS_LOCK:
            _JOBS[job_id]["pid"] = proc.pid

        for raw in proc.stdout:  # type: ignore[union-attr]
            line = _clean(raw)
            if not line:
                continue
            lines.append(line)
            if len(lines) > _MAX_LOG_LINES:
                lines = lines[-_MAX_LOG_LINES:]
            with _JOBS_LOCK:
                _JOBS[job_id]["lines"] = list(lines)

        proc.wait()
        status = "done" if proc.returncode == 0 else "failed"
    except Exception as exc:
        lines.append(f"ERROR: {exc}")
        status = "failed"

    with _JOBS_LOCK:
        _JOBS[job_id]["status"] = status
        _JOBS[job_id]["lines"] = lines
        _JOBS[job_id]["finished_at"] = datetime.now(timezone.utc).isoformat()


def _evict_old_jobs() -> None:
    """Drop completed/failed jobs that finished more than 1 hour ago."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
    with _JOBS_LOCK:
        for job_id in list(_JOBS.keys()):
            job = _JOBS[job_id]
            if job["status"] in ("done", "failed") and job.get("finished_at"):
                try:
                    finished = datetime.fromisoformat(job["finished_at"])
                    if finished.tzinfo is None:
                        finished = finished.replace(tzinfo=timezone.utc)
                    if finished < cutoff:
                        del _JOBS[job_id]
                except Exception:
                    pass


def submit_job(job_type: str, cmd: list) -> str:
    _evict_old_jobs()
    job_id = str(uuid.uuid4())[:8]
    with _JOBS_LOCK:
        _JOBS[job_id] = {
            "id": job_id,
            "type": job_type,
            "status": "pending",
            "lines": [],
            "started_at": None,
            "finished_at": None,
            "pid": None,
        }
    threading.Thread(target=_run_job, args=(job_id, cmd), daemon=True).start()
    return job_id


def has_running_job(job_type: str) -> bool:
    with _JOBS_LOCK:
        return any(
            j["type"] == job_type and j["status"] in ("pending", "running")
            for j in _JOBS.values()
        )


def get_job(job_id: str) -> dict | None:
    with _JOBS_LOCK:
        return dict(_JOBS[job_id]) if job_id in _JOBS else None
