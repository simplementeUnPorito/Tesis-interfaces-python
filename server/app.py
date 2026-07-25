"""Servidor de datos: recibe las capturas, las preprocesa y las sirve por web.

Arranque de FastAPI + uvicorn. Las rutas viven en ``api.py`` y ``routers/``.

Uso:
    python -m server                  # 0.0.0.0:8000, datos en data/server
    python -m server --port 9000 --data-root D:/geo
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import uvicorn

from .api import create_app
from .pipeline import Pipeline


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Servidor de datos Geophone")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--data-root", default=None,
                    help="dónde viven zips/ y jobs.json (por defecto <repo>/data/server)")
    ap.add_argument("--raw-root", default=None,
                    help="árbol de datasets, compartido con la app PyQt "
                         "(por defecto <repo>/data/raw)")
    args = ap.parse_args(argv)

    # src/interfaces/python/server/app.py -> subir 4 = raíz del superproyecto
    repo = Path(__file__).resolve().parents[4]
    data_root = Path(args.data_root) if args.data_root else repo / "data" / "server"
    # Por defecto data/raw, que es donde ya vive todo lo adquirido y lo que lee
    # review_field_data. Es a propósito: la web no tiene un dato propio: muestra
    # el mismo que la app de escritorio.
    raw_root = Path(args.raw_root) if args.raw_root else repo / "data" / "raw"
    data_root.mkdir(parents=True, exist_ok=True)
    raw_root.mkdir(parents=True, exist_ok=True)

    pipeline = Pipeline(data_root, raw_root=raw_root)
    app = create_app(pipeline)
    print(f"servidor de datos en http://{args.host}:{args.port}")
    print(f"datos en {data_root}")
    print(f"raw_root: {pipeline.raw_root}")
    try:
        uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    except KeyboardInterrupt:
        print("\ncortado")
    return 0


if __name__ == "__main__":
    sys.exit(main())
