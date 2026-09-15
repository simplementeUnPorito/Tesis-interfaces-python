"""Aplica un punto conocido en la ganancia final y arranca el PI enseguida.

Este runner es para repetir y observar un punto que ya fue identificado en el
banco. No caracteriza la placa, no pasa por PGAout x1 y no mide pendientes. El
procedimiento pesado sigue viviendo en ``saltar_ganancia.py``; aca las
pendientes se cargan por linea de comandos y tienen valores por defecto para el
punto validado PGA x4 / PGAout x24 de la placa geo-01.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from centrar_lpo import CANAL_LPO, leer_promedio, poner_idac
from regulador_pi import ReguladorCascada
from sostener import informar, tramo
from testbench.core.console import Console
from testbench.core.lab import GAIN_CODES, Lab
from testbench.core.session import Session


# Medidas representativas de las corridas del 2026-09-15. IDAC3 entra despues
# de PGAout y mueve LPo unos +7 mV/codigo. IDAC2 dio unos -58,4 mV/codigo a x1;
# escalado a x24 son aproximadamente -1.402 V/codigo.
PENDIENTE_IDAC3_MV = 7.0
PENDIENTE_IDAC2_X24_MV = -1400.0
NOMBRES = {0: "SEo", 1: "BPo", 2: "OPA_SUMo", 3: "SUMo", 4: "LPo"}


def punto_desde_texto(texto: str) -> list[int]:
    try:
        codigos = [int(x.strip()) for x in texto.split(",")]
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "--punto debe contener cuatro enteros separados por coma") from exc
    if len(codigos) != 4 or any(not -255 <= c <= 255 for c in codigos):
        raise argparse.ArgumentTypeError(
            "--punto requiere cuatro codigos entre -255 y +255")
    return codigos


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Carga ganancia y punto finales y sostiene LPo con el PI")
    ap.add_argument("--port", default="COM8")
    ap.add_argument("--pga", type=int, required=True)
    ap.add_argument("--pgaout", type=int, required=True)
    ap.add_argument("--punto", type=punto_desde_texto, required=True,
                    help="idac0,idac1,idac2,idac3 del punto final")
    ap.add_argument("--sostener", type=float, default=15.0,
                    help="minutos con el PI corriendo")
    ap.add_argument("--periodo", type=float, default=15.0,
                    help="segundos entre correcciones")
    ap.add_argument("--paso-rescate", type=int, default=80,
                    help="codigos de IDAC3 por paso mientras LPo esta fuera "
                         "de ventana (por defecto: 80)")
    ap.add_argument("--espera-rescate", type=float, default=40.0,
                    help="segundos sin acumular otra orden tras cada rescate "
                         "(por defecto: 40)")
    ap.add_argument("--tolerancia", type=float, default=200.0)
    ap.add_argument("--pendiente-idac3", type=float,
                    default=PENDIENTE_IDAC3_MV,
                    help="mV de LPo por codigo de IDAC3, con signo")
    ap.add_argument("--pendiente-idac2", type=float, default=None,
                    help="mV de LPo por codigo de IDAC2, con signo; por defecto "
                         "-1400 para x24 y proporcional para otra PGAout")
    ap.add_argument("--salida", default=None)
    ap.add_argument("--restaurar-al-salir", action="store_true",
                    help="vuelve a PGAout x1 e IDAC=0 al terminar")
    args = ap.parse_args()

    if args.pga not in GAIN_CODES or args.pgaout not in GAIN_CODES:
        ap.error(f"ganancias validas: {', '.join(map(str, GAIN_CODES))}")
    if args.sostener <= 0 or args.periodo <= 0:
        ap.error("--sostener y --periodo deben ser positivos")
    if not 1 <= args.paso_rescate <= 510:
        ap.error("--paso-rescate debe estar entre 1 y 510")
    if args.espera_rescate < 0:
        ap.error("--espera-rescate no puede ser negativo")
    if not args.pendiente_idac3:
        ap.error("--pendiente-idac3 no puede ser cero")

    k2 = (args.pendiente_idac2 if args.pendiente_idac2 is not None
          else PENDIENTE_IDAC2_X24_MV * args.pgaout / 24.0)
    if not k2:
        ap.error("--pendiente-idac2 no puede ser cero")

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
    codigos = list(args.punto)
    try:
        # Configuracion final desde el primer instante. No se pasa por x1 ni se
        # inserta una espera de asentamiento entre ganancia y punto.
        lab.set_adc_config(1)
        lab.set_gain("pga", GAIN_CODES.index(args.pga))
        lab.set_gain("pgaout", GAIN_CODES.index(args.pgaout))
        for etapa, codigo in enumerate(codigos):
            poner_idac(lab, etapa, codigo, registro)

        total = args.pga * args.pgaout
        registro(f"=== ARRANQUE DIRECTO: PGA x{args.pga}, PGAout x"
                 f"{args.pgaout}, ganancia conjunta {total} ===")
        registro("IDAC=(" + ",".join(f"{c:+d}" for c in codigos) + ")")
        registro(f"Pendientes cargadas: IDAC3 {args.pendiente_idac3:+.2f}, "
                 f"IDAC2 {k2:+.1f} mV/codigo sobre LPo")
        registro(f"Rescate rapido: IDAC3 de a {args.paso_rescate} codigos; "
                 "si llega al limite, IDAC2 de a 1 codigo; "
                 f"espera {args.espera_rescate:g} s entre movimientos")

        registro("Estado inmediatamente despues del adelanto:")
        lpo_vivo = False
        for canal in (0, 1, 2, 3, 4):
            valor, vivo = leer_promedio(lab, canal, n=3)
            registro(f"   ch{canal} ({NOMBRES[canal]:<8}) {valor:+9.1f} mV"
                     + ("" if vivo else "   FUERA DE VENTANA"))
            if canal == CANAL_LPO:
                lpo_vivo = vivo

        if not lpo_vivo:
            registro("LPo arranco fuera de ventana. Se entra igualmente al "
                     "control: primero usara el rescate por signo y el PI "
                     "normal comenzara apenas la lectura vuelva a ser valida.")

        reg = ReguladorCascada(
            ganancia_fina=args.pendiente_idac3,
            ganancia_gruesa=k2,
            tau_s=max(10.0, args.periodo * 2.0),
            periodo_s=args.periodo,
            referencia_real_mv=0.0,
            tolerancia_real_mv=args.tolerancia,
            codigo_fino=codigos[3], codigo_grueso=codigos[2],
            limite_fino=255, zona_comoda=200)
        registro(f"PI arrancando ya: alpha={reg.alpha:.3f}, una correccion "
                 f"cada {args.periodo:g} s durante {args.sostener:g} min")

        resultado = tramo(
            lab, args.sostener, args.periodo, args.tolerancia, reg, codigos,
            f"PI DIRECTO a ganancia {total}", registro,
            paso_rescate=args.paso_rescate,
            espera_rescate=args.espera_rescate)
        sostuvo = informar(resultado, args.tolerancia, registro,
                           periodo=args.periodo)
        registro("IDAC final=(" + ",".join(f"{c:+d}" for c in codigos)
                 + ")")
        registro(f">>> ganancia {total}: "
                 + ("SE SOSTIENE" if sostuvo else "NO se sostiene"))
        return 0 if sostuvo else 1
    finally:
        if args.restaurar_al_salir:
            try:
                lab.set_gain("pgaout", GAIN_CODES.index(1))
                for etapa in range(4):
                    lab.set_idac(etapa, 0)
            except Exception:
                pass
            print("Se restauro PGAout x1 e IDAC=0.", flush=True)
        else:
            print("Se deja la placa con la ganancia y los ultimos IDAC puestos.",
                  flush=True)
        console.close()


if __name__ == "__main__":
    raise SystemExit(main())
