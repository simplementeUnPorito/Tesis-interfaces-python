"""Ingesta y preprocesado en diferido de las capturas que manda la SPA.

Idea del diseño: el usuario final no espera nada. La SPA sube el ZIP, el servidor
lo descomprime y corre solo todo lo que antes se hacía a mano en la PC (descubrir
el dataset, cargar señales, picking automático del primer arribo). Cuando el
analista entra por la web, el trabajo pesado ya está hecho y solo queda la
validación humana — igual que en la app PyQt, pero sin mover archivos.

Reusa la capa de datos de ``geophone_scope`` tal cual: ``field_review_data`` no
tiene una sola línea de Qt, así que el picking, el layout de carpetas y el formato
de anotaciones son LOS MISMOS que usa la app de escritorio. Las dos pueden
trabajar sobre el mismo volumen sin traducciones de por medio.
"""

from __future__ import annotations

import json
import os
import queue
import threading
import time
import traceback
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# La capa de datos compartida con la app PyQt se resuelve en _gs (que hace el
# sys.path). Se re-exporta acá porque el resto del paquete ya importa
# `from .pipeline import frd`.
from ._gs import frd   # noqa: E402

from .catalog import scan_catalog   # noqa: E402


def _invalidate_caches(raw_root) -> None:
    """Tira los caches de escaneo. Import diferido: ``captures``/``datacache``
    no pueden importarse arriba sin volver circular a ``catalog``."""
    from . import captures, datacache
    datacache.invalidate(raw_root)
    captures.invalidate_scan(raw_root)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Job:
    """Una subida en proceso. El estado se persiste para que sobreviva reinicios."""

    job_id: str
    filename: str
    bytes_received: int
    state: str = "pendiente"        # pendiente|descomprimiendo|procesando|listo|error
    created_at: str = field(default_factory=_now)
    finished_at: str = ""
    folder: str = ""                # carpeta del dataset que quedó
    captures: int = 0
    nodes: int = 0
    shots: int = 0
    picks: int = 0
    error: str = ""
    log: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "filename": self.filename,
            "bytes": self.bytes_received,
            "state": self.state,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
            "folder": self.folder,
            "captures": self.captures,
            "nodes": self.nodes,
            "shots": self.shots,
            "picks": self.picks,
            "error": self.error,
            "log": self.log[-20:],
        }


class Pipeline:
    """Cola de trabajos con un worker. Un solo hilo a propósito.

    El picking carga señales enteras en memoria; correr varias sesiones en
    paralelo en una PC de escritorio pelea por RAM y no acelera nada útil. Si
    alguna vez hace falta, subir a un pool es cambiar este bucle.
    """

    def __init__(self, data_root: Path, raw_root: Path | None = None) -> None:
        self.data_root = Path(data_root)
        # raw_root separado del data_root a propósito: apunta al MISMO árbol de
        # datos que ya usa la app PyQt (data/raw), así todo lo adquirido hasta hoy
        # aparece en la web sin migrar nada, y lo que ingesta el servidor queda
        # donde la app de escritorio también lo ve. Un solo dato, dos interfaces.
        self.raw_root = Path(raw_root) if raw_root else (self.data_root / "raw")
        self.zips_root = self.data_root / "zips"
        self.state_path = self.data_root / "jobs.json"
        for d in (self.raw_root, self.zips_root):
            d.mkdir(parents=True, exist_ok=True)

        self._q: queue.Queue[str] = queue.Queue()
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        # Lock aparte para escribir el archivo de estado. No se puede usar
        # `_lock` (lo toman los handlers y bloquearía lecturas por un fsync),
        # pero sin él dos ingestas simultáneas escriben el MISMO .tmp y en
        # Windows la segunda muere con PermissionError, perdiendo la cola.
        self._state_io_lock = threading.Lock()
        self._load_state()
        self._worker = threading.Thread(target=self._run, name="pipeline", daemon=True)
        self._worker.start()

    # ── estado ──────────────────────────────────────────────────────────────
    def _load_state(self) -> None:
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:
            return
        for d in raw.get("jobs", []):
            job = Job(
                job_id=d.get("job_id", ""),
                filename=d.get("filename", ""),
                bytes_received=int(d.get("bytes", 0)),
                state=d.get("state", "pendiente"),
                created_at=d.get("created_at", ""),
                finished_at=d.get("finished_at", ""),
                folder=d.get("folder", ""),
                captures=int(d.get("captures", 0)),
                nodes=int(d.get("nodes", 0)),
                shots=int(d.get("shots", 0)),
                picks=int(d.get("picks", 0)),
                error=d.get("error", ""),
                log=list(d.get("log", [])),
            )
            # Un trabajo que quedó a mitad de camino en un corte se marca como
            # error en vez de fingir que sigue vivo: el ZIP está guardado y se
            # puede reprocesar.
            if job.state in ("descomprimiendo", "procesando", "pendiente"):
                job.state = "error"
                job.error = "interrumpido por un reinicio del servidor"
            if job.job_id:
                self._jobs[job.job_id] = job

    def _save_state(self) -> None:
        with self._lock:
            data = {"updated_at": _now(),
                    "jobs": [j.to_dict() for j in self._jobs.values()]}
        blob = json.dumps(data, indent=2, ensure_ascii=False)
        with self._state_io_lock:
            # Nombre único por escritura: si el replace de otro hilo llegara a
            # solaparse igual, no comparten el archivo temporal.
            tmp = self.state_path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
            try:
                tmp.write_text(blob, encoding="utf-8")
                tmp.replace(self.state_path)
            finally:
                tmp.unlink(missing_ok=True)

    def jobs(self) -> list[dict]:
        with self._lock:
            items = [j.to_dict() for j in self._jobs.values()]
        items.sort(key=lambda d: d["created_at"], reverse=True)
        return items

    def job(self, job_id: str) -> dict | None:
        with self._lock:
            j = self._jobs.get(job_id)
            return j.to_dict() if j else None

    # ── ingesta ─────────────────────────────────────────────────────────────
    def submit_zip(self, data: bytes, filename: str) -> Job:
        """Guarda el ZIP y encola su procesamiento. Devuelve enseguida.

        El ZIP crudo se conserva siempre, incluso si el procesado falla: es el
        dato original y volver a pedírselo al equipo de campo no siempre es
        posible.
        """
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe = Path(filename).name or "captura.zip"
        job_id = f"{stamp}_{safe.rsplit('.', 1)[0]}"[:80]
        dest = self.zips_root / f"{job_id}.zip"
        dest.write_bytes(data)

        job = Job(job_id=job_id, filename=safe, bytes_received=len(data))
        job.log.append(f"{_now()} ZIP guardado en {dest.name} ({len(data)} B)")
        with self._lock:
            self._jobs[job_id] = job
        self._save_state()
        self._q.put(job_id)
        return job

    def requeue(self, job_id: str) -> bool:
        """Reprocesa un ZIP ya subido (útil si el preprocesado falló)."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return False
            job.state = "pendiente"
            job.error = ""
            job.finished_at = ""
        self._save_state()
        self._q.put(job_id)
        return True

    # ── borrado (siempre pedido por un humano) ──────────────────────────────
    def delete_folder(self, folder_name: str, with_zip: bool = False,
                      campaign: str = "") -> dict:
        """Borra una carpeta extraída. Nunca se llama sola.

        Política: nada se borra automáticamente, ni por estar incompleto ni por
        antigüedad. El ZIP original se conserva salvo pedido explícito, porque es
        el dato tal como salió del campo y no siempre se puede volver a pedir.

        ``campaign`` es el id de campaña (``"."`` o vacío = la raíz). Sin él sólo
        se podían borrar las carpetas que cuelgan directo de ``raw_root``, y las
        de una campaña quedaban fuera de alcance.
        """
        import shutil

        safe = Path(folder_name).name
        if not safe or safe in (".", ".."):
            return {"ok": False, "error": "nombre inválido"}

        base = self.raw_root
        if campaign and campaign != ".":
            sub = Path(campaign).name
            if not sub or sub in (".", ".."):
                return {"ok": False, "error": "campaña inválida"}
            base = self.raw_root / sub
            if not base.is_dir():
                return {"ok": False, "error": f"campaña desconocida: {campaign}"}

        target = (base / safe).resolve()
        # Se compara contra raw_root igual: la campaña siempre cuelga de ahí, y
        # esto ataja un `..` que se haya colado por cualquiera de los dos lados.
        if not str(target).startswith(str(self.raw_root.resolve())):
            return {"ok": False, "error": "fuera de raw_root"}
        if not target.is_dir():
            return {"ok": False, "error": "no existe"}

        shutil.rmtree(target)
        zips = []
        if with_zip:
            for job_id, job in list(self._jobs.items()):
                if job.folder == safe:
                    z = self.zips_root / f"{job_id}.zip"
                    if z.exists():
                        z.unlink()
                        zips.append(z.name)
        # El trabajo queda en la lista, marcado: el historial de lo que llegó no
        # se pierde por haber borrado los archivos.
        with self._lock:
            for job in self._jobs.values():
                if job.folder == safe:
                    job.state = "borrado"
                    job.log.append(f"{_now()} borrado por el usuario"
                                   + (" (con ZIP)" if with_zip else " (ZIP conservado)"))
        self._save_state()
        _invalidate_caches(self.raw_root)
        return {"ok": True, "folder": safe, "zips_deleted": zips}

    # ── worker ──────────────────────────────────────────────────────────────
    def _set(self, job_id: str, **kw) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            for k, v in kw.items():
                if k == "log_line":
                    job.log.append(f"{_now()} {v}")
                else:
                    setattr(job, k, v)
        self._save_state()

    def _run(self) -> None:
        while True:
            job_id = self._q.get()
            try:
                self._process(job_id)
            except Exception:
                self._set(job_id, state="error", finished_at=_now(),
                          error=traceback.format_exc(limit=3),
                          log_line="falló el procesado")
            finally:
                # Entró (o se intentó meter) una captura nueva: el escaneo
                # cacheado quedó viejo. Se tira acá y no en cada rama para que
                # ninguna salida se olvide de hacerlo.
                _invalidate_caches(self.raw_root)
                self._q.task_done()

    def _process(self, job_id: str) -> None:
        zip_path = self.zips_root / f"{job_id}.zip"
        if not zip_path.exists():
            self._set(job_id, state="error", error="el ZIP ya no está en disco")
            return

        self._set(job_id, state="descomprimiendo", log_line="descomprimiendo")
        folder = self._extract(zip_path, job_id)
        self._set(job_id, folder=folder.name,
                  log_line=f"extraído en raw/{folder.name}")

        self._set(job_id, state="procesando", log_line="descubriendo el dataset")
        t0 = time.time()
        dataset = frd.discover_dataset(self.raw_root)
        # Solo los disparos de esta carpeta: el dataset abarca todo el raw_root.
        shots = [s for s in dataset.shots if s.folder.name == folder.name]
        self._set(job_id, shots=len(shots),
                  log_line=f"{len(shots)} disparos en esta carpeta "
                           f"({len(dataset.shots)} en total)")

        # Sin par hammer+geo no hay disparo MASW: auto_pick_shot detecta el primer
        # arribo EN la señal del martillo, así que sin fuente no hay nada que
        # picar. Eso no invalida la captura — queda catalogada y visible como
        # cualquier otra (ver catalog.py); lo único que no corre es el picking.
        if not shots:
            cat = scan_catalog(self.raw_root)
            mias = next((f for f in cat["folders"] if f["folder"] == folder.name), None)
            caps = len(mias["captures"]) if mias else 0
            nodos = sum(len(c["nodes"]) for c in mias["captures"]) if mias else 0
            self._set(job_id, captures=caps, nodes=nodos,
                      state="listo", finished_at=_now(),
                      log_line=f"{caps} captura(s), {nodos} nodo(s) catalogados; "
                               f"sin picking (falta el martillo)")
            return

        self._set(job_id, log_line="picking automático del primer arribo")
        # Mismo archivo que usa la app PyQt (procesados/field_review_annotations
        # .json): así lo que preprocesa el servidor lo ve la app y al revés.
        picks_path = frd.default_annotations_path(self.raw_root)
        annotations = frd.load_annotations(picks_path)
        hechos = 0
        for shot in shots:
            # No pisar lo que un humano ya revisó: el preprocesado es un punto de
            # partida, no la verdad.
            prev = annotations.get(shot.shot_id)
            if prev is not None and prev.reviewed:
                continue
            try:
                annotations[shot.shot_id] = frd.auto_pick_shot(shot)
                hechos += 1
            except Exception as exc:
                self._set(job_id, log_line=f"picking falló en {shot.capture_name}: {exc}")

        frd.save_annotations(picks_path, dataset, annotations)
        self._set(job_id, picks=hechos, state="listo", finished_at=_now(),
                  log_line=f"{hechos} picks automáticos en {time.time() - t0:.1f} s "
                           f"-> {Path(picks_path).name}")

    def _extract(self, zip_path: Path, job_id: str) -> Path:
        """Extrae el ZIP a raw/<carpeta>.

        El ZIP que arma la SPA ya trae la estructura que consume
        discover_dataset (maestro/, captures/NNN_.../, <tipo>_<pcb>/…), así que
        acá no se reordena nada: solo se elige el nombre de la carpeta contenedora
        y se saca el prefijo si el ZIP ya venía con uno.
        """
        with zipfile.ZipFile(zip_path) as zf:
            names = [n for n in zf.namelist() if not n.endswith("/")]
            if not names:
                raise ValueError("el ZIP está vacío")

            # ¿Todo cuelga de una única carpeta raíz? Entonces esa es la carpeta.
            tops = {n.split("/", 1)[0] for n in names if "/" in n}
            single_root = len(tops) == 1 and all("/" in n for n in names)
            base = (tops.pop() if single_root
                    else Path(zip_path.stem).name)
            dest = self.raw_root / base
            # Nunca sobrescribir una carpeta existente: dos capturas del mismo
            # punto son dos datos, no una corrección.
            n = 1
            while dest.exists():
                n += 1
                dest = self.raw_root / f"{base}_{n}"
            dest.mkdir(parents=True)

            for info in zf.infolist():
                if info.is_dir():
                    continue
                rel = info.filename
                if single_root:
                    rel = rel.split("/", 1)[1] if "/" in rel else rel
                if not rel:
                    continue
                # Zip-slip: rechazar rutas absolutas o con ".."
                target = (dest / rel).resolve()
                if not str(target).startswith(str(dest.resolve())):
                    raise ValueError(f"ruta sospechosa en el ZIP: {info.filename}")
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, open(target, "wb") as out:
                    while True:
                        chunk = src.read(65536)
                        if not chunk:
                            break
                        out.write(chunk)
            return dest
