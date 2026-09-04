"""Convierte lo que mide `medir_planta.py` en tablas y figuras.

Separado a proposito del programa que mide: el crudo se guarda una vez y se
analiza las veces que haga falta, sin volver a ocupar la placa. Si un ajuste
sale mal se corrige aca y se vuelve a correr sobre el mismo JSON.

Produce, para cada experimento:

  escalon  la MATRIZ DE ACOPLE: ganancia directa y cruzada de cada par
           (etapa, tap) y el tau de cada una. Es el numero que decide cuanto hay
           que esperar entre etapas, y hoy no existe: el tau de 31 s esta medido
           solo en la salida.

  curva    la curva de cada IDAC contra su tap, la pendiente local y cuanto se
           aparta de una recta. Hoy solo el LP tiene curva; las otras tres usan
           una constante y nadie verifico que sean lineales.

  matriz   por cada combinacion PGA x PGAout: el offset en reposo, la autoridad
           de cada etapa y si esa autoridad ALCANZA para corregir el offset.
           Ordenado de peor a mejor, que es como hay que leerlo.

Uso:
    python analizar_planta.py                 # todo lo que haya en lab/planta
    python analizar_planta.py --figs          # ademas de las tablas, las figuras
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
ENTRADA = REPO / "lab" / "planta"
SALIDA = REPO / "lab" / "planta" / "analisis"

CUENTAS_POR_MV = 262144.0 * 1000.0 / 5_000_000.0     # 52,4288
IDAC_UV_POR_CODIGO = 1875.0
NOMBRE_TAP = {0: "PGA", 1: "BP", 2: "ADDER", 3: "LP"}

# Objetivo de Elias: fiable siempre abajo de +-100 mV, idealmente ~20 mV,
# porque el rango mas fino del ADC es +-0,512 V.
OBJETIVO_IDEAL_MV = 20.0
OBJETIVO_MAXIMO_MV = 100.0


def uv_a_mv(uv: float | None) -> float:
    return float("nan") if uv is None else uv / 1000.0


# --------------------------------------------------------------------------
# escalon -> matriz de acople
# --------------------------------------------------------------------------

def tabla_escalon(d: dict) -> list[dict]:
    """Ganancia directa, ganancias cruzadas y tau de cada par (etapa, tap)."""
    filas = []
    for ens in d.get("ensayos", []):
        etapa, destino = ens["etapa"], ens["destino"]
        base = ens.get("base_uv", {})
        for ch_s, aj in (ens.get("ajustes") or {}).items():
            ch = int(ch_s)
            if aj is None:
                continue
            b = base.get(str(ch), base.get(ch))
            salto_codigos = destino - 0 if destino != 0 else -0
            filas.append({
                "etapa": etapa,
                "tap": ch,
                "tipo": "directo" if ch == etapa else ("cruzado" if ch > etapa else "aguas arriba"),
                "destino": destino,
                "base_mv": uv_a_mv(b),
                "y_inf_mv": aj["y_inf_uv"] / 1000.0,
                "amplitud_mv": aj["amplitud_uv"] / 1000.0,
                "tau_s": aj["tau_s"],
                "plano": aj["plano"],
                "rms_uv": aj["rms_uv"],
                # uV en el tap por codigo de IDAC: la ganancia que el firmware
                # necesita para convertir error en pasos.
                "uv_por_codigo": (
                    (aj["y_inf_uv"] - (b or 0.0)) / salto_codigos
                    if salto_codigos else float("nan")),
            })
    return filas


def imprimir_escalon(d: dict) -> None:
    print(f"\n=== ESCALON  PGA={d.get('pga_x')}x PGAout={d.get('pgaout_x')}x "
          f"amplitud {d.get('amplitud'):+d} ===")
    print(f"{'etapa':>6} {'tap':>5} {'tipo':>13} {'A [mV]':>9} {'tau [s]':>9} "
          f"{'uV/codigo':>11} {'rms':>7}")
    for f in tabla_escalon(d):
        tau = "-" if (f["plano"] or math.isnan(f["tau_s"])) else f"{f['tau_s']:.2f}"
        print(f"{f['etapa']:>6} {f['tap']:>5} {f['tipo']:>13} "
              f"{f['amplitud_mv']:>9.3f} {tau:>9} "
              f"{f['uv_por_codigo']:>11.1f} {f['rms_uv']:>7.0f}")

    # Lo que hay que sacar en limpio: el tau mas lento que ve cada etapa.
    print("\n  tau que enfrenta cada etapa (el mas lento de los que la afectan):")
    for etapa in range(4):
        taus = [f["tau_s"] for f in tabla_escalon(d)
                if f["tap"] == etapa and not f["plano"] and not math.isnan(f["tau_s"])]
        if taus:
            print(f"    etapa {etapa} ({NOMBRE_TAP[etapa]:<5}): "
                  f"tau_max = {max(taus):.2f} s   -> 3 tau = {3*max(taus):.0f} s")


# --------------------------------------------------------------------------
# curva -> no linealidad
# --------------------------------------------------------------------------

def analizar_curva(d: dict) -> dict:
    """Pendiente local y apartamiento de la recta, sin suponer linealidad."""
    etapa = d["etapa"]
    pts = [(p["code"], p.get(f"ch{etapa}")) for p in d["puntos"]
           if p.get(f"ch{etapa}") is not None]
    if len(pts) < 3:
        return {"etapa": etapa, "puntos": 0}

    xs = [float(c) for c, _ in pts]
    ys = [float(v) for _, v in pts]
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    pend = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx if sxx else float("nan")
    orden = my - pend * mx
    resid = [y - (pend * x + orden) for x, y in zip(xs, ys)]
    rms = math.sqrt(sum(r * r for r in resid) / n)

    # Pendiente local por tramos: es lo que delata la no linealidad.
    locales = []
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if x1 != x0:
            locales.append(((x0 + x1) / 2.0, (y1 - y0) / (x1 - x0)))
    ls = [s for _, s in locales]
    razon = (max(ls) / min(ls)) if ls and min(ls) > 0 else float("nan")

    return {
        "etapa": etapa, "puntos": n,
        "pendiente_uv_por_codigo": pend,
        "ganancia_x1000": pend / IDAC_UV_POR_CODIGO * 1000.0,
        "residuo_rms_uv": rms,
        "residuo_rms_mv": rms / 1000.0,
        "pendiente_min": min(ls) if ls else float("nan"),
        "pendiente_max": max(ls) if ls else float("nan"),
        "razon_no_linealidad": razon,
        "recorrido_mv": (max(ys) - min(ys)) / 1000.0,
        "locales": locales,
    }


def imprimir_curva(d: dict) -> None:
    a = analizar_curva(d)
    if not a.get("puntos"):
        print("  curva sin puntos suficientes"); return
    print(f"\n=== CURVA etapa {a['etapa']} ({NOMBRE_TAP[a['etapa']]})  "
          f"PGA={d.get('pga_x')}x PGAout={d.get('pgaout_x')}x ===")
    print(f"  puntos            {a['puntos']}")
    print(f"  recorrido         {a['recorrido_mv']:.2f} mV")
    print(f"  pendiente global  {a['pendiente_uv_por_codigo']:.2f} uV/codigo "
          f"(ganancia x1000 = {a['ganancia_x1000']:.0f})")
    print(f"  pendiente local   {a['pendiente_min']:.2f} .. {a['pendiente_max']:.2f} uV/codigo")
    print(f"  razon max/min     {a['razon_no_linealidad']:.2f}")
    print(f"  residuo vs recta  {a['residuo_rms_mv']:.3f} mV rms")
    if a["razon_no_linealidad"] > 1.25:
        print(f"  -> NO ES LINEAL. Una constante de ganancia no alcanza para esta "
              f"etapa; necesita curva a trozos como ya tiene el LP.")
    else:
        print(f"  -> compatible con lineal dentro de lo medido.")


# --------------------------------------------------------------------------
# matriz -> se puede calibrar con cualquier par de ganancias?
# --------------------------------------------------------------------------

def imprimir_matriz(d: dict) -> None:
    print(f"\n=== MATRIZ PGA x PGAout ===")
    filas = []
    for f in d.get("filas", []):
        peor = None
        detalle = []
        for st in range(4):
            ext = f["autoridad_uv"].get(str(st), f["autoridad_uv"].get(st, {}))
            arriba = ext.get("255") if isinstance(ext, dict) else None
            abajo = ext.get("-255") if isinstance(ext, dict) else None
            reposo = f["reposo_uv"].get(str(st), f["reposo_uv"].get(st))
            if None in (arriba, abajo, reposo):
                continue
            # Autoridad: cuanto puede mover el tap en cada sentido desde reposo.
            sube = (arriba - reposo) / 1000.0
            baja = (abajo - reposo) / 1000.0
            off = reposo / 1000.0
            # Alcanza si el offset queda dentro del rango alcanzable.
            alcanza = (min(sube, baja) <= -off <= max(sube, baja))
            margen = min(abs(-off - min(sube, baja)), abs(max(sube, baja) - (-off)))
            detalle.append({"etapa": st, "offset_mv": off, "sube_mv": sube,
                            "baja_mv": baja, "alcanza": alcanza,
                            "margen_mv": margen if alcanza else -abs(off)})
            if peor is None or margen < peor:
                peor = margen
        filas.append({"pga_x": f["pga_x"], "pgaout_x": f["pgaout_x"],
                      "margen_peor_mv": peor, "detalle": detalle,
                      "calibrable": all(x["alcanza"] for x in detalle) if detalle else False})
    filas.sort(key=lambda r: (r["calibrable"], r["margen_peor_mv"] if r["margen_peor_mv"] is not None else 0))

    print(f"{'PGA':>5} {'PGAout':>7} {'calibrable':>11} {'margen peor':>13}  etapas sin autoridad")
    for r in filas:
        malas = ",".join(NOMBRE_TAP[x["etapa"]] for x in r["detalle"] if not x["alcanza"])
        m = f"{r['margen_peor_mv']:.1f} mV" if r["margen_peor_mv"] is not None else "-"
        print(f"{r['pga_x']:>5} {r['pgaout_x']:>7} {str(r['calibrable']):>11} "
              f"{m:>13}  {malas or '-'}")
    n_ok = sum(1 for r in filas if r["calibrable"])
    print(f"\n  {n_ok} de {len(filas)} combinaciones se pueden calibrar.")
    if n_ok < len(filas):
        print("  Las que no, es por falta de AUTORIDAD del IDAC, no por sintonia:")
        print("  no se arregla con firmware, hay que tocar el rango de referencias.")
    return filas


# --------------------------------------------------------------------------

def figuras(datos: list[tuple[Path, dict]]) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    SALIDA.mkdir(parents=True, exist_ok=True)
    FONDO = "#faf9f7"
    col = {0: "#c1440e", 1: "#1f6f8b", 2: "#3f7d20", 3: "#7d3c98"}

    for ruta, d in datos:
        if d.get("experimento") == "escalon":
            for ens in d.get("ensayos", []):
                fig, ax = plt.subplots(figsize=(9, 4), facecolor=FONDO)
                ax.set_facecolor(FONDO)
                for ch in range(4):
                    pts = [(p["t"], p["uv"] / 1000.0) for p in ens["puntos"] if p["ch"] == ch]
                    if pts:
                        ax.plot([t for t, _ in pts], [v for _, v in pts], ".-", ms=3,
                                lw=1, color=col[ch], label=f"ch{ch} {NOMBRE_TAP[ch]}")
                ax.set_xlabel("tiempo desde el escalon [s]"); ax.set_ylabel("tap [mV]")
                ax.set_title(f"Escalon de la etapa {ens['etapa']} a {ens['destino']:+d}")
                ax.legend(fontsize=8, ncol=4)
                fig.tight_layout()
                fig.savefig(SALIDA / f"escalon_e{ens['etapa']}_{ens['destino']:+d}.png",
                            dpi=140, facecolor=FONDO)
                plt.close(fig)
        elif d.get("experimento") == "curva":
            a = analizar_curva(d)
            if not a.get("puntos"):
                continue
            et = d["etapa"]
            fig, (a1, a2) = plt.subplots(1, 2, figsize=(11, 4), facecolor=FONDO)
            for ax in (a1, a2): ax.set_facecolor(FONDO)
            xs = [p["code"] for p in d["puntos"] if p.get(f"ch{et}") is not None]
            ys = [p[f"ch{et}"] / 1000.0 for p in d["puntos"] if p.get(f"ch{et}") is not None]
            a1.plot(xs, ys, "o-", ms=3, color=col[et])
            a1.set_xlabel("codigo de IDAC"); a1.set_ylabel(f"tap ch{et} [mV]")
            a1.set_title(f"Curva etapa {et} ({NOMBRE_TAP[et]})")
            a2.plot([x for x, _ in a["locales"]], [s for _, s in a["locales"]],
                    "o-", ms=3, color=col[et])
            a2.axhline(a["pendiente_uv_por_codigo"], ls="--", lw=1, color="#888")
            a2.set_xlabel("codigo de IDAC"); a2.set_ylabel("pendiente local [uV/codigo]")
            a2.set_title(f"No linealidad: razon max/min = {a['razon_no_linealidad']:.2f}")
            fig.tight_layout()
            fig.savefig(SALIDA / f"curva_e{et}.png", dpi=140, facecolor=FONDO)
            plt.close(fig)
    print(f"\n  figuras -> {SALIDA}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--entrada", default=str(ENTRADA))
    ap.add_argument("--figs", action="store_true")
    args = ap.parse_args()

    raiz = Path(args.entrada)
    archivos = sorted(raiz.glob("*.json")) if raiz.is_dir() else []
    if not archivos:
        print(f"No hay crudos en {raiz}.")
        print("Correr primero:  python medir_planta.py --port COM8 escalon")
        return 1

    datos = []
    for f in archivos:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"  {f.name}: no se pudo leer ({e})"); continue
        datos.append((f, d))
        exp = d.get("experimento")
        if exp == "escalon":
            imprimir_escalon(d)
        elif exp == "curva":
            imprimir_curva(d)
        elif exp == "matriz":
            imprimir_matriz(d)

    if args.figs:
        figuras(datos)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
