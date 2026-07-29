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
import hashlib
import os
import queue
import shutil
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
from .limits import (  # noqa: E402
    MAX_COMPRESSION_RATIO,
    MAX_UNCOMPRESSED_BYTES,
    MAX_ZIP_FILES,
)
from .state import atomic_write_bytes  # noqa: E402


SUPPORTED_ARCHIVE_SCHEMA = "geophone_scope_web_zip_v4"


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
    kind: str = "ingest"
    state: str = "pendiente"        # pendiente|descomprimiendo|procesando|listo|error
    stage: str = "queued"
    progress: float = 0.0
    created_at: str = field(default_factory=_now)
    started_at: str = ""
    finished_at: str = ""
    folder: str = ""                # carpeta del dataset que quedó
    captures: int = 0
    nodes: int = 0
    shots: int = 0
    picks: int = 0
    error: str = ""
    sha256: str = ""
    duplicate_of: str = ""
    log: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.job_id,
            "job_id": self.job_id,
            "kind": self.kind,
            "filename": self.filename,
            "bytes": self.bytes_received,
            "state": self.state,
            "stage": self.stage,
            "progress": round(float(self.progress), 4),
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "folder": self.folder,
            "captures": self.captures,
            "nodes": self.nodes,
            "shots": self.shots,
            "picks": self.picks,
            "error": self.error,
            "sha256": self.sha256,
            "duplicate_of": self.duplicate_of,
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
        self.incoming_root = self.data_root / "incoming"
        self.state_path = self.data_root / "jobs.json"
        self.staging_root = self.raw_root / ".ingest_staging"
        for d in (self.raw_root, self.zips_root, self.incoming_root, self.staging_root):
            d.mkdir(parents=True, exist_ok=True)

        self._q: queue.Queue[str] = queue.Queue()
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        # Lock aparte para escribir el archivo de estado. No se puede usar
        # `_lock` (lo toman los handlers y bloquearía lecturas por un fsync),
        # pero sin él dos ingestas simultáneas escriben el MISMO .tmp y en
        # Windows la segunda muere con PermissionError, perdiendo la cola.
        self._state_io_lock = threading.Lock()
        self._recover_ids: list[str] = []
        self._load_state()
        self._worker = threading.Thread(target=self._run, name="pipeline", daemon=True)
        self._worker.start()
        for job_id in self._recover_ids:
            self._q.put(job_id)

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
                kind=d.get("kind", "ingest"),
                state=d.get("state", "pendiente"),
                stage=d.get("stage", "queued"),
                progress=float(d.get("progress", 0.0) or 0.0),
                created_at=d.get("created_at", ""),
                started_at=d.get("started_at", ""),
                finished_at=d.get("finished_at", ""),
                folder=d.get("folder", ""),
                captures=int(d.get("captures", 0)),
                nodes=int(d.get("nodes", 0)),
                shots=int(d.get("shots", 0)),
                picks=int(d.get("picks", 0)),
                error=d.get("error", ""),
                sha256=d.get("sha256", ""),
                duplicate_of=d.get("duplicate_of", ""),
                log=list(d.get("log", [])),
            )
            if job.state in ("descomprimiendo", "procesando", "pendiente"):
                job.state = "pendiente"
                job.stage = "recovered"
                job.progress = 0.0
                job.error = ""
                job.log.append(f"{_now()} recuperado después de reiniciar el servidor")
            if job.job_id:
                self._jobs[job.job_id] = job
                if job.state == "pendiente":
                    self._recover_ids.append(job.job_id)

    def _save_state(self) -> None:
        with self._lock:
            data = {"updated_at": _now(),
                    "jobs": [j.to_dict() for j in self._jobs.values()]}
        blob = json.dumps(data, indent=2, ensure_ascii=False)
        with self._state_io_lock:
            atomic_write_bytes(self.state_path, blob.encode("utf-8"))

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
        digest = hashlib.sha256(data).hexdigest()
        tmp = self.incoming_root / (
            f"upload-{os.getpid()}-{threading.get_ident()}-{time.time_ns()}.tmp"
        )
        tmp.write_bytes(data)
        return self.submit_zip_file(tmp, filename, digest=digest, size=len(data))

    def submit_zip_file(
        self,
        temp_path: str | Path,
        filename: str,
        *,
        digest: str,
        size: int,
    ) -> Job:
        """Publica una subida ya transmitida y la encola de forma idempotente."""
        temp_path = Path(temp_path)
        # Validación estructural rápida antes de responder al ESP. El CRC
        # completo exige descomprimir el archivo entero y se hace en el worker,
        # para que la recepción no quede bloqueada por un ZIP grande.
        self._validate_archive(temp_path, verify_crc=False)
        safe = Path(filename).name or "captura.zip"
        digest = str(digest).lower()
        with self._lock:
            previous = next(
                (j for j in self._jobs.values() if j.sha256 == digest), None
            )
            if previous is not None:
                previous.log.append(
                    f"{_now()} reintento idempotente recibido; se reutiliza este trabajo"
                )
            else:
                # La comprobación y la reserva son una sola sección crítica:
                # dos reintentos simultáneos del mismo SHA no pueden publicar
                # dos trabajos ni pelear por el mismo ZIP.
                stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                job_id = f"{stamp}_{digest[:12]}"
                dest = self.zips_root / f"{job_id}.zip"
                temp_path.replace(dest)
                job = Job(
                    job_id=job_id,
                    filename=safe,
                    bytes_received=int(size),
                    sha256=digest,
                )
                job.log.append(
                    f"{_now()} ZIP guardado en {dest.name} "
                    f"({size} B, sha256 {digest[:12]})"
                )
                self._jobs[job_id] = job
        if previous is not None:
            temp_path.unlink(missing_ok=True)
            self._save_state()
            return previous
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
            job.stage = "queued"
            job.progress = 0.0
            job.error = ""
            job.finished_at = ""
        self._save_state()
        self._q.put(job_id)
        return True

    def cancel(self, job_id: str) -> bool:
        """Cancela sólo una ingesta todavía en cola; un proceso iniciado termina."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.state != "pendiente":
                return False
            job.state = "cancelado"
            job.stage = "canceled"
            job.finished_at = _now()
            job.log.append(f"{_now()} cancelado antes de procesar")
        self._save_state()
        return True

    # ── borrado (siempre pedido por un humano) ──────────────────────────────
    def delete_folder(self, folder_name: str, with_zip: bool = False,
                      campaign: str = "") -> dict:
        """Compatibilidad defensiva: el borrado físico está clausurado.

        La cuarentena reversible vive en ``server.deletion``. Mantener este
        método como rechazo explícito evita que una integración antigua pueda
        volver a habilitar accidentalmente ``rmtree`` o el borrado de ZIP.
        """
        return {
            "ok": False,
            "folder": Path(folder_name).name,
            "error": (
                "borrado físico deshabilitado; use /api/deletion/disable "
                "para aplicar la bandera reversible"
            ),
            "zip_preserved": True,
            "physical_deletion": False,
        }

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
                with self._lock:
                    queued = self._jobs.get(job_id)
                    if queued is None or queued.state == "cancelado":
                        continue
                self._process(job_id)
            except Exception:
                self._set(job_id, state="error", stage="failed", finished_at=_now(),
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

        self._set(
            job_id,
            state="descomprimiendo",
            stage="validating",
            progress=0.05,
            started_at=_now(),
            log_line="validando y descomprimiendo",
        )
        folder = self._extract(zip_path, job_id)
        self._set(job_id, folder=folder.name, stage="cataloging", progress=0.55,
                  log_line=f"extraído en raw/{folder.name}")

        self._set(job_id, state="procesando", stage="cataloging", progress=0.65,
                  log_line="descubriendo el dataset")
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
                      state="listo", stage="complete", progress=1.0,
                      finished_at=_now(),
                      log_line=f"{caps} captura(s), {nodos} nodo(s) catalogados; "
                               f"sin picking (falta el martillo)")
            return

        self._set(job_id, stage="autopick", progress=0.75,
                  log_line="picking automático del primer arribo")
        # Mismo archivo que usa la app PyQt (procesados/field_review_annotations
        # .json): así lo que preprocesa el servidor lo ve la app y al revés.
        picks_path = frd.default_annotations_path(self.raw_root)
        annotations = frd.load_annotations(picks_path)
        hechos = 0
        for index, shot in enumerate(shots):
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
            if index % 10 == 0:
                self._set(
                    job_id,
                    progress=0.75 + 0.2 * ((index + 1) / max(1, len(shots))),
                )

        frd.save_annotations(picks_path, dataset, annotations)
        self._set(job_id, picks=hechos, state="listo", stage="complete",
                  progress=1.0, finished_at=_now(),
                  log_line=f"{hechos} picks automáticos en {time.time() - t0:.1f} s "
                           f"-> {Path(picks_path).name}")

    def _validate_archive(
        self, zip_path: Path, *, verify_crc: bool = True
    ) -> list[zipfile.ZipInfo]:
        """Valida límites, rutas y el contrato ZIP actual antes de extraer.

        Una captura sin martillo o sin geófono sigue siendo válida: precisamente
        debe llegar a Capturas/Borrado para que el analista la clasifique. Lo que
        no se acepta es un ZIP arbitrario sin el ``metadata.json`` v4 que genera
        el master.
        """
        try:
            with zipfile.ZipFile(zip_path) as zf:
                infos = [info for info in zf.infolist() if not info.is_dir()]
                if not infos:
                    raise ValueError("el ZIP está vacío")
                if len(infos) > MAX_ZIP_FILES:
                    raise ValueError(
                        f"demasiados archivos ({len(infos)}; máximo {MAX_ZIP_FILES})"
                    )
                expanded = sum(max(0, int(info.file_size)) for info in infos)
                compressed = sum(max(0, int(info.compress_size)) for info in infos)
                if expanded > MAX_UNCOMPRESSED_BYTES:
                    raise ValueError(
                        f"ZIP expandido demasiado grande ({expanded} B; "
                        f"máximo {MAX_UNCOMPRESSED_BYTES} B)"
                    )
                ratio = expanded / max(1, compressed)
                if ratio > MAX_COMPRESSION_RATIO:
                    raise ValueError(
                        f"relación de compresión sospechosa ({ratio:.1f}×; "
                        f"máximo {MAX_COMPRESSION_RATIO:g}×)"
                    )
                normalized: dict[str, zipfile.ZipInfo] = {}
                normalized_folded: dict[str, str] = {}
                for info in infos:
                    name = info.filename.replace("\\", "/")
                    parts = Path(name).parts
                    if (
                        not name
                        or name.startswith("/")
                        or (len(name) > 1 and name[1] == ":")
                        or ".." in parts
                        or any(":" in part for part in parts)
                    ):
                        raise ValueError(f"ruta sospechosa en el ZIP: {info.filename}")
                    if info.flag_bits & 0x1:
                        raise ValueError(
                            f"archivo cifrado no soportado: {info.filename}"
                        )
                    if info.compress_type not in {
                        zipfile.ZIP_STORED,
                        zipfile.ZIP_DEFLATED,
                    }:
                        raise ValueError(
                            f"compresión no soportada en {info.filename}"
                        )
                    canonical = name.strip("/")
                    folded = canonical.casefold()
                    if folded in normalized_folded:
                        raise ValueError(
                            "ruta duplicada en el ZIP: "
                            f"{normalized_folded[folded]} y {info.filename}"
                        )
                    normalized_folded[folded] = info.filename
                    normalized[canonical] = info
                first_parts = {
                    name.split("/", 1)[0] for name in normalized if "/" in name
                }
                has_root_file = any("/" not in name for name in normalized)
                if not has_root_file and len(first_parts) == 1:
                    prefix = next(iter(first_parts)) + "/"
                    normalized = {
                        name[len(prefix):]: info
                        for name, info in normalized.items()
                        if name.startswith(prefix)
                    }

                metadata_info = normalized.get("metadata.json")
                if metadata_info is None:
                    raise ValueError(
                        "estructura inesperada: falta metadata.json del master"
                    )
                try:
                    metadata = json.loads(
                        zf.read(metadata_info).decode("utf-8-sig")
                    )
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise ValueError(
                        "estructura inesperada: metadata.json no es JSON válido"
                    ) from exc
                if not isinstance(metadata, dict):
                    raise ValueError(
                        "estructura inesperada: metadata.json debe ser un objeto"
                    )
                schema = str(metadata.get("schema") or "")
                if schema != SUPPORTED_ARCHIVE_SCHEMA:
                    raise ValueError(
                        "estructura inesperada: esquema "
                        f"{schema or '(ausente)'}; se esperaba "
                        f"{SUPPORTED_ARCHIVE_SCHEMA}"
                    )
                if verify_crc:
                    bad = zf.testzip()
                    if bad:
                        raise ValueError(f"CRC inválido en {bad}")
                return infos
        except zipfile.BadZipFile as exc:
            raise ValueError(f"ZIP inválido: {exc}") from exc

    def _extract(self, zip_path: Path, job_id: str) -> Path:
        """Extrae el ZIP a raw/<carpeta>.

        El ZIP que arma la SPA ya trae la estructura que consume
        discover_dataset (maestro/, captures/NNN_.../, <tipo>_<pcb>/…), así que
        acá no se reordena nada: solo se elige el nombre de la carpeta contenedora
        y se saca el prefijo si el ZIP ya venía con uno.
        """
        for candidate in self.raw_root.iterdir():
            marker = candidate / ".ingest_job"
            if candidate.is_dir() and marker.is_file():
                try:
                    if marker.read_text(encoding="utf-8").strip() == job_id:
                        return candidate
                except OSError:
                    pass

        infos = self._validate_archive(zip_path, verify_crc=True)
        with zipfile.ZipFile(zip_path) as zf:
            names = [info.filename.replace("\\", "/") for info in infos]

            # ¿Todo cuelga de una única carpeta raíz? Entonces esa es la carpeta.
            tops = {n.split("/", 1)[0] for n in names if "/" in n}
            single_root = len(tops) == 1 and all("/" in n for n in names)
            base = (tops.pop() if single_root
                    else Path(zip_path.stem).name)
            dest = self.raw_root / Path(base).name
            # Nunca sobrescribir una carpeta existente: dos capturas del mismo
            # punto son dos datos, no una corrección.
            n = 1
            while dest.exists():
                n += 1
                dest = self.raw_root / f"{base}_{n}"
            stage = self.staging_root / job_id
            if stage.exists():
                shutil.rmtree(stage)
            stage.mkdir(parents=True)
            try:
                stage_resolved = stage.resolve()
                for index, info in enumerate(infos):
                    rel = info.filename.replace("\\", "/")
                    if single_root:
                        rel = rel.split("/", 1)[1] if "/" in rel else rel
                    if not rel:
                        continue
                    target = (stage / rel).resolve()
                    if not target.is_relative_to(stage_resolved):
                        raise ValueError(
                            f"ruta sospechosa en el ZIP: {info.filename}"
                        )
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(info) as src, target.open("wb") as out:
                        shutil.copyfileobj(src, out, length=1024 * 1024)
                    if index % 50 == 0:
                        self._set(
                            job_id,
                            stage="extracting",
                            progress=0.1
                            + 0.4 * ((index + 1) / max(1, len(infos))),
                        )
                (stage / ".ingest_job").write_text(job_id + "\n", encoding="utf-8")
                stage.replace(dest)
                return dest
            except Exception:
                shutil.rmtree(stage, ignore_errors=True)
                raise
