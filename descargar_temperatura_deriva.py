# -*- coding: utf-8 -*-
"""Guarda la temperatura exterior horaria que acompana a EXP4c.

Es un proxy meteorologico de Open-Meteo, no una medida del banco. Sirve para
buscar coherencia temporal y formular la siguiente prueba, pero no para
atribuir causalidad termica a la placa.

Uso:  python descargar_temperatura_deriva.py [ruta_deriva.json]
"""
from __future__ import annotations

import glob
import json
import os
import sys
from datetime import datetime, timedelta
from urllib.parse import urlencode
from urllib.request import urlopen


LAB = r"C:\Github\Tesis\lab\planta"
LATITUD = -25.3387
LONGITUD = -57.6161
ZONA = "America/Asuncion"


def main() -> None:
    candidatos = glob.glob(os.path.join(LAB, "deriva_2026*.json"))
    ruta = sys.argv[1] if len(sys.argv) > 1 else max(candidatos, key=os.path.getmtime)
    with open(ruta, encoding="utf-8") as fh:
        deriva = json.load(fh)
    muestras = deriva.get("muestras", [])
    if not muestras:
        raise SystemExit("el registro de deriva no tiene muestras")

    inicio = datetime.fromisoformat(muestras[0]["t"])
    fin = datetime.fromisoformat(muestras[-1]["t"])
    dias = max(2, (datetime.now().date() - inicio.date()).days + 1)
    parametros = {
        "latitude": LATITUD,
        "longitude": LONGITUD,
        "hourly": "temperature_2m",
        "past_days": dias,
        "forecast_days": 1,
        "timezone": ZONA,
    }
    url = "https://api.open-meteo.com/v1/forecast?" + urlencode(parametros)
    with urlopen(url, timeout=30) as respuesta:
        meteo = json.load(respuesta)

    margen_inicio = inicio - timedelta(hours=1)
    margen_fin = fin + timedelta(hours=1)
    horas = []
    for t, temperatura in zip(meteo["hourly"]["time"],
                              meteo["hourly"]["temperature_2m"]):
        instante = datetime.fromisoformat(t)
        if margen_inicio <= instante <= margen_fin and temperatura is not None:
            horas.append({"t": t, "temperature_2m_c": temperatura})
    if len(horas) < 2:
        raise SystemExit("Open-Meteo no devolvio horas suficientes para el registro")

    salida = {
        "obtenido": datetime.now().astimezone().isoformat(timespec="seconds"),
        "fuente": url,
        "latitud_solicitada": LATITUD,
        "longitud_solicitada": LONGITUD,
        "latitud_modelo": meteo.get("latitude"),
        "longitud_modelo": meteo.get("longitude"),
        "elevacion_m": meteo.get("elevation"),
        "timezone": meteo.get("timezone"),
        "variable": "temperature_2m",
        "unidad": meteo.get("hourly_units", {}).get("temperature_2m", "°C"),
        "advertencia": (
            "Temperatura exterior modelada; no es un sensor en el banco y "
            "no permite atribuir causalidad termica a la placa."
        ),
        "registro_deriva": os.path.basename(ruta),
        "horas": horas,
    }
    destino = os.path.join(
        LAB, "temperatura_exterior_%s_%s.json" %
        (inicio.strftime("%Y%m%d"), fin.strftime("%Y%m%d")))
    temporal = destino + ".tmp"
    with open(temporal, "w", encoding="utf-8") as fh:
        json.dump(salida, fh, indent=1, ensure_ascii=False)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(temporal, destino)
    print("temperatura exterior -> %s (%d puntos horarios)" %
          (destino, len(horas)))


if __name__ == "__main__":
    main()
