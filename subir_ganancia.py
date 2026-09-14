"""Calibra a una ganancia y despues SUBE PGAout sin soltar la cadena.

POR QUE ASI Y NO BUSCANDO DE CERO

Buscar el punto de trabajo desde cero a cada ganancia no funciona en esta placa:
el punto se corre mas que la ventana del sumador en decenas de minutos, asi que
para cuando la busqueda termina el blanco ya se movio. Medido el 2026-09-13: los
mismos cuatro codigos dieron ch2 en +7,4 mV a las 16:43 y en -4.973 a las 17:06.

Pero hay un camino que evita la busqueda entera, y ademas es el flujo de campo
descrito en ARQUITECTURA_CALIBRACION_CAMPO.md: se arma el tendido, se calibra,
se hace un disparo de prueba, se elige la ganancia, y esa eleccion tiene que
costar "un tiempo razonable", no una calibracion completa.

La clave es que al subir PGAout **el sumador no se toca**. IDAC0 e IDAC1, que son
los lentos y los que obligan a esperar el pasabanda, se quedan donde estan. Lo
unico que hay que recentrar es lo que la nueva ganancia desacomodo aguas abajo, y
para eso alcanzan IDAC2 e IDAC3, que asientan en milisegundos.

Asi que: calibrar UNA vez a la ganancia mas facil, y despues subir escalon por
escalon retocando solo con los rapidos.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from centrar_lpo import (CANAL_LPO, CANAL_SUMO, IDAC2_LIMITE, IDAC3_LIMITE,
                         TOLERANCIA_CH3_MV, bisectar, esperar_quieto,
                         leer_promedio, poner_idac)
from cerrar_lazo import CANAL_SUM
from perfil_placa import PerfilPlaca
from testbench.core.console import Console
from testbench.core.lab import GAIN_CODES, Lab
from testbench.core.session import Session

NOMBRES = {0: "SEo", 1: "BPo", 2: "OPA_SUMo", 3: "SUMo", 4: "LPo"}


def ya_esta_bien(lab: Lab, canal: int, tolerancia_mv: float,
                 registro) -> tuple[bool, float]:
    """Mira si el tap ya esta donde queremos ANTES de tocarlo.

    Sin esto, el 2026-09-13 a las 17:15 se perdio una calibracion que ya estaba
    hecha: al subir PGAout a x2, LPo quedo en +16,8 mV -centrado, muy dentro de
    la tolerancia de 200- y la biseccion, que empieza sondeando sus dos
    extremos, mando IDAC3 a -255 y lo tiro a -5.781.

    Una busqueda que arranca probando los extremos no puede correrse sobre un
    punto que ya sirve. Mirar primero cuesta tres segundos.
    """
    valor, vivo = leer_promedio(lab, canal, n=3)
    if vivo and abs(valor) <= tolerancia_mv:
        registro(f"   ch{canal} ya esta en {valor:+.1f} mV, dentro de "
                 f"{tolerancia_mv:.0f}: no se toca")
        return True, valor
    return False, valor


def foto(lab: Lab, registro) -> tuple[dict, bool]:
    estado, todos = {}, True
    for canal in (0, 1, 2, 3, 4):
        v, ok = leer_promedio(lab, canal, n=3)
        estado[canal] = (v, ok)
        todos = todos and ok
        registro(f"      ch{canal} ({NOMBRES[canal]:<8}) {v:+9.1f} mV"
                 + ("" if ok else "   FUERA"))
    return estado, todos


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM8")
    ap.add_argument("--pga", type=int, default=50)
    ap.add_argument("--desde", type=int, default=1, help="PGAout de partida")
    ap.add_argument("--hasta", default="2,4",
                    help="ganancias de PGAout a las que subir, en orden")
    ap.add_argument("--punto", default="0,164,-95,0",
                    help="el punto que cerro a la ganancia de partida")
    ap.add_argument("--espera", type=float, default=6.0)
    ap.add_argument("--tolerancia", type=float, default=200.0)
    args = ap.parse_args()

    base = [int(x) for x in args.punto.split(",")]
    console = Console(args.port)
    console.open(wait_ready=True, timeout=25.0)
    lab = Lab(Session(console))
    try:
        lab.set_adc_config(1)
        lab.set_gain("pga", GAIN_CODES.index(args.pga))
        lab.set_gain("pgaout", GAIN_CODES.index(args.desde))
        for etapa, codigo in enumerate(base):
            poner_idac(lab, etapa, codigo, print)
        print(f"1) PGA x{args.pga}, PGAout x{args.desde}, punto "
              + " ".join(f"{c:+d}" for c in base), flush=True)
        print("   esperando a que la cadena se aquiete", flush=True)
        esperar_quieto(lab, CANAL_SUM, print, maximo_s=200.0)
        foto(lab, print)

        codigo2, codigo3 = base[2], base[3]
        for ganancia in [int(x) for x in args.hasta.split(",")]:
            print(f"\n2) Sube PGAout a x{ganancia}. "
                  f"Ganancia conjunta {args.pga * ganancia}.", flush=True)
            lab.set_gain("pgaout", GAIN_CODES.index(ganancia))
            time.sleep(args.espera)

            # Solo los RAPIDOS. IDAC0 e IDAC1 se quedan donde estan: el sumador
            # no cambio, lo que cambio es lo que hay despues de PGAout.
            bien3, ch3 = ya_esta_bien(lab, CANAL_SUMO, TOLERANCIA_CH3_MV,
                                      print)
            if not bien3:
                print("   IDAC2 recentra SUMo", flush=True)
                codigo2, ch3, ch3_ok, motivo = bisectar(
                    lab, 2, CANAL_SUMO,
                    max(-IDAC2_LIMITE, codigo2 - 60),
                    min(IDAC2_LIMITE, codigo2 + 60),
                    args.espera, print, tolerancia_mv=TOLERANCIA_CH3_MV)
                print(f"   IDAC2={codigo2:+d} deja ch3 en {ch3:+.1f} mV",
                      flush=True)

            bien4, lpo = ya_esta_bien(lab, CANAL_LPO, args.tolerancia, print)
            if not bien4:
                print("   IDAC3 recentra LPo", flush=True)
                codigo3, lpo, lpo_ok, motivo3 = bisectar(
                    lab, 3, CANAL_LPO, -IDAC3_LIMITE, IDAC3_LIMITE,
                    args.espera, print, tolerancia_mv=args.tolerancia)
                print(f"   IDAC3={codigo3:+d} deja LPo en {lpo:+.1f} mV",
                      flush=True)

            print("   estado:", flush=True)
            estado, todos = foto(lab, print)
            lpo_v, lpo_vivo = estado[4]
            bien = todos and abs(lpo_v) <= args.tolerancia
            print(f"   >>> PGA x{args.pga} x PGAout x{ganancia} = "
                  f"{args.pga * ganancia}: "
                  + ("CALIBRA" if bien else "no llega")
                  + f", LPo {lpo_v:+.1f} mV", flush=True)
            if not bien:
                print("   no se sigue subiendo: las mayores exigen mas.",
                      flush=True)
                return 1
        return 0
    finally:
        try:
            lab.set_gain("pgaout", GAIN_CODES.index(1))
            for etapa in range(4):
                lab.set_idac(etapa, 0)
        except Exception:
            pass
        console.close()


if __name__ == "__main__":
    raise SystemExit(main())
