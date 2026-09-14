"""Donde deja de ser estable la cadena, y de que depende.

QUE SE SABE Y QUE FALTA

El 2026-09-13 quedo probado con un experimento de control que a PGA x50 y
PGAout x2 -ganancia conjunta 100- la cadena diverge SOLA: cinco minutos con los
cuatro IDAC congelados y ch2 se fue de -23 a +1.672 mV, ch0 y LPo contra el
riel. El mismo atractor, +1.672 mV, que ya habia aparecido en el primer barrido
de la tarde. No es el lazo de calibracion: es el punto de operacion.

Lo que falta saber es de que depende, y hay dos hipotesis con consecuencias
opuestas:

  A) Del PRODUCTO de las ganancias. Entonces hay un techo de ganancia total y
     da igual como se reparta entre PGA y PGAout: repartirla no sirve de nada.

  B) De que PGAout pase de x1. Entonces el techo es de esa etapa, y repartir
     la ganancia tampoco ayuda, pero el diagnostico y el arreglo son otros.

  C) De la ganancia de PGA sola, y PGAout no tiene nada que ver.

COMO SE SEPARAN

Barriendo PGA con PGAout fijo, y despues al reves. Si la divergencia aparece
siempre por encima del mismo PRODUCTO, es (A). Si aparece en cuanto PGAout pasa
de x1 con cualquier PGA, es (B). Si sigue al PGA solo, es (C).

Y no hace falta calibrar para medirlo. Lo que se mide es la VELOCIDAD DE DERIVA
de ch2, que es el tap que delata la divergencia: a x1 se mueve unos 30 mV por
minuto y cuando la cadena se va, cientos. Los IDAC se dejan en cero, que es un
estado neutro y reproducible, y lo unico que cambia entre punto y punto son las
dos ganancias.

El unico cuidado es no confundir "diverge" con "esta clavado": un tap contra el
riel tampoco se mueve. Por eso se informa aparte si el tap estaba en ventana.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from centrar_lpo import leer_promedio, poner_idac
from testbench.core.console import Console
from testbench.core.lab import GAIN_CODES, Lab
from testbench.core.session import Session

TAPS = (0, 1, 2, 3, 4)
NOMBRES = {0: "SEo", 1: "BPo", 2: "OPA_SUMo", 3: "SUMo", 4: "LPo"}
CANAL_TESTIGO = 2
#: Por encima de esto la cadena no esta derivando, se esta yendo. A x1 medido
#: son unos 30 mV/min; el caso divergente dio 1.664 mV en 45 s, o sea 2.200.
DERIVA_DIVERGENTE = 300.0


def leer_todo(lab: Lab) -> dict[int, tuple[float, bool]]:
    return {c: leer_promedio(lab, c, n=3) for c in TAPS}


def fila(etiqueta: str, lecturas: dict[int, tuple[float, bool]]) -> str:
    partes = [f"{etiqueta:>16}"]
    for c in TAPS:
        valor, ok = lecturas[c]
        partes.append(f"{valor:+9.1f}" + ("  " if ok else " *"))
    return " ".join(partes)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM8")
    ap.add_argument("--pgas", default="1,8,16,24,32,50")
    ap.add_argument("--pgaouts", default="1,2")
    ap.add_argument("--combos", default=None,
                    help="pares pga:pgaout separados por coma; si se da, "
                         "reemplaza al producto cartesiano de --pgas/--pgaouts")
    ap.add_argument("--idac", default="0,0,0,0")
    ap.add_argument("--observar", type=float, default=120.0,
                    help="segundos entre las dos lecturas de cada punto")
    ap.add_argument("--asentar", type=float, default=25.0)
    ap.add_argument("--salida", default=None)
    args = ap.parse_args()

    base = [int(x) for x in args.idac.split(",")]
    # El asentado corto fue lo que arruino el primer mapa: con 25 s las
    # lecturas median el transitorio del reset y daban "divergencias" de
    # 546 mV/min a PGA x1, que es imposible. La cadena tiene tau de 30 a 44 s
    # mas una cola larga, asi que hay que esperarla.
    pgas = [int(x) for x in args.pgas.split(",")]
    pgaouts = [int(x) for x in args.pgaouts.split(",")]
    if args.combos:
        pares = [tuple(int(v) for v in c.split(":"))
                 for c in args.combos.split(",")]
        pgas = sorted({p for p, _ in pares})
        pgaouts = sorted({o for _, o in pares})
    else:
        pares = [(p, o) for p in pgas for o in pgaouts]
    destino = Path(args.salida) if args.salida else None
    if destino:
        destino.parent.mkdir(parents=True, exist_ok=True)

    def registro(texto: str) -> None:
        print(texto, flush=True)
        if destino:
            with destino.open("a", encoding="utf-8") as fh:
                fh.write(texto + "\n")

    console = Console(args.port)
    console.open(wait_ready=True, timeout=25.0)
    lab = Lab(Session(console))
    resultados: list[tuple[int, int, float, bool, bool]] = []
    try:
        lab.set_adc_config(1)
        registro("IDAC congelados en " + " ".join(f"{c:+d}" for c in base))
        registro(f"Cada punto: {args.asentar:.0f} s de asentado, una lectura, "
                 f"{args.observar:.0f} s de espera y otra lectura.")
        registro("Nada se corrige. Lo que se mide es cuanto se mueve ch2 solo.")
        registro("")
        registro(" " * 16 + " ".join(f"{'ch'+str(c):>9}  " for c in TAPS))
        registro(" " * 16 + " ".join(f"{NOMBRES[c]:>9}  " for c in TAPS))

        for pga, pgaout in pares:
                total = pga * pgaout
                # Se vuelve al estado neutro antes de cada punto, para que lo
                # que le pase a uno no contamine al siguiente: ya se sabe que
                # en esta cadena el punto de trabajo depende del camino.
                lab.set_gain("pgaout", GAIN_CODES.index(1))
                lab.set_gain("pga", GAIN_CODES.index(1))
                for etapa, codigo in enumerate(base):
                    poner_idac(lab, etapa, codigo)
                time.sleep(args.asentar)

                lab.set_gain("pga", GAIN_CODES.index(pga))
                lab.set_gain("pgaout", GAIN_CODES.index(pgaout))
                time.sleep(3.0)
                leer_promedio(lab, CANAL_TESTIGO, n=1)   # la primera se tira
                antes = leer_todo(lab)
                registro("")
                registro(fila(f"x{pga}*x{pgaout}={total}", antes))
                time.sleep(args.observar)
                despues = leer_todo(lab)
                registro(fila(f"  +{args.observar:.0f}s", despues))

                deriva = {c: despues[c][0] - antes[c][0] for c in TAPS}
                por_minuto = deriva[CANAL_TESTIGO] * 60.0 / args.observar
                legible = antes[CANAL_TESTIGO][1] and despues[CANAL_TESTIGO][1]
                diverge = abs(por_minuto) > DERIVA_DIVERGENTE
                registro(fila("  deriva", {c: (deriva[c], True) for c in TAPS}))
                registro(f"                 ch2 se mueve {por_minuto:+.0f} "
                         f"mV/min"
                         + ("" if legible else "  (pero estuvo fuera de "
                                               "ventana: el numero no vale)")
                         + ("   DIVERGE" if diverge and legible else ""))
                resultados.append((pga, pgaout, por_minuto, legible, diverge))

        registro("")
        registro("=== RESUMEN: deriva de ch2 en mV por minuto ===")
        registro(f"{'PGA':>6} " + " ".join(f"{'x'+str(g):>12}"
                                           for g in pgaouts))
        for pga in pgas:
            celdas = []
            for pgaout in pgaouts:
                r = [x for x in resultados if x[0] == pga and x[1] == pgaout]
                if not r:
                    celdas.append(f"{'-':>12}")
                elif not r[0][3]:
                    celdas.append(f"{'fuera':>12}")
                else:
                    celdas.append(f"{r[0][2]:>+9.0f}"
                                  + ("  !!" if r[0][4] else "   "))
            registro(f"x{pga:<5} " + " ".join(celdas))
        registro("")
        registro("'fuera' quiere decir que ch2 no estuvo en ventana y por lo "
                 "tanto no se puede afirmar nada de ese punto: un tap clavado "
                 "tampoco se mueve. '!!' es divergencia.")

        estables = [(p, o, p * o) for p, o, _, leg, div in resultados
                    if leg and not div]
        if estables:
            mejor = max(estables, key=lambda x: x[2])
            registro(f"\\nLa ganancia conjunta mas alta que NO diverge es "
                     f"{mejor[2]} (PGA x{mejor[0]} por PGAout x{mejor[1]}).")
        return 0
    finally:
        try:
            lab.set_gain("pgaout", GAIN_CODES.index(1))
            lab.set_gain("pga", GAIN_CODES.index(1))
            for etapa in range(4):
                lab.set_idac(etapa, 0)
        except Exception:
            pass
        console.close()


if __name__ == "__main__":
    raise SystemExit(main())
