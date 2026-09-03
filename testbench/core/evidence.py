"""Evidencia reproducible de una corrida, en el mismo esquema que el runner del firmware.

El banco no inventa un formato nuevo: escribe el mismo JSON que
``autotest_runner.py`` (``schema_version`` 1) para que las corridas hechas
desde acá y desde el runner se puedan comparar y archivar juntas en
``slave/artifacts/``. Se agregan campos, nunca se cambia el significado de los
que ya existían; los nuevos van bajo ``testbench`` para que sea evidente de
dónde salieron.
"""

from __future__ import annotations

import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .checklist import (
    D2_MIN_SLOPE_UV_PLACA,
    LSB_UV_PLACA,
    REQUIRED_ALWAYS,
    REQUIRED_WITH_PSOC,
    STAGE_NAMES,
    TAP_NAMES,
)
from .console import BAUD
from .session import RunResult

SCHEMA_VERSION = 1


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def build(
    result: RunResult,
    port: str,
    started_at: str,
    hw_profile: Optional[dict] = None,
    taps: Optional[list[dict]] = None,
    note: Optional[str] = None,
) -> dict:
    """Arma el diccionario de evidencia de una corrida."""
    p = result.parser
    m = p.meas
    v = result.verdict

    return {
        # -- campos compartidos con autotest_runner.py --------------------
        "schema_version": SCHEMA_VERSION,
        "test": "AUTOTEST_PLACA_NODO_ESCLAVO",
        "status": "ok" if v["ok"] else "failed",
        "error": None if result.completed else "la corrida no llego al #JSON",
        "started_at_utc": started_at,
        "finished_at_utc": utc_now(),
        "port": port,
        "baud": BAUD,
        "dtr": False,
        "rts": False,
        "required_always": list(REQUIRED_ALWAYS),
        "required_with_psoc": list(REQUIRED_WITH_PSOC),
        "raw_lines": len(p.lines),
        "items": [i.as_dict() for i in p.items],
        "firmware_json": p.json,
        "summary_verdict": p.summary_verdict,
        "counts": v["counts"],
        "fails": v["fails"],
        "warns": v["warns"],
        "missing_required": v["missing_required"],
        "problems": v["problems"],
        "board_verdict": v["board_verdict"],
        "coverage": v["coverage"],
        # -- agregado del banco -------------------------------------------
        "testbench": {
            "tool": "testbench",
            "command": result.command,
            "seconds": round(result.seconds, 2),
            "completed": result.completed,
            "python": sys.version.split()[0],
            "host": platform.node(),
            "note": note,
            "hw_profile": hw_profile or dict(p.hw),
            "link": {
                "up": p.link.up,
                "frames_ok": p.link.frames_ok,
                "frames_bad": p.link.frames_bad,
                "pings": p.link.pings,
                "diags": p.link.diags,
                "overruns": p.link.overruns,
            },
            # Escala corregida a la placa construida: el firmware informa con
            # la de la portadora JitX, que no se fabrico. Ver
            # registro_pruebas_analogicas_2026-09-02.md.
            "escala_placa": {
                "lsb_uv_por_codigo": LSB_UV_PLACA,
                "umbral_d2_uv_por_codigo": D2_MIN_SLOPE_UV_PLACA,
                "nota": (
                    "el firmware usa 3750 uV/codigo (R=30k de la portadora JitX); "
                    "esta placa tiene R=15k, asi que el LSB real es la mitad y el "
                    "umbral de D2 equivalente es 100 uV/codigo"
                ),
            },
            "mediciones": {
                "stage_names": list(STAGE_NAMES),
                "tap_names": list(TAP_NAMES[:4]),
                "d1_mv": m.d1_mv,
                "d2_uv_por_codigo": m.d2,
                "d2_diagonal": m.d2_diagonal(),
                "d2_veredicto_placa": [
                    {"etapa": n, "pendiente": s, "pasa": ok}
                    for n, s, ok in m.d2_veredicto_placa()
                ],
                "d6": m.d6,
                "d7": taps if taps is not None else m.d7,
            },
        },
    }


def save(data: dict, path: Path | str) -> Path:
    """Escribe la evidencia y devuelve la ruta."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def save_transcript(text: str, path: Path | str) -> Path:
    """Guarda el diálogo completo con la placa, con hora y dirección."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


__all__ = ["build", "save", "save_transcript", "utc_now", "SCHEMA_VERSION"]
