"""Servidor de datos: recibe las capturas, las preprocesa y las sirve por web.

Arranque de FastAPI + uvicorn. Las rutas viven en ``api.py`` y ``routers/``.

Uso:
    python -m server                  # 0.0.0.0:8000, datos en data/server
    python -m server --port 9000 --data-root D:/geo/server \
        --raw-root D:/geo/raw --processed-root D:/geo/processed
"""

from __future__ import annotations

import argparse
import os
import socket
import sys
from pathlib import Path

import uvicorn


def _env_true(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {
        "1", "true", "yes", "on", "si", "sí",
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Servidor de datos Geophone")
    ap.add_argument("--port", type=int, default=int(os.environ.get("TESIS_PORT", "8000")))
    ap.add_argument("--host", default=os.environ.get("TESIS_HOST", "0.0.0.0"))
    ap.add_argument("--data-root", default=os.environ.get("TESIS_SERVER_DATA_ROOT"),
                    help="dónde viven zips/ y jobs.json "
                         "(por defecto <TESIS_DATA_ROOT>/server)")
    ap.add_argument("--raw-root", default=os.environ.get("TESIS_RAW_ROOT"),
                    help="árbol de datasets, compartido con la app PyQt "
                         "(por defecto <TESIS_DATA_ROOT>/raw)")
    ap.add_argument(
        "--processed-root",
        default=os.environ.get("TESIS_PROCESSED_ROOT"),
        help="estados JSON/NPZ compartidos con PyQt "
             "(por defecto <TESIS_DATA_ROOT>/processed o <repo>/data/processed)",
    )
    ap.add_argument(
        "--read-only",
        action="store_true",
        default=_env_true("TESIS_READ_ONLY"),
        help="rechazar todos los POST; útil para auditorías sobre datos reales",
    )
    args = ap.parse_args(argv)

    # src/interfaces/python/server/app.py -> subir 4 = raíz del superproyecto
    repo = Path(__file__).resolve().parents[4]
    storage_root = Path(
        os.environ.get("TESIS_DATA_ROOT", repo / "data")
    ).expanduser().resolve()
    data_root = (
        Path(args.data_root).expanduser().resolve()
        if args.data_root
        else storage_root / "server"
    )
    # TESIS_DATA_ROOT mueve el árbol completo, igual que field_review_data.
    # Las variables específicas siguen teniendo precedencia cuando se necesita
    # repartir raw, processed y server en volúmenes distintos.
    raw_root = (
        Path(args.raw_root).expanduser().resolve()
        if args.raw_root
        else storage_root / "raw"
    )
    processed_root = (
        Path(args.processed_root).expanduser().resolve()
        if args.processed_root
        else storage_root / "processed"
    )
    data_root.mkdir(parents=True, exist_ok=True)
    raw_root.mkdir(parents=True, exist_ok=True)
    processed_root.mkdir(parents=True, exist_ok=True)

    # ``field_review_data`` resuelve su raíz procesada al importarse. Las
    # importaciones quedan deliberadamente después de este override para que
    # --processed-root aísle de verdad anotaciones, grupos y estado MASW.
    os.environ["TESIS_PROCESSED_ROOT"] = str(processed_root)
    from .api import create_app
    from .pipeline import Pipeline

    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind((args.host, args.port))
    except OSError as exc:
        print(
            f"ERROR: no se puede usar {args.host}:{args.port}: {exc}\n"
            "Ya hay un servidor en ese puerto. Revisá http://127.0.0.1:"
            f"{args.port}/api/meta, detené el proceso anterior o elegí "
            f"--port {args.port + 1}.",
            file=sys.stderr,
        )
        return 2
    finally:
        probe.close()

    pipeline = Pipeline(data_root, raw_root=raw_root)
    app = create_app(pipeline, read_only=args.read_only)
    print(f"servidor de datos en http://{args.host}:{args.port}")
    print(f"datos en {data_root}")
    print(f"raw_root: {pipeline.raw_root}")
    print(f"processed_root: {processed_root}")
    if args.read_only:
        print("modo: SOLO LECTURA (todos los POST serán rechazados)")
    try:
        uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    except KeyboardInterrupt:
        print("\ncortado")
    except OSError as exc:
        print(f"ERROR al iniciar {args.host}:{args.port}: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
