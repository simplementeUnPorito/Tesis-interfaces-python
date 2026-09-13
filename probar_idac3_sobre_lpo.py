"""Mide si IDAC3 mueve LPo, en el estado en que la placa este ahora.

IDAC3 es el actuador DEDICADO de la etapa pasabajos y el lazo cerrado nunca lo
uso. La calibracion vieja lo daba por inutil, pero esa conclusion se saco con LPo
clavado contra el riel, donde ningun actuador se ve.

Aca se barre su rango completo, los dos signos, sin suponer direccion, y se
informa cuanto movio LPo de verdad. Si mueve, es el actuador natural para el lazo
fino sobre LPo, porque es local y no perturba nada aguas arriba.
"""
from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from escala_banco import BANCO_VREF_MV, FACTOR, lectura_valida
from testbench.core.console import Console
from testbench.core.lab import GAIN_CODES, Lab
from testbench.core.session import Session

CANAL_LPO = 4


def real_mv(u): return (u / 1000.0 - BANCO_VREF_MV) * FACTOR


def leer(lab, ch):
    m = [p.mean_uv for _ in range(3)
         if (p := lab.measure_dc(ch, settle_sel=2)) is not None and p.ok]
    if not m:
        return None, False
    v = statistics.median(m)
    return real_mv(v), lectura_valida(v / 1000.0)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM8")
    ap.add_argument("--pga", type=int, default=50)
    ap.add_argument("--pgaout", type=int, default=4)
    ap.add_argument("--espera", type=float, default=4.0)
    ap.add_argument("--desde", type=int, default=-255)
    ap.add_argument("--hasta", type=int, default=255)
    ap.add_argument("--paso", type=int, default=51)
    ap.add_argument("--ida-y-vuelta", action="store_true",
                    help="recorre el rango en los dos sentidos para ver histeresis")
    args = ap.parse_args()

    console = Console(args.port)
    console.open(wait_ready=True, timeout=25.0)
    lab = Lab(Session(console))
    try:
        lab.set_adc_config(1)
        lab.set_gain("pga", GAIN_CODES.index(args.pga))
        lab.set_gain("pgaout", GAIN_CODES.index(args.pgaout))
        print("Se deja la cadena como esta y solo se mueve IDAC3.\n", flush=True)
        lecturas = []
        codigos = list(range(args.desde, args.hasta + 1, args.paso))
        if args.ida_y_vuelta:
            # La prueba de histeresis: el mismo codigo visitado subiendo y
            # bajando. Si la lectura difiere, el estado depende del camino y
            # ninguna caracterizacion punto a punto vale.
            codigos = codigos + codigos[-2::-1]
        subiendo = {}
        for indice, codigo in enumerate(codigos):
            lab.set_idac(3, codigo)
            time.sleep(args.espera)
            valor, valido = leer(lab, CANAL_LPO)
            marca = "   LEGIBLE" if valido else ""
            print(f"  IDAC3={codigo:+5d} -> LPo "
                  + ("sin lectura" if valor is None else f"{valor:+9.1f} mV")
                  + marca, flush=True)
            if valor is not None:
                lecturas.append((codigo, valor, valido))
                sentido = "sube" if indice < len(codigos) / 2 else "baja"
                subiendo.setdefault(codigo, {})[sentido] = valor
        lab.set_idac(3, 0)

        if args.ida_y_vuelta:
            print("\n=== IDA Y VUELTA ===", flush=True)
            print(f"{'IDAC3':>7} {'subiendo':>11} {'bajando':>11} {'dif':>10}")
            peor = 0.0
            for codigo in sorted(subiendo):
                par = subiendo[codigo]
                if "sube" in par and "baja" in par:
                    d = par["baja"] - par["sube"]
                    peor = max(peor, abs(d))
                    print(f"{codigo:+7d} {par['sube']:+11.1f} "
                          f"{par['baja']:+11.1f} {d:+10.1f}")
            print(f"\nPeor diferencia entre subir y bajar: {peor:.0f} mV")
            if peor > 200.0:
                print("El mismo código da resultados MUY distintos según de "
                      "dónde se venga.")
                print("OJO: eso puede ser histéresis real, o puede ser que la "
                      "espera por punto sea corta frente al tau de la cadena. "
                      "Las dos cosas se ven IGUAL. Sólo vale concluir "
                      "histéresis si la espera fue de varios tau.")
                print(f"En esta corrida la espera fue de {args.espera:.0f} s "
                      f"por punto.")
            else:
                print("Sin diferencia apreciable entre subir y bajar.")

        print("\n=== RESULTADO ===", flush=True)
        if len(lecturas) < 2:
            print("Sin lecturas suficientes.")
            return 1
        excursion = max(v for _, v, _ in lecturas) - min(v for _, v, _ in lecturas)
        # En ida y vuelta el primero y el ultimo codigo son el mismo, asi que la
        # pendiente extremo a extremo no existe. Se toma sobre el tramo de ida.
        subida = lecturas[:max(2, len(lecturas) // 2)]
        span = subida[-1][0] - subida[0][0]
        pendiente = ((subida[-1][1] - subida[0][1]) / span) if span else 0.0
        print(f"Excursion total de LPo en todo el rango: {excursion:.0f} mV")
        print(f"Pendiente media: {pendiente:+.2f} mV/código")
        print(f"Autoridad a fondo de escala: +-{abs(pendiente)*255:.0f} mV")
        # ESTAR EN LA VENTANA NO ES ESTAR VIVO.
        #
        # El veredicto anterior daba por bueno un barrido donde las dieciseis
        # lecturas eran +1878,x: dentro de la ventana, identicas entre si, o sea
        # clavadas contra un riel que cae adentro del rango medible. Un tap que
        # no cambia mientras su actuador recorre setenta y cinco codigos no esta
        # respondiendo, este donde este.
        legibles = [c for c, v, ok in lecturas if ok]
        vivos = []
        for i, (c, v, ok) in enumerate(lecturas):
            vecinos = [w for _, w, _ in lecturas[max(0, i-1):i+2]]
            if ok and (max(vecinos) - min(vecinos)) > 20.0:
                vivos.append(c)
        if vivos:
            print(f"Codigos donde LPo esta VIVO (legible y respondiendo): de "
                  f"{min(vivos):+d} a {max(vivos):+d}")
            print("\nIDAC3 SIRVE. Es el actuador natural del lazo fino sobre "
                  "LPo: es local y no perturba nada aguas arriba.")
        elif legibles:
            print(f"Hay {len(legibles)} lecturas dentro de la ventana pero "
                  "ninguna responde a su vecina: LPo esta CLAVADO, no centrado. "
                  "Ojo que uno de sus rieles cae DENTRO del rango medible, asi "
                  "que la ventana sola no lo delata.")
        elif excursion > 500.0:
            print("\nIDAC3 mueve LPo pero no alcanza a meterlo en rango desde "
                  "aca. Sirve como fino una vez que otro lo acerque.")
        else:
            print("\nIDAC3 no mueve LPo de forma util en este estado.")
        return 0
    finally:
        try:
            lab.set_gain("pgaout", 0)
            for e in range(4):
                lab.set_idac(e, 0)
        except Exception:
            pass
        console.close()


if __name__ == "__main__":
    raise SystemExit(main())
