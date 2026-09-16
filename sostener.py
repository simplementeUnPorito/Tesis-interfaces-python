"""Mantiene el punto con el PI corriendo, y mide si aguanta.

POR QUE ESTA PRUEBA ES LA QUE IMPORTA

Llegar a un punto no es sostenerlo. Esta cadena se corre sola entre 15 y 141 mV
por minuto -medido el 2026-09-12-, asi que una calibracion que converge y
termina pierde el punto en minutos. Es la explicacion mas probable de los PASS
del historial que despues no se reproducian: nadie los volvio a mirar diez
minutos mas tarde.

El diseno que fijo Elias es que el PI NO se apague. Queda corriendo con periodo
lento y se congela solo cuando arranca una adquisicion -lo cual ademas es
forzoso, porque el AMux es compartido y leer los taps obliga a sacarlo del canal
de captura-. Al maestro se le informa una sola cosa: en rango o fuera de rango.

QUE MIDE, Y CONTRA QUE

La prueba corre dos tramos sobre el MISMO punto y en la misma sesion:

  1. CONGELADO. Los cuatro codigos quietos. Mide cuanto se va la cadena sola.
  2. CON EL PI. Los mismos minutos, corrigiendo.

El primer tramo es el control, y sin el la prueba no dice nada: un lazo que no
hiciera nada y una cadena que casualmente no derivo esa tarde se ven exactamente
igual. Con el, la diferencia entre las dos excursiones es lo que el lazo aporta,
y es un numero presentable.

De cada tramo salen la excursion de LPo y la fraccion del tiempo dentro de
tolerancia; del segundo, ademas, cuanta correccion hubo que aplicar. Esa
correccion es el insumo del punto 5 de la arquitectura de campo: mide el
envejecimiento, que la fecha del perfil solo estima.

LOS ACTUADORES

Los rapidos: IDAC3 de fino y IDAC2 de grueso. Ninguno de los dos toca el
pasabanda, asi que el lazo corrige sin excitar la cola de 680 uF y el periodo
puede ser de decenas de segundos sin que eso lo limite.
"""
from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from centrar_lpo import CANAL_LPO, leer_promedio, poner_idac
from regulador_pi import ReguladorCascada
from testbench.core.console import Console
from testbench.core.lab import GAIN_CODES, Lab
from testbench.core.session import Session

NOMBRES = {0: "SEo", 1: "BPo", 2: "OPA_SUMo", 3: "SUMo", 4: "LPo"}
CANAL_SUMO = 3
#: Cuantos codigos del actuador fino se mueven de una vez cuando el tap esta
#: fuera de ventana y solo se conoce el signo del error. Con 6,8 mV por
#: codigo son unos 200 mV por paso: suficiente para salir en pocos pasos de
#: una saturacion de dos voltios, y poco para pasarse de largo.
PASO_RESCATE = 30

#: Respuesta minima que hace confiable una pendiente, en mV. Por debajo de esto
#: el ABBA esta midiendo ruido y deriva residual, no al actuador.
RESPUESTA_MINIMA_MV = 60.0


def medir_pendiente(lab: Lab, etapa: int, base: int, canal: int, espera: float,
                    registro, deltas=(2, 8, 32, 128)) -> float | None:
    """ABBA de escalon creciente: la pendiente de un actuador sobre un tap.

    El escalon no puede ser fijo. IDAC2 mueve LPo unos pocos mV por codigo a
    PGAout x1 y mas de cien a x8, asi que un delta que a x1 no se distingue del
    ruido a x8 clava el tap contra el riel y devuelve una pendiente falsa. Ese
    es exactamente el error que hizo figurar la autoridad de IDAC3 once veces
    por debajo de la real.

    Asi que se empieza chico y se agranda hasta que la respuesta se despegue del
    ruido, abandonando en cuanto una lectura se sale de ventana.
    """
    for delta in deltas:
        lecturas: dict[int, list[float]] = {}
        valido = True
        for codigo in (base, base + delta, base + delta, base):
            if not -255 <= codigo <= 255:
                valido = False
                break
            poner_idac(lab, etapa, codigo)
            time.sleep(espera)
            valor, vivo = leer_promedio(lab, canal, n=2)
            if not vivo:
                valido = False
                break
            lecturas.setdefault(codigo, []).append(valor)
        poner_idac(lab, etapa, base)
        time.sleep(espera)
        if not valido:
            registro(f"      IDAC{etapa} con delta {delta}: se salio de "
                     f"ventana; se abandona")
            return None
        salto = (statistics.fmean(lecturas[base + delta])
                 - statistics.fmean(lecturas[base]))
        if abs(salto) >= RESPUESTA_MINIMA_MV:
            registro(f"      IDAC{etapa} con delta {delta}: {salto:+.0f} mV, "
                     f"o sea {salto / delta:+.2f} mV/codigo")
            return salto / delta
        registro(f"      IDAC{etapa} con delta {delta}: solo {salto:+.0f} mV, "
                 f"indistinguible del ruido; se agranda")
    return None


def tramo(lab: Lab, minutos: float, periodo: float, tolerancia: float,
          reg: ReguladorCascada | None, codigos: list[int], titulo: str,
          registro, paso_rescate: int = PASO_RESCATE,
          espera_rescate: float = 0.0, vigilar_sumo: bool = False,
          objetivo_sumo_mv: float = 0.0,
          banda_sumo_mv: float = 1500.0,
          paso_rescate_sumo: int = 1) -> dict:
    """Corre un tramo y devuelve lo que se midio. reg=None es el congelado."""
    registro(f"\n--- {titulo}: {minutos:.0f} min, una lectura cada "
             f"{periodo:.0f} s ---")
    fin = time.time() + minutos * 60.0
    t0 = time.time()
    valores: list[float] = []
    dentro = pasos = 0
    c2, c3 = codigos[2], codigos[3]
    proximo_rescate = 0.0

    def estado_idac(paso2: int = 0, paso3: int = 0) -> str:
        """Tupla completa y efecto estimado del ultimo movimiento sobre LPo."""
        tupla = f"({codigos[0]:+d},{codigos[1]:+d},{c2:+d},{c3:+d})"
        if reg is None:
            return f"IDAC={tupla}"
        dv2 = paso2 * reg.ganancia_gruesa / 1000.0
        dv3 = paso3 * reg.ganancia_fina / 1000.0
        return (f"IDAC={tupla}  correccion=(IDAC2 d{paso2:+d} "
                f"{dv2:+.3f} V, IDAC3 d{paso3:+d} {dv3:+.3f} V, "
                f"total {dv2 + dv3:+.3f} V)")

    while time.time() < fin:
        if vigilar_sumo:
            sumo, sumo_vivo = leer_promedio(lab, CANAL_SUMO, n=3)
            sumo_controlable = (sumo_vivo and
                                abs(sumo - objetivo_sumo_mv) <= banda_sumo_mv)
        else:
            sumo, sumo_vivo, sumo_controlable = 0.0, True, True
        lpo, vivo = leer_promedio(lab, CANAL_LPO, n=3)
        pasos += 1
        if vivo and sumo_controlable:
            valores.append(lpo)
            if abs(lpo) <= tolerancia:
                dentro += 1
        if reg is not None and not sumo_controlable:
            # JERARQUIA DE SATURACION: IDAC3 no puede arreglar una etapa que
            # ya llega saturada desde PGAout. Primero se devuelve SUMo a la
            # franja desde la cual el pasabajos tiene autoridad, usando IDAC2.
            # La pendiente IDAC2->SUMo tiene signo opuesto a IDAC2->LPo porque
            # el pasabajos es inversor.
            k_sumo = reg.ganancia_gruesa / -4.8
            direccion = 1 if objetivo_sumo_mv - sumo > 0 else -1
            paso2_pedido = (paso_rescate_sumo if direccion * k_sumo > 0
                            else -paso_rescate_sumo)
            nuevo_c2 = max(-255, min(255, c2 + paso2_pedido))
            paso2 = nuevo_c2 - c2
            if paso2 and poner_idac(lab, 2, nuevo_c2, registro):
                c2 = nuevo_c2
                reg.codigo_grueso = c2
                reg.ultimo_bueno = None
            else:
                paso2 = 0
            proximo_rescate = 0.0
            registro(f"  t={time.time() - t0:6.0f}s  "
                     f"SUMo {sumo:+9.1f} mV"
                     + ("" if sumo_vivo else " FUERA")
                     + f"  LPo {lpo:+9.1f} mV"
                     + ("" if vivo else " FUERA")
                     + "   rescate PGAout con IDAC2   "
                     + estado_idac(paso2=paso2))
            time.sleep(periodo)
            continue
        if reg is not None and not vivo:
            # RESCATE POR SIGNO.
            #
            # El regulador se niega a integrar sobre una lectura invalida, y
            # hace bien: un tap comprimido contra el borde devuelve un error
            # constante y falso. Pero eso lo deja sin nada que hacer, y el
            # 2026-09-13 a las 19:10 se quedo diecisiete minutos mirando un LPo
            # clavado en +2.504 mV sin mover un codigo.
            #
            # De una lectura saturada se pierde el valor pero NO el signo, y el
            # signo alcanza para saber para donde ir. Asi que mientras este
            # afuera se avanza a pasos fijos en la direccion que reduce el
            # modulo, que es la biseccion de signo de siempre.
            paso2 = 0
            paso3 = 0
            ahora = time.time()
            if ahora < proximo_rescate:
                accion = ("esperando respuesta del rescate "
                          f"({proximo_rescate - ahora:.0f} s)")
            else:
                direccion_salida = -1 if lpo > 0 else 1
                paso = (paso_rescate if
                        direccion_salida * reg.ganancia_fina > 0
                        else -paso_rescate)
                nuevo_c3 = max(-255, min(255, c3 + paso))
                paso3 = nuevo_c3 - c3
                c3 = nuevo_c3
                if paso3:
                    poner_idac(lab, 3, c3)
                else:
                    # Si el fino ya llego al extremo correcto, un codigo del
                    # grueso aporta recorrido. Nunca se apilan pasos: tambien
                    # queda sujeto a la espera de respuesta de la planta.
                    paso_grueso = (
                        1 if direccion_salida * reg.ganancia_gruesa > 0 else -1)
                    nuevo_c2 = max(-255, min(255, c2 + paso_grueso))
                    paso2 = nuevo_c2 - c2
                    c2 = nuevo_c2
                    if paso2:
                        poner_idac(lab, 2, c2)
                reg.codigo_fino = c3
                reg.codigo_grueso = c2
                reg.ultimo_bueno = None
                proximo_rescate = ahora + espera_rescate
                accion = "rescate con espera de respuesta"
            registro(f"  t={time.time() - t0:6.0f}s  "
                     + (f"SUMo {sumo:+9.1f} mV  " if vigilar_sumo else "")
                     + f"LPo {lpo:+9.1f} mV"
                     f"   FUERA, {accion}   "
                     + estado_idac(paso2=paso2, paso3=paso3))
            time.sleep(periodo)
            continue
        if reg is not None:
            salida = reg.paso(lpo, vivo)
            if salida.paso_fino:
                poner_idac(lab, 3, salida.codigo_fino)
            if salida.paso_grueso:
                poner_idac(lab, 2, salida.codigo_grueso)
            c2, c3 = salida.codigo_grueso, salida.codigo_fino
        registro(f"  t={time.time() - t0:6.0f}s  "
                 + (f"SUMo {sumo:+9.1f} mV  " if vigilar_sumo else "")
                 + f"LPo {lpo:+9.1f} mV"
                 + ("" if vivo else "   FUERA")
                 + "   "
                 + estado_idac(
                     paso2=salida.paso_grueso if reg is not None else 0,
                     paso3=salida.paso_fino if reg is not None else 0))
        time.sleep(periodo)
    codigos[2], codigos[3] = c2, c3
    return {"titulo": titulo, "valores": valores, "dentro": dentro,
            "pasos": pasos}


def informar(r: dict, tolerancia: float, registro,
             minutos_de_gracia: float = 5.0, periodo: float = 20.0) -> bool:
    """Juzga el tramo SIN contar la convergencia inicial.

    El veredicto tiene que mirar el regimen, no el arranque. El 2026-09-14 a
    ganancia 64 el lazo partio de +245 mV y tardo seis minutos en llegar a
    cero; despues se quedo entre -12 y +35 mV durante nueve minutos. Contando
    todo el tramo daban 40 de 44 lecturas en tolerancia y el veredicto era "no
    se sostiene", que es reprobar a un regulador por no arrancar ya convergido.

    Se descartan las primeras ``minutos_de_gracia`` y se exige que TODO lo que
    viene despues este dentro de tolerancia.
    """
    if not r["valores"]:
        registro(f"{r['titulo']}: ninguna lectura valida; LPo estuvo fuera de "
                 f"ventana todo el tramo.")
        return False
    v = r["valores"]
    fuera = r["pasos"] - len(v)
    registro(f"{r['titulo']}:")
    registro(f"   LPo entre {min(v):+.1f} y {max(v):+.1f} mV, excursion "
             f"{max(v) - min(v):.1f} mV")
    registro(f"   dentro de +-{tolerancia:.0f} mV en {r['dentro']} de "
             f"{r['pasos']} lecturas ({100.0 * r['dentro'] / r['pasos']:.0f} %)"
             + (f", y {fuera} fuera de ventana" if fuera else ""))
    gracia = int(minutos_de_gracia * 60.0 / max(periodo, 1.0))
    regimen = v[gracia:]
    if len(regimen) < 3:
        registro("   el tramo es demasiado corto para juzgar el regimen")
        return r["dentro"] == r["pasos"]
    dentro_reg = sum(1 for x in regimen if abs(x) <= tolerancia)
    registro(f"   descontando los primeros {minutos_de_gracia:.0f} min de "
             f"convergencia: LPo entre {min(regimen):+.1f} y "
             f"{max(regimen):+.1f} mV, {dentro_reg} de {len(regimen)} en "
             f"tolerancia")
    return dentro_reg == len(regimen) and fuera == 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM8")
    ap.add_argument("--pga", type=int, default=50)
    ap.add_argument("--pgaout", type=int, default=1)
    ap.add_argument("--punto", required=True, help="idac0,idac1,idac2,idac3")
    ap.add_argument("--minutos", type=float, default=20.0,
                    help="duracion del tramo CON el PI")
    ap.add_argument("--minutos-congelado", type=float, default=10.0,
                    help="tramo de control previo, sin corregir; 0 lo saltea")
    ap.add_argument("--periodo", type=float, default=30.0)
    ap.add_argument("--tolerancia", type=float, default=200.0)
    ap.add_argument("--tau", type=float, default=34.0)
    ap.add_argument("--salida", default=None)
    args = ap.parse_args()

    base = [int(x) for x in args.punto.split(",")]
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
    try:
        lab.set_adc_config(1)
        lab.set_gain("pga", GAIN_CODES.index(args.pga))
        lab.set_gain("pgaout", GAIN_CODES.index(args.pgaout))
        for etapa, codigo in enumerate(base):
            poner_idac(lab, etapa, codigo)
        total = args.pga * args.pgaout
        registro(f"=== SOSTENER LA GANANCIA {total} "
                 f"(PGA x{args.pga} por PGAout x{args.pgaout}) ===")
        registro("Punto de partida IDAC " + " ".join(f"{c:+d}" for c in base))
        registro(f"Tolerancia sobre LPo: +-{args.tolerancia:.0f} mV")
        time.sleep(15.0)
        registro("\nTaps al empezar:")
        for canal in (0, 1, 2, 3, 4):
            v, ok = leer_promedio(lab, canal, n=3)
            registro(f"   ch{canal} ({NOMBRES[canal]:<8}) {v:+9.1f} mV"
                     + ("" if ok else "   FUERA DE VENTANA"))

        codigos = list(base)
        control = None
        if args.minutos_congelado > 0:
            control = tramo(lab, args.minutos_congelado, args.periodo,
                            args.tolerancia, None, codigos,
                            "CONGELADO (control, sin corregir)", registro)

        registro("\nMidiendo la autoridad de los actuadores rapidos en ESTE "
                 "punto y a ESTA ganancia:")
        k3 = medir_pendiente(lab, 3, codigos[3], CANAL_LPO, 4.0, registro)
        k2 = medir_pendiente(lab, 2, codigos[2], CANAL_LPO, 4.0, registro)
        if k3 is None:
            registro("   IDAC3 no da una pendiente medible aca; se usa la del "
                     "2026-09-13, 6,68 mV/codigo")
            k3 = 6.68
        if k2 is None:
            registro("   IDAC2 no da una pendiente medible aca; se lo estima "
                     "en veinte veces IDAC3")
            k2 = k3 * 20.0
        reg = ReguladorCascada(
            ganancia_fina=k3, ganancia_gruesa=k2, tau_s=args.tau,
            periodo_s=args.periodo, referencia_real_mv=0.0,
            tolerancia_real_mv=args.tolerancia,
            codigo_fino=codigos[3], codigo_grueso=codigos[2],
            limite_fino=255, zona_comoda=200)
        registro(f"   alpha = T/tau = {reg.alpha:.3f}")

        antes = list(codigos)
        prueba = tramo(lab, args.minutos, args.periodo, args.tolerancia, reg,
                       codigos, "CON EL PI", registro)

        registro("\n=== VEREDICTO ===")
        registro(f"Ganancia conjunta {total}")
        if control is not None:
            informar(control, args.tolerancia, registro,
                     periodo=args.periodo)
        bien = informar(prueba, args.tolerancia, registro,
                        periodo=args.periodo)
        registro(f"   correccion aplicada: IDAC3 de {antes[3]:+d} a "
                 f"{codigos[3]:+d} ({codigos[3] - antes[3]:+d} codigos), "
                 f"IDAC2 de {antes[2]:+d} a {codigos[2]:+d} "
                 f"({codigos[2] - antes[2]:+d})")
        if control and control["valores"] and prueba["valores"]:
            ec = max(control["valores"]) - min(control["valores"])
            ep = max(prueba["valores"]) - min(prueba["valores"])
            registro(f"   excursion: {ec:.0f} mV congelado en "
                     f"{args.minutos_congelado:.0f} min contra {ep:.0f} mV con "
                     f"el PI en {args.minutos:.0f} min")
        registro("\nTaps al terminar:")
        for canal in (0, 1, 2, 3, 4):
            v, ok = leer_promedio(lab, canal, n=3)
            registro(f"   ch{canal} ({NOMBRES[canal]:<8}) {v:+9.1f} mV"
                     + ("" if ok else "   FUERA DE VENTANA"))
        registro(f"\nLa ganancia {total} "
                 + ("SE SOSTIENE" if bien else "NO se sostiene")
                 + f" durante {args.minutos:.0f} minutos con el PI corriendo.")
        return 0 if bien else 1
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
