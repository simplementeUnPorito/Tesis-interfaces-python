"""Guarda en el perfil de la placa lo que dejó una corrida del lazo cerrado.

Es el paso que faltaba del plan de campo (ARQUITECTURA_CALIBRACION_CAMPO.md):
que lo aprendido en el banco quede archivado con FECHA y asociado a un
identificador de placa, para que en el campo no haya que volver a medirlo.

Se hace leyendo el log en vez de tocando `cerrar_lazo.py` a propósito. Ese
script es el que demostró funcionar y está corriendo la escalera de ganancias;
modificarlo a mitad de camino arriesgaría las corridas que faltan por una
ganancia que no cambia el resultado.

Lo que se archiva de cada ganancia:

  - Los cuatro códigos con los que terminó.
  - La CONSIGNA de ch2 que deja LPo legible, que es el dato caro: encontrarla
    cuesta un rastreo, y con ella la ganancia siguiente arranca de una
    predicción estrecha en vez de rastrear cuatro voltios.
  - El error final sobre LPo, para saber cuánto vale el punto.
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from perfil_placa import PerfilPlaca, resumen

RE_GANANCIA = re.compile(r"PGA x(\d+), PGAout x(\d+)")
RE_CONSIGNA = re.compile(r"Consigna encontrada: ch2 = ([-+]?\d+) mV")
RE_CODIGOS = re.compile(
    r"IDAC0=([-+]?\d+)\s+IDAC1=([-+]?\d+)\s+IDAC2=([-+]?\d+)")
RE_IDAC3 = re.compile(r"IDAC3=([-+]?\d+)")
RE_LPO = re.compile(r"LPo (?:final|en) ([-+]?\d+(?:\.\d+)?)")


def leer_log(ruta: Path) -> dict:
    """Saca de un log de cerrar_lazo lo que vale la pena archivar."""
    texto = ruta.read_text(encoding="utf-8", errors="replace")
    datos: dict = {}
    if (m := RE_GANANCIA.search(texto)):
        datos["pga"] = int(m.group(1))
        datos["pgaout"] = int(m.group(2))
    if (m := RE_CONSIGNA.search(texto)):
        datos["consigna_ch2_mv"] = int(m.group(1))
    # Los codigos de la ULTIMA aparicion: los primeros son los del adelanto.
    codigos = RE_CODIGOS.findall(texto)
    if codigos:
        datos["idac"] = [int(x) for x in codigos[-1]]
    idac3 = RE_IDAC3.findall(texto)
    if idac3:
        datos.setdefault("idac", [0, 0, 0])
        datos["idac"] = list(datos["idac"][:3]) + [int(idac3[-1])]
    lpo = RE_LPO.findall(texto)
    if lpo:
        datos["lpo_final_mv"] = float(lpo[-1])
    datos["convergio"] = "CONVERGIDO" in texto
    return datos


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("logs", nargs="+", help="logs de cerrar_lazo.py")
    ap.add_argument("--placa", default="geo-01")
    ap.add_argument("--seco", action="store_true",
                    help="muestra lo que haria sin escribir")
    args = ap.parse_args()

    perfil = PerfilPlaca.cargar(args.placa) or PerfilPlaca(
        id_placa=args.placa, fecha=date.today().isoformat())

    guardados = 0
    for nombre in args.logs:
        ruta = Path(nombre)
        if not ruta.exists():
            print(f"  {nombre}: no existe")
            continue
        datos = leer_log(ruta)
        if "pgaout" not in datos or "idac" not in datos:
            print(f"  {ruta.name}: sin ganancia o sin códigos; se saltea")
            continue
        clave = f"x{datos['pgaout']}"
        perfil.puntos[clave] = list(datos["idac"])
        if (consigna := datos.get("consigna_ch2_mv")) is not None:
            perfil.pendientes[f"consigna_ch2@{clave}"] = float(consigna)
        lpo = datos.get("lpo_final_mv")
        bueno = datos["convergio"] or (lpo is not None and abs(lpo) <= 50.0)
        if bueno and clave not in perfil.validados:
            perfil.validados.append(clave)
        print(f"  {ruta.name}: {clave} -> IDAC "
              + " ".join(f"{c:+d}" for c in datos["idac"])
              + (f", consigna ch2 {consigna:+d} mV" if consigna is not None
                 else "")
              + (f", LPo {lpo:+.1f} mV" if lpo is not None else "")
              + ("   VALIDADO" if bueno else "   sin validar"))
        guardados += 1

    if not guardados:
        print("Nada que guardar.")
        return 1
    perfil.fecha = date.today().isoformat()
    if args.seco:
        print("\n(seco: no se escribio nada)")
        print(resumen(perfil))
        return 0
    ruta = perfil.guardar()
    print(f"\nPerfil guardado en {ruta}")
    print(resumen(perfil))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
