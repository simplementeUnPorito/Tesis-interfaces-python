"""Ejecuta el ciclo GEO completo en la ganancia nominal.

IDAC2/IDAC3 quedan congelados en su semilla mientras se identifica la fase
lenta. IDAC0/IDAC1 sólo se aceptan si dejan SUMo controlable en la misma
ganancia que luego usará el PI rápido. No hay conmutación intermedia a x1.
"""
from __future__ import annotations

import argparse
import time
from datetime import datetime
from pathlib import Path

from autocalibracion_dos_fases import (MARGEN_FASE1_MV, TwoPhaseCalibrator,
                                        load_history, margen_riel_real_mv,
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
    ap.add_argument("--punto-rapido", type=par_rapido, default=(0, 0),
                    help="semilla IDAC2,IDAC3; por defecto 0,0 para máximo rango")
    ap.add_argument("--tau-lento", type=float, default=43.7,
                    help="tau DC medida del BP; por defecto 43.7 s")
    ap.add_argument("--pendiente-dc-idac0", type=float, default=4.296,
                    help="IDAC0->SEo asentado, mV/codigo")
    ap.add_argument("--pendiente-dc-idac1", type=float, default=1.75,
                    help="IDAC1->BPo asentado, mV/codigo")
    ap.add_argument("--objetivo-opasum", type=float, default=-270.0,
                    help="OPA_SUM que cancela el offset referido de PGAout")
    ap.add_argument("--objetivo-sumo", type=float, default=0.0,
                    help="salida de PGAout deseada respecto de Vref")
    ap.add_argument("--banda-sumo", type=float, default=500.0)
    ap.add_argument("--samples", type=int, default=1)
    ap.add_argument("--sostener", type=float, default=15.0,
                    help="minutos de PI rapido")
    ap.add_argument("--periodo", type=float, default=5.0)
    ap.add_argument("--tolerancia", type=float, default=200.0)
    ap.add_argument("--banda-muerta", type=float, default=100.0,
                    help="no mover el PI dentro de +/- este valor en mV")
    ap.add_argument("--kp-relativo", type=float, default=0.25,
                    help="peso del término e[k]-e[k-1]; antes era 1.0")
    ap.add_argument("--tau-pi", type=float, default=30.0,
                    help="tau usada para alpha=T/tau del PI rápido")
    ap.add_argument("--max-paso-pi", type=int, default=30,
                    help="máximo salto de IDAC3 con una lectura válida")
    ap.add_argument("--banda-sumo-rescate", type=float, default=1500.0,
                    help="IDAC2 sólo rescata SUMo fuera de esta banda")
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
    if not 0 <= args.banda_muerta <= args.tolerancia:
        ap.error("--banda-muerta debe estar entre 0 y --tolerancia")
    if args.kp_relativo < 0 or args.tau_pi <= 0 or args.max_paso_pi < 1:
        ap.error("sintonía PI inválida")
    if args.banda_sumo_rescate <= 0:
        ap.error("--banda-sumo-rescate debe ser positiva")
    if not 1 <= args.paso_rescate <= 510:
        ap.error("--paso-rescate debe estar entre 1 y 510")
    if args.espera_rescate < 0:
        ap.error("--espera-rescate no puede ser negativo")
    if args.tau_lento <= 0:
        ap.error("--tau-lento debe ser positivo")
    if abs(args.pendiente_dc_idac0) < 0.1 or abs(args.pendiente_dc_idac1) < 0.1:
        ap.error("las pendientes DC no pueden ser cero")

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

    def esperar_lento(etapa: int, etiqueta: str) -> None:
        """Espera al menos 2 tau y exige que la cola realmente se detenga."""
        duracion_min = 2.0 * args.tau_lento
        duracion_max = 4.0 * args.tau_lento
        inicio = time.monotonic()
        historial_estabilidad: list[tuple[float, float]] = []
        registro(f"{etiqueta}: mínimo {duracion_min:.1f} s, máximo "
                 f"{duracion_max:.1f} s; termina con BPo y OPA_SUM quietos")
        while True:
            transcurrido = time.monotonic() - inicio
            if transcurrido >= duracion_max:
                registro("  fin por máximo de 4 tau")
                break
            demora = min(5.0, duracion_max - transcurrido)
            time.sleep(demora)
            puntos = {ch: cal.read(ch, etiqueta, etapa, cal.codes[etapa],
                                   require_valid=False)
                      for ch in (1, 2, 3)}
            registro(f"  t={time.monotonic()-inicio:5.1f}s  "
                     f"BPo={puntos[1].error_real_mv:+8.1f}  "
                     f"OPA_SUM={puntos[2].error_real_mv:+8.1f}  "
                     f"SUMo={puntos[3].error_real_mv:+8.1f} mV")
            historial_estabilidad.append((puntos[1].error_real_mv,
                                           puntos[2].error_real_mv))
            if (time.monotonic()-inicio >= duracion_min and
                    len(historial_estabilidad) >= 4):
                ultimas = historial_estabilidad[-4:]
                rango_bp = max(x[0] for x in ultimas)-min(x[0] for x in ultimas)
                rango_opa = max(x[1] for x in ultimas)-min(x[1] for x in ultimas)
                if rango_bp <= 5.0 and rango_opa <= 10.0:
                    registro(f"  cola quieta: ΔBPo={rango_bp:.1f} mV, "
                             f"ΔOPA_SUM={rango_opa:.1f} mV en 4 muestras")
                    break

    try:
        registro(f"=== FASE LENTA NOMINAL: PGA x{args.pga}, "
                 f"PGAout x{args.pgaout} ===")
        historia = load_history()
        if not lab.set_adc_config(1):
            raise RuntimeError("sin ACK ADC +/-2,5 V")
        if not lab.set_gain("pga", GAIN_CODES.index(args.pga)):
            raise RuntimeError(f"sin ACK PGA x{args.pga}")
        if not lab.set_gain("pgaout", GAIN_CODES.index(args.pgaout)):
            raise RuntimeError(f"sin ACK PGAout x{args.pgaout}")
        cal.current_gain = args.pgaout
        cal.phase1_pga = args.pga
        codigos = [0, 0, args.punto_rapido[0], args.punto_rapido[1]]
        for etapa, codigo in enumerate(codigos):
            cal.set_code(etapa, codigo)

        registro("Punto inicial nominal IDAC=(" +
                 ",".join(f"{c:+d}" for c in codigos) + ")")
        esperar_lento(1, "Asentamiento inicial")
        inicial = cal.read_five_taps("lento nominal inicial", robust=True)
        for canal, punto in inicial.items():
            registro(f"   ch{canal} ({NOMBRES[canal]:<8}) "
                     f"{punto.error_real_mv:+9.1f} mV" +
                     ("" if punto.valid and not punto.clavado else
                      "   FUERA/CLAVADO"))

        base = inicial
        margen0_base = margen_riel_real_mv(base[0])
        margen1_base = margen_riel_real_mv(base[1])

        # Modelo DC del sumador inversor:
        # OPA_SUM = Vdsum - (27/6.8)*SEo - (27/7.48)*BPo.
        # La medición base ya contiene Vdsum, por eso sólo hacen falta los
        # incrementos que provocarán IDAC0 e IDAC1.
        a = 27.0 / 6.8
        b = 27.0 / 7.48
        k0_opasum = -a * args.pendiente_dc_idac0
        k1_opasum = -b * args.pendiente_dc_idac1
        correccion_necesaria = args.objetivo_opasum-base[2].error_real_mv

        # Hay dos grados de libertad para una ecuación. Busca sobre los códigos
        # enteros la solución de mínima norma L2, conservando margen previsto
        # en ambos taps directos. Primero exige residuo menor a 5 mV; dentro de
        # esa banda gana el par que menos esfuerzo cuadrático total requiere.
        candidatos = []
        for target0 in range(-255, 256):
            delta0 = target0-cal.codes[0]
            margen0_pred = margen0_base-abs(
                args.pendiente_dc_idac0*delta0)
            if margen0_pred < MARGEN_FASE1_MV:
                continue
            for target1 in range(-255, 256):
                delta1 = target1-cal.codes[1]
                margen1_pred = margen1_base-abs(
                    args.pendiente_dc_idac1*delta1)
                if margen1_pred < MARGEN_FASE1_MV:
                    continue
                aporte = k0_opasum*delta0+k1_opasum*delta1
                residuo = abs(correccion_necesaria-aporte)
                esfuerzo_l2 = delta0*delta0+delta1*delta1
                candidatos.append((residuo, esfuerzo_l2,
                                   max(abs(delta0), abs(delta1)),
                                   target0, target1, aporte,
                                   margen0_pred, margen1_pred))
        precisos = [c for c in candidatos if c[0] <= 5.0]
        if precisos:
            elegido = min(precisos, key=lambda c: (c[1], c[2], c[0]))
        elif candidatos:
            elegido = min(candidatos, key=lambda c: (c[0], c[1], c[2]))
        else:
            registro("No existe combinación IDAC0/IDAC1 con el margen pedido.")
            return 1
        (residuo_modelo, esfuerzo_l2, _, target0, target1, aporte_modelo,
         margen0_pred, margen1_pred) = elegido
        delta0, delta1 = target0-cal.codes[0], target1-cal.codes[1]
        registro("Modelo DC: OPA_SUM = Vdsum - 3.971*SEo - 3.610*BPo")
        registro(f"Pendientes sobre OPA_SUM: IDAC0 {k0_opasum:+.3f}, "
                 f"IDAC1 {k1_opasum:+.3f} mV/codigo")
        registro(f"Prediccion lenta: OPA_SUM {base[2].error_real_mv:+.1f} -> "
                 f"{args.objetivo_opasum:+.1f} mV; "
                 f"IDAC=({cal.codes[0]:+d},{cal.codes[1]:+d}) -> "
                 f"({target0:+d},{target1:+d})")
        registro(f"Solucion entera de minimo esfuerzo: "
                 f"dIDAC=({delta0:+d},{delta1:+d}), "
                 f"||dIDAC||={esfuerzo_l2**0.5:.1f}, "
                 f"aporte={aporte_modelo:+.1f} mV, "
                 f"residuo modelado={residuo_modelo:.1f} mV")
        registro(f"Margenes conservadores previstos: SEo {margen0_pred:.1f}, "
                 f"BPo {margen1_pred:.1f} mV")
        if target0 != cal.codes[0]:
            cal.set_code(0, target0)
        if target1 != cal.codes[1]:
            cal.set_code(1, target1)
        if target0 != 0 or target1 != 0:
            esperar_lento(1 if target1 != 0 else 0,
                           "Asentamiento del salto calculado")
        verificado = cal.read_five_taps("handoff nominal verificado", robust=True)
        codigos = list(cal.codes)
        margen0 = margen_riel_real_mv(verificado[0])
        margen1 = margen_riel_real_mv(verificado[1])
        handoff_ok = bool(
            verificado[0].valid and not verificado[0].clavado and
            margen0 >= MARGEN_FASE1_MV and
            verificado[1].valid and not verificado[1].clavado and
            margen1 >= MARGEN_FASE1_MV and
            verificado[2].valid and not verificado[2].clavado and
            abs(verificado[2].error_real_mv-args.objetivo_opasum) <=
            args.banda_sumo and
            verificado[3].valid and not verificado[3].clavado and
            abs(verificado[3].error_real_mv-args.objetivo_sumo) <=
            args.banda_sumo)
        fase1 = {
            "pass": handoff_ok, "codes": list(codigos),
            "tau_s": args.tau_lento,
            "pendiente_dc_idac0": args.pendiente_dc_idac0,
            "pendiente_dc_idac1": args.pendiente_dc_idac1,
            "idac0_opasum_real_mv_per_code": k0_opasum,
            "idac1_opasum_real_mv_per_code": k1_opasum,
            "objetivo_opasum_real_mv": args.objetivo_opasum,
            "objetivo_sumo_real_mv": args.objetivo_sumo,
            "banda_sumo_real_mv": args.banda_sumo,
            "initial": {ch: vars(p) for ch, p in inicial.items()},
            "verified": {ch: vars(p) for ch, p in verificado.items()},
            "margenes_real_mv": {"ch0": margen0, "ch1": margen1},
        }
        cal.event("phase1_nominal_pass" if handoff_ok else
                  "phase1_nominal_fail",
                  f"Fase lenta nominal {'PASS' if handoff_ok else 'FAIL'}: "
                  f"IDAC0={codigos[0]:+d} IDAC1={codigos[1]:+d}, "
                  f"SUMo={verificado[3].error_real_mv:+.1f} mV",
                  result=fase1)
        if not handoff_ok:
            registro("No se inicia el PI: el par lento no dejó SUMo dentro "
                     "de la banda nominal verificable.")
            return 1

        historia["phase1"] = {
            "codes": list(codigos[:2]), "pga": args.pga,
            "pgaout": args.pgaout, "tau_s": args.tau_lento,
            "verified_at": datetime.now().isoformat(),
            "final": fase1["verified"],
            "source": "aprendizaje y verificacion en ganancia nominal",
        }
        save_history(historia)
        registro("FASE LENTA PASS: IDAC0/IDAC1 aprendidos = "
                 f"({codigos[0]:+d},{codigos[1]:+d})")

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
            tau_s=args.tau_pi,
            periodo_s=args.periodo,
            referencia_real_mv=0.0,
            tolerancia_real_mv=args.tolerancia,
            codigo_fino=codigos[3], codigo_grueso=codigos[2],
            limite_fino=255, zona_comoda=200,
            kp_relativo=args.kp_relativo,
            banda_muerta_real_mv=args.banda_muerta,
            max_paso_fino=args.max_paso_pi)
        registro(f"PI: alpha={reg.alpha:.3f}, Kp_relativo={args.kp_relativo:.2f}, "
                 f"banda muerta=+/-{args.banda_muerta:.0f} mV, "
                 f"max paso IDAC3={args.max_paso_pi}")
        resultado = tramo(
            lab, args.sostener, args.periodo, args.tolerancia, reg, codigos,
            f"PI RAPIDO a ganancia {args.pga * args.pgaout}", registro,
            paso_rescate=args.paso_rescate,
            espera_rescate=args.espera_rescate, vigilar_sumo=True,
            banda_sumo_mv=args.banda_sumo_rescate,
            paso_rescate_sumo=1)
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
