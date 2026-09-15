"""Ejecuta el ciclo GEO completo: fase lenta y PI rapido permanente.

La fase lenta aprende IDAC0/IDAC1 con PGAout x1, donde los taps se pueden
observar sin que la ganancia de salida los esconda contra un riel. Despues
carga el adelanto de IDAC2/IDAC3, conmuta a la ganancia nominal y entrega LPo
al regulador rapido que queda corriendo.
"""
from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from autocalibracion_dos_fases import (TwoPhaseCalibrator, load_history,
                                        save_history)
from centrar_lpo import CANAL_LPO, leer_promedio
from regulador_pi import ReguladorCascada
from sostener import informar, tramo
from sostener_directo import (NOMBRES, PENDIENTE_IDAC2_X24_MV,
                              PENDIENTE_IDAC3_MV)
from testbench.core.console import Console
from testbench.core.lab import GAIN_CODES, Lab
from testbench.core.session import Session


def par_rapido(texto: str) -> tuple[int, int]:
    try:
        valores = tuple(int(x.strip()) for x in texto.split(","))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "--punto-rapido debe ser idac2,idac3") from exc
    if len(valores) != 2 or any(not -255 <= x <= 255 for x in valores):
        raise argparse.ArgumentTypeError(
            "--punto-rapido requiere dos codigos entre -255 y +255")
    return valores[0], valores[1]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", default="COM8")
    ap.add_argument("--pga", type=int, default=4)
    ap.add_argument("--pgaout", type=int, default=24)
    ap.add_argument("--punto-rapido", type=par_rapido, default=(75, -87),
                    help="semilla IDAC2,IDAC3; por defecto 75,-87")
    ap.add_argument("--samples", type=int, default=1)
    ap.add_argument("--sostener", type=float, default=15.0,
                    help="minutos de PI rapido")
    ap.add_argument("--periodo", type=float, default=5.0)
    ap.add_argument("--tolerancia", type=float, default=200.0)
    ap.add_argument("--paso-rescate", type=int, default=80)
    ap.add_argument("--espera-rescate", type=float, default=40.0)
    ap.add_argument("--pendiente-idac3", type=float,
                    default=PENDIENTE_IDAC3_MV)
    ap.add_argument("--pendiente-idac2", type=float, default=None)
    ap.add_argument("--evidencia", type=Path, default=None)
    ap.add_argument("--restaurar-al-salir", action="store_true")
    args = ap.parse_args()

    if args.pga not in GAIN_CODES or args.pgaout not in GAIN_CODES:
        ap.error(f"ganancias validas: {', '.join(map(str, GAIN_CODES))}")
    if args.samples < 1:
        ap.error("--samples debe ser al menos 1")
    if args.sostener <= 0 or args.periodo <= 0:
        ap.error("--sostener y --periodo deben ser positivos")
    if not 1 <= args.paso_rescate <= 510:
        ap.error("--paso-rescate debe estar entre 1 y 510")
    if args.espera_rescate < 0:
        ap.error("--espera-rescate no puede ser negativo")

    evidencia = args.evidencia or (
        Path(__file__).resolve().parents[3] / "lab" / "calibracion_nueva" /
        f"ciclo_completo_{datetime.now():%Y%m%d_%H%M%S}.json")
    console = Console(args.port)
    console.open(wait_ready=True, timeout=25.0)
    lab = Lab(Session(console))
    cal = TwoPhaseCalibrator(lab, evidencia, samples=args.samples)
    codigos = [0, 0, 0, 0]

    def registro(texto: str) -> None:
        print(texto, flush=True)

    try:
        registro(f"=== FASE LENTA: aprender IDAC0/IDAC1 con PGA x{args.pga}, "
                 "PGAout x1 ===")
        historia = load_history()
        fase1 = cal.fase1_margen(historia, pga=args.pga)
        if not fase1["pass"]:
            registro("La fase lenta no encontro un punto verificable; no se "
                     "entrega una planta inestable al PI rapido.")
            return 1

        historia["phase1"] = {
            "codes": list(cal.codes[:2]),
            "pga": args.pga,
            "tau_s": fase1.get("tau_s"),
            "verified_at": datetime.now().isoformat(),
            "puntaje": fase1["puntaje"],
            "final": fase1["verified"],
            "source": "fase lenta medida por ciclo completo",
        }
        save_history(historia)
        codigos = list(cal.codes)
        registro("FASE LENTA PASS: IDAC0/IDAC1 aprendidos = "
                 f"({codigos[0]:+d},{codigos[1]:+d})")

        # Los actuadores rapidos se precargan todavia en x1. Asi, al conmutar
        # PGAout, la ganancia final ya encuentra aplicada su mejor semilla.
        codigos[2], codigos[3] = args.punto_rapido
        cal.set_code(2, codigos[2])
        cal.set_code(3, codigos[3])
        if not lab.set_gain("pgaout", GAIN_CODES.index(args.pgaout)):
            raise RuntimeError(f"sin ACK PGAout x{args.pgaout}")
        cal.current_gain = args.pgaout

        registro(f"=== FASE RAPIDA: PGA x{args.pga}, PGAout x{args.pgaout}, "
                 f"ganancia conjunta {args.pga * args.pgaout} ===")
        registro("IDAC=(" + ",".join(f"{c:+d}" for c in codigos) + ")")
        lpo_vivo = False
        for canal in range(5):
            valor, vivo = leer_promedio(lab, canal, n=3)
            registro(f"   ch{canal} ({NOMBRES[canal]:<8}) {valor:+9.1f} mV"
                     + ("" if vivo else "   FUERA DE VENTANA"))
            if canal == CANAL_LPO:
                lpo_vivo = vivo
        if not lpo_vivo:
            registro("LPo fuera de ventana: entra al rescate con espera de "
                     "respuesta y luego al PI normal.")

        k2 = (args.pendiente_idac2 if args.pendiente_idac2 is not None else
              PENDIENTE_IDAC2_X24_MV * args.pgaout / 24.0)
        reg = ReguladorCascada(
            ganancia_fina=args.pendiente_idac3,
            ganancia_gruesa=k2,
            tau_s=max(10.0, args.periodo * 2.0),
            periodo_s=args.periodo,
            referencia_real_mv=0.0,
            tolerancia_real_mv=args.tolerancia,
            codigo_fino=codigos[3], codigo_grueso=codigos[2],
            limite_fino=255, zona_comoda=200)
        resultado = tramo(
            lab, args.sostener, args.periodo, args.tolerancia, reg, codigos,
            f"PI RAPIDO a ganancia {args.pga * args.pgaout}", registro,
            paso_rescate=args.paso_rescate,
            espera_rescate=args.espera_rescate)
        sostuvo = informar(resultado, args.tolerancia, registro,
                           periodo=args.periodo)
        cal.codes[:] = codigos
        cal.event("ciclo_completo",
                  "Ciclo completo: " + ("PASS" if sostuvo else "FAIL"),
                  fase1=fase1, pi=resultado, codes=list(codigos))
        registro("IDAC final=(" + ",".join(f"{c:+d}" for c in codigos)
                 + ")")
        return 0 if sostuvo else 1
    finally:
        if args.restaurar_al_salir:
            try:
                lab.set_gain("pgaout", GAIN_CODES.index(1))
                for etapa in range(4):
                    lab.set_idac(etapa, 0)
            except Exception:
                pass
            registro("Se restauro PGAout x1 e IDAC=0.")
        else:
            registro("Se dejan aplicados la ganancia y los ultimos IDAC.")
        cal.save()
        console.close()
        registro(f"Evidencia: {evidencia}")


if __name__ == "__main__":
    raise SystemExit(main())
