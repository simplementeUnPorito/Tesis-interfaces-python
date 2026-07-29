"""Cola científica separada de la ingesta, aislada en procesos cancelables."""

from __future__ import annotations

import json
import multiprocessing
import os
import queue
import threading
import time
import traceback
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .state import atomic_write_json


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class AnalysisJob:
    id: str
    kind: str
    campaign: str
    payload: dict[str, Any] = field(repr=False)
    state: str = "pendiente"
    stage: str = "queued"
    progress: float = 0.0
    created_at: str = field(default_factory=_now)
    started_at: str = ""
    finished_at: str = ""
    error: str = ""
    result: dict[str, Any] = field(default_factory=dict)

    def public(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("payload", None)
        return data

    def persisted(self) -> dict[str, Any]:
        return asdict(self)


def _worker_entry(
    kind: str, payload: dict[str, Any], artifact_dir: str, result_path: str
) -> None:
    try:
        if kind == "masw_inversion":
            from .masw import run_inversion_payload

            result = run_inversion_payload(payload, Path(artifact_dir))
        elif kind == "processed_export":
            from .exports import run_processed_export

            result = run_processed_export(payload, Path(artifact_dir))
        elif kind == "waterfall_export":
            from .exports import run_waterfall_export

            result = run_waterfall_export(payload, Path(artifact_dir))
        else:
            raise ValueError(f"tipo de análisis desconocido: {kind}")
        atomic_write_json(result_path, {"ok": True, "result": result})
    except BaseException:  # el traceback completo queda como resultado del hijo
        atomic_write_json(
            result_path,
            {"ok": False, "error": traceback.format_exc(limit=20)},
        )
        raise


class AnalysisJobs:
    """Un proceso científico activo por vez; la ingesta usa otra cola."""

    def __init__(self, data_root: str | Path) -> None:
        self.data_root = Path(data_root)
        self.artifacts_root = self.data_root / "artifacts"
        self.results_root = self.data_root / "analysis-results"
        self.state_path = self.data_root / "analysis_jobs.json"
        self.artifacts_root.mkdir(parents=True, exist_ok=True)
        self.results_root.mkdir(parents=True, exist_ok=True)
        self._ctx = multiprocessing.get_context("spawn")
        self._queue: queue.Queue[str] = queue.Queue()
        self._jobs: dict[str, AnalysisJob] = {}
        self._lock = threading.RLock()
        self._active: tuple[str, multiprocessing.Process] | None = None
        self._load()
        self._thread = threading.Thread(
            target=self._loop, name="analysis-jobs", daemon=True
        )
        self._thread.start()
        for job in self._jobs.values():
            if job.state == "pendiente":
                self._queue.put(job.id)

    def _load(self) -> None:
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        for item in raw.get("jobs", []):
            try:
                job = AnalysisJob(**item)
            except (TypeError, ValueError):
                continue
            if job.state in {"running", "canceling"}:
                job.state = "pendiente"
                job.stage = "recovered"
                job.progress = 0.0
                job.error = ""
            self._jobs[job.id] = job

    def _save(self) -> None:
        with self._lock:
            data = {
                "updated_at": _now(),
                "jobs": [job.persisted() for job in self._jobs.values()],
            }
        atomic_write_json(self.state_path, data)

    def submit(
        self, kind: str, campaign: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        job_id = (
            datetime.now().strftime("%Y%m%d_%H%M%S")
            + "_"
            + uuid.uuid4().hex[:10]
        )
        job = AnalysisJob(
            id=job_id, kind=str(kind), campaign=str(campaign), payload=payload
        )
        with self._lock:
            self._jobs[job_id] = job
        self._save()
        self._queue.put(job_id)
        return job.public()

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            jobs = [job.public() for job in self._jobs.values()]
        return sorted(jobs, key=lambda item: item["created_at"], reverse=True)

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return job.public() if job else None

    def cancel(self, job_id: str) -> bool:
        process = None
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.state in {"listo", "error", "cancelado"}:
                return False
            if job.state == "pendiente":
                job.state = "cancelado"
                job.stage = "canceled"
                job.finished_at = _now()
            else:
                job.state = "canceling"
                job.stage = "canceling"
                if self._active and self._active[0] == job_id:
                    process = self._active[1]
        if process is not None and process.is_alive():
            process.terminate()
        self._save()
        return True

    def artifact(self, artifact_id: str) -> Path | None:
        safe = Path(str(artifact_id)).name
        if safe != artifact_id:
            return None
        for path in self.artifacts_root.rglob(safe):
            resolved = path.resolve()
            if resolved.is_file() and resolved.is_relative_to(
                self.artifacts_root.resolve()
            ):
                return resolved
        return None

    def _set(self, job_id: str, **values: Any) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            for key, value in values.items():
                setattr(job, key, value)
        self._save()

    def _loop(self) -> None:
        while True:
            job_id = self._queue.get()
            try:
                with self._lock:
                    job = self._jobs.get(job_id)
                    if job is None or job.state != "pendiente":
                        continue
                    payload = dict(job.payload)
                    kind = job.kind
                artifact_dir = self.artifacts_root / job_id
                artifact_dir.mkdir(parents=True, exist_ok=True)
                result_path = self.results_root / f"{job_id}.json"
                result_path.unlink(missing_ok=True)
                process = self._ctx.Process(
                    target=_worker_entry,
                    args=(kind, payload, str(artifact_dir), str(result_path)),
                    name=f"analysis-{job_id}",
                    daemon=False,
                )
                self._set(
                    job_id,
                    state="running",
                    stage="starting",
                    progress=0.05,
                    started_at=_now(),
                )
                process.start()
                with self._lock:
                    self._active = (job_id, process)
                while process.is_alive():
                    time.sleep(0.25)
                    current = self.get(job_id)
                    if current and current["state"] == "canceling":
                        process.terminate()
                        break
                    if current and current["progress"] < 0.2:
                        self._set(job_id, stage="computing", progress=0.2)
                process.join(timeout=5)
                with self._lock:
                    self._active = None
                    current_job = self._jobs.get(job_id)
                    canceled = bool(
                        current_job
                        and current_job.state in {"canceling", "cancelado"}
                    )
                if canceled:
                    self._set(
                        job_id,
                        state="cancelado",
                        stage="canceled",
                        finished_at=_now(),
                    )
                    continue
                try:
                    outcome = json.loads(result_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    outcome = {
                        "ok": False,
                        "error": f"el proceso terminó con código {process.exitcode} sin resultado",
                    }
                if process.exitcode == 0 and outcome.get("ok"):
                    self._set(
                        job_id,
                        state="listo",
                        stage="complete",
                        progress=1.0,
                        finished_at=_now(),
                        result=outcome.get("result") or {},
                    )
                else:
                    self._set(
                        job_id,
                        state="error",
                        stage="failed",
                        finished_at=_now(),
                        error=str(outcome.get("error") or f"exit {process.exitcode}"),
                    )
            finally:
                self._queue.task_done()
