"""Trabajos científicos, cancelación y artefactos descargables."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from .. import campaigns
from ..analysis_jobs import AnalysisJobs
from ..api import get_analysis_jobs, get_pipeline
from ..pipeline import Pipeline

router = APIRouter()


def _campaign_root(pipeline: Pipeline, campaign: str) -> Path:
    if not campaign:
        return Path(pipeline.raw_root)
    if campaign not in campaigns.discover_campaign_ids(pipeline.raw_root):
        raise HTTPException(404, f"campaña desconocida: {campaign}")
    return campaigns.campaign_path(pipeline.raw_root, campaign)


@router.get("/api/jobs/{job_id}")
def job_get(
    job_id: str,
    pipeline: Pipeline = Depends(get_pipeline),
    analysis: AnalysisJobs = Depends(get_analysis_jobs),
):
    job = analysis.get(job_id) or pipeline.job(job_id)
    if job is None:
        raise HTTPException(404, "trabajo desconocido")
    return job


@router.post("/api/jobs/{job_id}/cancel")
def job_cancel(
    job_id: str,
    pipeline: Pipeline = Depends(get_pipeline),
    analysis: AnalysisJobs = Depends(get_analysis_jobs),
):
    if analysis.get(job_id):
        if not analysis.cancel(job_id):
            raise HTTPException(409, "el trabajo ya terminó")
        return analysis.get(job_id)
    if pipeline.cancel(job_id):
        return pipeline.job(job_id)
    raise HTTPException(404, "trabajo desconocido o ya iniciado")


@router.post("/api/exports")
def export_post(
    body: dict,
    pipeline: Pipeline = Depends(get_pipeline),
    analysis: AnalysisJobs = Depends(get_analysis_jobs),
):
    kind = str(body.get("kind") or "processed")
    if kind not in {"processed", "waterfall"}:
        raise HTTPException(400, f"tipo de exportación desconocido: {kind}")
    campaign = str(body.get("campaign", ""))
    root = _campaign_root(pipeline, campaign)
    return analysis.submit(
        "waterfall_export" if kind == "waterfall" else "processed_export",
        campaign,
        {
            "raw_root": str(root),
            "group_id": max(1, int(body.get("group_id", 1) or 1)),
            "prefer_filtered": bool(body.get("prefer_filtered", False)),
        },
    )


@router.get("/api/artifacts/{artifact_id}")
def artifact_get(
    artifact_id: str,
    analysis: AnalysisJobs = Depends(get_analysis_jobs),
):
    path = analysis.artifact(artifact_id)
    if path is None:
        raise HTTPException(404, "artefacto desconocido")
    return FileResponse(path, filename=path.name)
