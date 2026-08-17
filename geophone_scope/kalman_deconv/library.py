"""Biblioteca de modelos: geofonos y acondicionadores, cargables y guardables.

Dos catalogos independientes en JSON. Se elige un GEO y un CONDITIONER, y esa
pareja define la planta. No es un lujo: el modelo nominal y el medido plantean
problemas distintos y hay que poder correr los dos para compararlos
(HANDOFF_KALMAN.md §5.2).

Los presets viven en ``data/geophones`` y ``data/conditioners``. Los modelos que
guarde el usuario van al mismo directorio y aparecen listados junto a los
presets; ``builtin`` distingue unos de otros para que un preset no se pise sin
querer.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from .models import ConditionerSpec, GeophoneSpec

_DATA_ROOT = Path(__file__).resolve().parent / "data"
_GEO_DIR = _DATA_ROOT / "geophones"
_COND_DIR = _DATA_ROOT / "conditioners"

# Presets que vienen con el modulo. Sobrescribirlos exige overwrite=True.
BUILTIN_GEOPHONES = ("sm24_nominal", "sm24_shunt1339", "jf20dx_ma2023")
BUILTIN_CONDITIONERS = (
    "unity",
    "lp_pga_medido",
    "comp_nominal",
    "comp_ma2023",
    "geo_lp_medido",
)


def _encode_complex(values: Iterable[complex]) -> list[list[float]]:
    """JSON no tiene complejos: se guardan como pares [real, imag]."""
    return [[float(np.real(v)), float(np.imag(v))] for v in values]


def _decode_complex(values: Any) -> tuple[complex, ...]:
    return tuple(complex(re, im) for re, im in values)


def _read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    tmp.replace(path)


def sha256_of(path: str | Path) -> str:
    """Hash de un archivo fuente, para congelar la procedencia de un modelo.

    Si la fuente cambia, el modelo guardado deja de coincidir y eso se detecta en
    vez de arrastrar coeficientes viejos en silencio.
    """
    digest = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


# --------------------------------------------------------------------------- #
# Geofonos
# --------------------------------------------------------------------------- #


def list_geophones() -> list[dict[str, Any]]:
    """Resumen de los geofonos disponibles, ordenado por id."""
    out = []
    for path in sorted(_GEO_DIR.glob("*.json")):
        raw = _read_json(path)
        out.append(
            {
                "id": raw.get("id", path.stem),
                "name": raw.get("name", ""),
                "f_n_hz": raw.get("f_n_hz"),
                "zeta": raw.get("zeta"),
                "form": raw.get("form"),
                "builtin": raw.get("id", path.stem) in BUILTIN_GEOPHONES,
            }
        )
    return out


def load_geophone(geophone_id: str) -> GeophoneSpec:
    path = _GEO_DIR / f"{geophone_id}.json"
    if not path.exists():
        known = ", ".join(g["id"] for g in list_geophones()) or "(catalogo vacio)"
        raise KeyError(f"no existe el geofono {geophone_id!r}. Disponibles: {known}")
    raw = _read_json(path)
    raw.pop("schema", None)
    return GeophoneSpec(**raw)


def save_geophone(spec: GeophoneSpec, *, overwrite: bool = False) -> Path:
    path = _GEO_DIR / f"{spec.id}.json"
    if path.exists() and not overwrite:
        raise FileExistsError(f"ya existe {spec.id!r}; pasa overwrite=True para pisarlo")
    if spec.id in BUILTIN_GEOPHONES and not overwrite:
        raise FileExistsError(f"{spec.id!r} es un preset del modulo")
    payload = {"schema": "kalman_deconv_geophone_v1", **asdict(spec)}
    _write_json(path, payload)
    return path


# --------------------------------------------------------------------------- #
# Acondicionadores
# --------------------------------------------------------------------------- #


def list_conditioners() -> list[dict[str, Any]]:
    out = []
    for path in sorted(_COND_DIR.glob("*.json")):
        raw = _read_json(path)
        cid = raw.get("id", path.stem)
        out.append(
            {
                "id": cid,
                "name": raw.get("name", ""),
                "kind": raw.get("kind"),
                "n_zeros": len(raw.get("zeros", ())),
                "n_poles": len(raw.get("poles", ())),
                "includes_geophone": bool(raw.get("includes_geophone", False)),
                "builtin": cid in BUILTIN_CONDITIONERS,
            }
        )
    return out


def load_conditioner(conditioner_id: str) -> ConditionerSpec:
    path = _COND_DIR / f"{conditioner_id}.json"
    if not path.exists():
        known = ", ".join(c["id"] for c in list_conditioners()) or "(catalogo vacio)"
        raise KeyError(
            f"no existe el acondicionador {conditioner_id!r}. Disponibles: {known}"
        )
    raw = _read_json(path)
    raw.pop("schema", None)
    raw["zeros"] = _decode_complex(raw.get("zeros", ()))
    raw["poles"] = _decode_complex(raw.get("poles", ()))
    for key in ("valid_band_hz", "id_error_floor"):
        if key in raw and raw[key] is not None:
            raw[key] = tuple(raw[key])
    return ConditionerSpec(**raw)


def save_conditioner(spec: ConditionerSpec, *, overwrite: bool = False) -> Path:
    path = _COND_DIR / f"{spec.id}.json"
    if path.exists() and not overwrite:
        raise FileExistsError(f"ya existe {spec.id!r}; pasa overwrite=True para pisarlo")
    if spec.id in BUILTIN_CONDITIONERS and not overwrite:
        raise FileExistsError(f"{spec.id!r} es un preset del modulo")
    payload = {"schema": "kalman_deconv_conditioner_v1", **asdict(spec)}
    payload["zeros"] = _encode_complex(spec.zeros)
    payload["poles"] = _encode_complex(spec.poles)
    payload["valid_band_hz"] = list(spec.valid_band_hz)
    payload["id_error_floor"] = list(spec.id_error_floor)
    _write_json(path, payload)
    return path


# --------------------------------------------------------------------------- #
# Constructores desde una descripcion cruda
# --------------------------------------------------------------------------- #


def conditioner_from_num_den(
    num: Iterable[float],
    den: Iterable[float],
    *,
    id: str,
    name: str = "",
    **meta: Any,
) -> ConditionerSpec:
    """Convierte num/den a zpk UNA sola vez, para congelarlo en el catalogo.

    ``np.roots`` sobre polinomios con 25 decadas de rango dinamico es fragil; por
    eso se hace aca, se guarda el resultado, y en runtime se lee el zpk. El
    modulo no vuelve a llamar ``np.roots`` ni ``tf2ss``.
    """
    num_arr = np.asarray(list(num), dtype=float)
    den_arr = np.asarray(list(den), dtype=float)
    zeros = np.roots(num_arr)
    poles = np.roots(den_arr)
    # Ganancia de la forma zpk: cociente de los coeficientes lideres no nulos.
    lead_num = num_arr[np.flatnonzero(num_arr)[0]]
    lead_den = den_arr[np.flatnonzero(den_arr)[0]]
    return ConditionerSpec(
        id=id,
        name=name,
        kind="zpk",
        zeros=tuple(zeros),
        poles=tuple(poles),
        gain=float(lead_num / lead_den),
        **meta,
    )
