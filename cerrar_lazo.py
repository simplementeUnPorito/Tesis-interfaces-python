"""Cierra la calibracion con el regulador PI. Es la culminacion, no un extra.

La division de trabajo:

- `autocalibracion_dos_fases.py` hace el **adelanto a lazo abierto**. Su trabajo
  es dejar la cadena dentro de rango y razonablemente cerca. No tiene que ser
  exacto: para eso esta lo que sigue.
- Este modulo **cierra el lazo** y lo mantiene cerrado. Rechaza la deriva, no
  necesita un modelo confiable y no le importa por que camino llego la planta.

Lo unico que el lazo no perdona es el SIGNO de cada actuador, asi que se mide en
placa antes de arrancar en vez de suponerse.

Uso:

    py -3 cerrar_lazo.py --port COM8 --pgaout 4 --minutos 20

Deja un CSV con la evolucion completa para poder dibujarla despues.
"""
from __future__ import annotations

import argparse
import csv
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from escala_banco import BANCO_VREF_MV, FACTOR, lectura_valida
from autocalibracion_dos_fases import TwoPhaseCalibrator
from regulador_pi import (ETAPA_FINA, ETAPA_GRUESA, ReguladorCascada,
                          tolerancia_de_ganancia)
from testbench.core.console import Console
from testbench.core.lab import GAIN_CODES, Lab
from testbench.core.session import Session

SALIDA = Path(__file__).resolve().parents[3] / "lab" / "calibracion_nueva"
CANAL_LPO = 4
#: Cuanto mas grande es la respuesta ASENTADA que la que devuelve un
#: sondeo de 1,5 tau, para los actuadores que pasan por el pasabanda.
#: Medido el 2026-09-13: 72 mV por codigo al instante contra unos 540
#: en regimen, en IDAC1 sobre ch2.
FACTOR_ASENTADO_GRUESO = 7.5
CANAL_SUM = 2
# Donde tiene que quedar el sumador para que LPo entre en la ventana.
#
# La etapa LP amplifica desde SUMo con modulo 9,07 y su cero esta en SUMo cerca
# de -280 mV. Con PGAout en G, SUMo = G*(ch2 + 270) - 270, asi que la ventana de
# +-2.400 mV en LPo se traduce en una franja angosta de ch2 que se calcula abajo.
OFFSET_PGAOUT_MV = -270.0
GANANCIA_LP = -9.07
VENTANA_LPO_MV = 2400.0


def franja_util_de_ch2(ganancia_pgaout: float) -> tuple[float, float]:
    """En que franja tiene que caer ch2 para que LPo sea legible.

    Es angosta y esa es la razon de que los rastreos gruesos no la encuentren:
    a x4 mide unos 133 mV, o sea once codigos del actuador fino, y un rastreo de
    63 codigos le pasa por encima sin verla.
    """
    centro_sumo = OFFSET_PGAOUT_MV
    medio_ancho = VENTANA_LPO_MV / abs(GANANCIA_LP)
    lo_sumo = centro_sumo - medio_ancho
    hi_sumo = centro_sumo + medio_ancho
    def a_ch2(sumo: float) -> float:
        """Invierte SUMo = G*(ch2 - offset) + offset."""
        return (sumo - OFFSET_PGAOUT_MV) / ganancia_pgaout + OFFSET_PGAOUT_MV

    lo, hi = a_ch2(lo_sumo), a_ch2(hi_sumo)
    return (min(lo, hi), max(lo, hi))
TAU_REFERENCIA_S = 34.0
# Lecturas invalidas seguidas tras las cuales se abandona la etapa. El regulador
# se repliega solo a los tres; si a los quince no volvio, no va a volver.
CIEGOS_PARA_ABANDONAR = 15


def real_mv(banco_uv: float) -> float:
    return (banco_uv / 1000.0 - BANCO_VREF_MV) * FACTOR


def leer(lab: Lab, ch: int, repeticiones: int = 3):
    """Mediana de varias ventanas. Devuelve el valor y si la lectura VALE."""
    medias = []
    for _ in range(repeticiones):
        p = lab.measure_dc(ch, settle_sel=2)
        if p is not None and p.ok:
            medias.append(p.mean_uv)
    if not medias:
        return None, False
    media = statistics.median(medias)
    return real_mv(media), lectura_valida(media / 1000.0)


def adelanto_por_rastreo(lab: Lab, canal: int, etapa: int,
                         espera_s: float, limite: int = 160,
                         puntos: int = 9) -> tuple[int, bool]:
    """Rastrea el rango COMPLETO buscando un codigo que deje el tap legible.

    Antes esto era una biseccion, y tenia una suposicion de modelo escondida:
    daba por hecho que subir el codigo sube el tap. Con la pendiente negativa se
    iba justo para el lado contrario, y el 2026-09-12 recorrio de 0 a +255
    concluyendo que la cadena no llegaba, cuando ni siquiera habia mirado los
    codigos negativos.

    Un adelanto que se supone ciego no puede suponer un signo. Se rastrea la
    grilla entera, los dos signos, y recien despues se afina alrededor del mejor
    punto. Cuesta unos minutos mas y no puede equivocarse de direccion.
    """
    paso = max(1, (2 * limite) // (puntos - 1))
    candidatos = list(range(-limite, limite + 1, paso))
    mejor_codigo, mejor_distancia = None, float("inf")
    for codigo in candidatos:
        lab.set_idac(etapa, codigo)
        time.sleep(espera_s)
        valor, valido = leer(lab, canal)
        print(f"    adelanto: IDAC{etapa}={codigo:+4d} -> "
              + ("sin lectura" if valor is None else f"{valor:+9.1f} mV")
              + ("   DENTRO" if valido else ""), flush=True)
        if valor is None:
            continue
        if valido:
            return codigo, True
        # Aunque este fuera de rango, el modulo dice cuan lejos esta. Sirve para
        # elegir por donde afinar.
        if abs(valor) < mejor_distancia:
            mejor_codigo, mejor_distancia = codigo, abs(valor)
    if mejor_codigo is None:
        return 0, False

    # Afinado alrededor del mejor punto del rastreo.
    print(f"    ninguno entro; se afina alrededor de {mejor_codigo:+d}",
          flush=True)
    for delta in (paso // 2, paso // 4):
        if delta < 1:
            break
        for codigo in (mejor_codigo - delta, mejor_codigo + delta):
            if not -limite <= codigo <= limite:
                continue
            lab.set_idac(etapa, codigo)
            time.sleep(espera_s)
            valor, valido = leer(lab, canal)
            print(f"    afinado:  IDAC{etapa}={codigo:+4d} -> "
                  + ("sin lectura" if valor is None else f"{valor:+9.1f} mV")
                  + ("   DENTRO" if valido else ""), flush=True)
            if valido:
                return codigo, True
    lab.set_idac(etapa, mejor_codigo)
    return mejor_codigo, False


def medir_signo(lab: Lab, etapa: int, canal: int, codigo_base: int,
                delta: int, espera_s: float) -> tuple[float | None, str]:
    """Mide cuanto mueve un actuador al tap, con signo.

    Es la unica pieza del modelo que el lazo necesita bien. Se mide con una
    excursion grande a proposito: con pocos codigos la lectura queda por debajo
    del ruido y el signo sale al azar, que fue como se perdieron varias horas.
    """
    lab.set_idac(etapa, codigo_base - delta)
    time.sleep(espera_s)
    bajo, ok_bajo = leer(lab, canal)
    lab.set_idac(etapa, codigo_base + delta)
    time.sleep(espera_s)
    alto, ok_alto = leer(lab, canal)
    lab.set_idac(etapa, codigo_base)
    time.sleep(espera_s)
    if not (ok_bajo and ok_alto) or bajo is None or alto is None:
        return None, "el tap salio de rango durante la medicion"
    pendiente = (alto - bajo) / (2.0 * delta)
    if abs(pendiente) < 0.5:
        return None, f"no responde ({pendiente:+.2f} mV/código)"
    return pendiente, "ok"


# Cuanto tiene que responder un tap para considerarlo en su zona lineal.
#
# Medio milivolt por codigo era demasiado permisivo: el 2026-09-12 dio por buena
# una respuesta de 0,90 cuando el nominal son 12,35, o sea catorce veces menos.
# Ahi el tap sigue comprimido y la ganancia estimada no sirve: el lazo corrige
# de mas y queda al borde de la inestabilidad.
#
# Un cuarto del nominal es exigente sin ser imposible, y coincide con el criterio
# de plausibilidad que ya se usa en el calibrador.
RESPUESTA_MINIMA_MV = 3.0


def llevar_a_zona_lineal(lab: Lab, canal: int, etapa_prueba: int,
                         etapa_entrada: int, codigo_entrada: int,
                         sentido: float, espera_s: float,
                         pasos: int = 12,
                         minimo: float = RESPUESTA_MINIMA_MV) -> tuple[int, bool]:
    """Mueve el actuador de ENTRADA hasta que el tap responda al suyo.

    Estar dentro de la ventana de lectura no alcanza. El 2026-09-12 el sumador
    marcaba +1.868 mV, comodamente adentro, y no se movia ni un milivolt ante
    ocho codigos de su actuador: estaba saturado igual. Ni el valor ni el ruido
    lo delatan; la falta de respuesta si.

    Y cuando esta pegado, lo unico que puede moverlo es lo que viene ANTES, que
    mueve unos 250 mV por codigo. Se avanza de a un codigo y se vuelve a probar
    la respuesta, que es el unico criterio que no se puede enganar.

    ``sentido`` es solo el SIGNO del efecto del actuador de entrada sobre el
    tap. Su modulo no se usa: vario entre -207 y -253 a lo largo del dia, pero
    el signo nunca cambio.
    """
    direccion = 1 if sentido < 0 else -1
    for intento in range(pasos):
        pendiente, _ = medir_signo(lab, etapa_prueba, canal, 0, 8, 3.0)
        if pendiente is not None and abs(pendiente) >= minimo:
            print(f"    zona lineal: responde con {pendiente:+.2f} mV/código "
                  f"tras {intento} pasos de IDAC{etapa_entrada}", flush=True)
            return codigo_entrada, True
        valor, _ = leer(lab, canal)
        if pendiente is not None:
            print(f"    zona lineal: responde solo {pendiente:+.2f} mV/código, "
                  f"menos del minimo {minimo:.1f}; sigue comprimido",
                  flush=True)
        codigo_entrada += direccion
        if not -255 <= codigo_entrada <= 255:
            break
        print(f"    zona lineal: ch{canal}="
              + ("?" if valor is None else f"{valor:+.0f} mV")
              + f" no responde; IDAC{etapa_entrada} -> {codigo_entrada:+d}",
              flush=True)
        lab.set_idac(etapa_entrada, codigo_entrada)
        time.sleep(espera_s)
    return codigo_entrada, False


def buscar_consigna_por_lpo(lab: Lab, escritor, t0: float,
                            hacer_regulador, minutos_por_intento: float,
                            etapa_gruesa: int = ETAPA_GRUESA,
                            lo: float = -1600.0, hi: float = 1600.0,
                            puntos: int = 9) -> tuple[float | None, float]:
    """Encuentra RASTREANDO la consigna de ch2 que deja LPo legible.

    Dos correcciones sobre lo que se intento antes, y las dos son el mismo error
    con distinta cara.

    Primero se calculaba con un modelo de la etapa LP. El modelo estaba mal:
    puso ch2 en -268,7 mV, dentro de la franja que el mismo predijo, y LPo
    siguio clavado en -5.407.

    Despues se bisectaba, y la biseccion suponia que subir ch2 sube LPo. Es
    falso, la etapa LP es inversora: con ch2 en 0 LPo dio -5.537 y subiendolo a
    +1.000 bajo a -5.594. Buscaba para el lado contrario. Es exactamente el
    mismo defecto que ya se habia corregido en el rastreo de codigos, escrito de
    nuevo en codigo nuevo que se suponia ciego.

    Asi que se rastrea la grilla completa, los dos signos, sin suponer ninguna
    direccion. Cada evaluacion lleva ch2 a una consigna con el lazo, que la
    alcanza al milivolt, y mira LPo. Nueve puntos cubren tres voltios y pico.
    """
    paso = (hi - lo) / (puntos - 1)
    mejor_consigna, mejor_distancia = None, float("inf")
    for indice in range(puntos):
        consigna = lo + indice * paso
        reg = hacer_regulador(consigna)
        print(f"\n  rastreo {indice + 1}/{puntos}: consigna de ch2 = "
              f"{consigna:+.0f} mV", flush=True)
        regular(lab, CANAL_SUM, reg, escritor, t0,
                f"busca{indice}", minutos_por_intento, 40.0, etapa_gruesa)
        lpo, valido = leer(lab, CANAL_LPO)
        if lpo is None:
            continue
        print(f"    ch2 en {consigna:+.0f} -> LPo {lpo:+.0f} mV"
              + ("   LEGIBLE" if valido else ""), flush=True)
        if valido:
            return consigna, lpo
        if abs(lpo) < mejor_distancia:
            mejor_consigna, mejor_distancia = consigna, abs(lpo)
    if mejor_consigna is None:
        return None, float("nan")

    # Afinado alrededor del punto que quedo mas cerca de la ventana.
    print(f"\n  ninguna consigna entro; se afina alrededor de "
          f"{mejor_consigna:+.0f} mV", flush=True)
    for divisor in (2.0, 4.0):
        for consigna in (mejor_consigna - paso / divisor,
                         mejor_consigna + paso / divisor):
            reg = hacer_regulador(consigna)
            regular(lab, CANAL_SUM, reg, escritor, t0, "afina",
                    minutos_por_intento, 40.0, etapa_gruesa)
            lpo, valido = leer(lab, CANAL_LPO)
            if lpo is None:
                continue
            print(f"    afinado: ch2 en {consigna:+.0f} -> LPo {lpo:+.0f} mV"
                  + ("   LEGIBLE" if valido else ""), flush=True)
            if valido:
                return consigna, lpo
    return None, float("nan")


def consigna_esperada(consigna_conocida: float, ganancia_conocida: float,
                      ganancia_nueva: float,
                      offset_pgaout: float = OFFSET_PGAOUT_MV) -> float:
    """Predice la consigna de ch2 al cambiar la ganancia de PGAout.

    LPo se centra cuando SUMo vale cierto valor fijo, y SUMo = G*(ch2 - off) +
    off. Despejando, la consigna de ch2 vale ``off + (S* - off)/G``, o sea que se
    acerca al offset de PGAout a medida que la ganancia sube.

    Con un solo punto conocido alcanza para predecir el resto: se despeja S* de
    ese punto y se evalua en la ganancia nueva. Medido a x4 la consigna es
    -1.600 mV, asi que a x24 deberia estar cerca de -492.

    Es una PREDICCION, no un dato. Sirve para centrar una busqueda estrecha en
    vez de rastrear tres voltios, y la busqueda la confirma o la desmiente.
    """
    s_estrella = ganancia_conocida * (consigna_conocida - offset_pgaout) + offset_pgaout
    return offset_pgaout + (s_estrella - offset_pgaout) / ganancia_nueva


def regular(lab: Lab, canal: int, reg: ReguladorCascada, escritor,
            t0: float, etiqueta: str, minutos: float,
            tolerancia_mv: float,
            etapa_gruesa: int = ETAPA_GRUESA) -> tuple[bool, float | None]:
    """Corre el lazo cerrado sobre un canal hasta converger o agotar el tiempo.

    Devuelve si convergio y el ultimo error valido. No decide nada sobre que
    canal regular: eso lo elige quien llama, y es lo que permite usar el mismo
    lazo para las dos etapas.
    """
    fin = time.time() + minutos * 60.0
    ultimo, ciegos = None, 0
    while time.time() < fin:
        valor, valido = leer(lab, canal)
        if valor is None:
            time.sleep(reg.periodo_s)
            continue
        salida = reg.paso(valor, valido)
        if salida.paso_fino:
            lab.set_idac(ETAPA_FINA, salida.codigo_fino)
        if salida.paso_grueso:
            lab.set_idac(etapa_gruesa, salida.codigo_grueso)
        t = time.time() - t0
        escritor.writerow([f"{t:.1f}", etiqueta, f"{valor:.1f}", valido,
                           "" if salida.error_real_mv != salida.error_real_mv
                           else f"{salida.error_real_mv:.1f}",
                           salida.codigo_grueso, salida.codigo_fino,
                           salida.paso_fino, salida.paso_grueso,
                           salida.motivo])
        estado = ("FUERA DE RANGO" if not valido
                  else f"error {salida.error_real_mv:+8.1f}")
        print(f"  [{etiqueta}] t={t:6.0f}s  ch{canal}={valor:+9.1f}  {estado}"
              # La etiqueta dice que actuador es de verdad. Decia "IDAC1"
              # fijo, pero el grueso puede ser IDAC0 o IDAC1 segun cual
              # responda, y el 2026-09-13 esa etiqueta equivocada me hizo
              # reconstruir un punto de trabajo con los codigos cruzados.
              f"  IDAC{ETAPA_FINA}={salida.codigo_fino:+4d} "
              f"IDAC{etapa_gruesa}={salida.codigo_grueso:+4d}"
              + (f"  {salida.motivo}" if salida.motivo else ""), flush=True)
        if valido:
            ultimo, ciegos = salida.error_real_mv, 0
        else:
            # Sin lecturas validas el lazo no puede hacer nada util. Insistir
            # gasta tiempo de placa: el 2026-09-12 esta espera se comio
            # cincuenta minutos mirando un tap fuera de rango. El regulador ya
            # se repliega solo; si aun asi no vuelve, hay que salir y decirlo.
            ciegos += 1
            if ciegos >= CIEGOS_PARA_ABANDONAR:
                print(f"  [{etiqueta}] {ciegos} lecturas seguidas fuera de "
                      "rango incluso tras replegarse; se abandona", flush=True)
                return False, ultimo
        if reg.convergido(tolerancia_mv):
            print(f"  [{etiqueta}] CONVERGIDO a los {t:.0f} s", flush=True)
            return True, ultimo
        time.sleep(reg.periodo_s)
    return False, ultimo



def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM8")
    ap.add_argument("--pga", type=int, default=50)
    ap.add_argument("--pgaout", type=int, default=4)
    ap.add_argument("--minutos", type=float, default=20.0)
    ap.add_argument("--periodo", type=float, default=5.0)
    ap.add_argument("--tolerancia", type=float, default=400.0,
                    help="precision pedida sobre LPo, en mV")
    ap.add_argument("--idac0", type=int, default=11,
                    help="lo que dejo el adelanto a lazo abierto")
    ap.add_argument("--idac1", type=int, default=0)
    ap.add_argument("--idac2", type=int, default=0)
    ap.add_argument("--consigna-semilla", type=float, default=None,
                    help="consigna de ch2 hallada en otra ganancia")
    ap.add_argument("--ganancia-semilla", type=float, default=0.0)
    ap.add_argument("--sin-fase1", action="store_true",
                    help="no corre el adelanto; usa los codigos dados")
    args = ap.parse_args()

    SALIDA.mkdir(parents=True, exist_ok=True)
    destino = SALIDA / ("cerrar_lazo_" + time.strftime("%Y%m%d_%H%M%S")
                        + ".csv")
    console = Console(args.port)
    console.open(wait_ready=True, timeout=25.0)
    lab = Lab(Session(console))
    salida = destino.open("w", newline="", encoding="utf-8")
    escritor = csv.writer(salida)
    escritor.writerow(["t_s", "etapa", "medido_real_mv", "valido", "error_mv",
                       "idac1", "idac2", "paso_fino", "paso_grueso", "motivo"])
    t0 = time.time()
    consigna_semilla = args.consigna_semilla
    ganancia_semilla = args.ganancia_semilla or 1.0

    try:
        lab.set_adc_config(1)
        lab.set_gain("pga", GAIN_CODES.index(args.pga))
        lab.set_gain("pgaout", GAIN_CODES.index(args.pgaout))
        # EL ADELANTO A LAZO ABIERTO, QUE ES LA FASE 1 QUE YA FUNCIONA.
        #
        # Con la cadena saturada, el actuador fino no tiene ninguna autoridad:
        # el 2026-09-12 se barrieron sus 160 codigos y LPo se movio 2 mV. Los
        # que pueden sacarla del riel son los gruesos, y elegirlos es justo lo
        # que hace la fase 1, que paso en todas las corridas del dia.
        #
        # No se reimplementa: se llama. El lazo cerrado no necesita que el
        # adelanto sea exacto, solo que deje la cadena legible.
        if not args.sin_fase1:
            print("Adelanto a lazo abierto (fase 1)...", flush=True)
            cal = TwoPhaseCalibrator(lab, SALIDA / "adelanto_fase1.json")
            resultado = cal.fase1_margen()
            args.idac0, args.idac1 = cal.codes[0], cal.codes[1]
            args.idac2 = cal.codes[2]
            veredicto = ("PASS" if resultado.get("criteria", {}).get(
                "ch0_margin") else "sin verificar")
            print(f"  fase 1 {veredicto}: IDAC0={args.idac0:+d} "
                  f"IDAC1={args.idac1:+d} IDAC2={args.idac2:+d}", flush=True)
            lab.set_gain("pgaout", GAIN_CODES.index(args.pgaout))
        else:
            lab.set_idac(0, args.idac0)
            lab.set_idac(ETAPA_GRUESA, args.idac1)
            lab.set_idac(ETAPA_FINA, args.idac2)
            lab.set_idac(3, 0)
        print(f"PGA x{args.pga}, PGAout x{args.pgaout}. "
              f"Punto de partida IDAC0={args.idac0:+d} "
              f"IDAC1={args.idac1:+d} IDAC2={args.idac2:+d}", flush=True)
        print("Asentando 3 tau...", flush=True)
        time.sleep(3.0 * TAU_REFERENCIA_S)

        # ETAPA A: cerrar el lazo sobre el SUMADOR, que si es observable.
        #
        # Con la cadena saturada, LPo no dice nada: se rastreo el rango completo
        # de los dos actuadores, en ambos signos, y se movio dos milivolts. No
        # es que los actuadores esten muertos, es que la franja de ch2 donde LPo
        # vuelve a ser legible es angosta y un rastreo grueso le pasa por
        # encima: a x4 mide 132 mV, o sea once codigos del fino.
        #
        # Pero ch2 se lee perfectamente aunque LPo este clavado. Asi que primero
        # se regula ch2 hasta meterlo en esa franja, y recien despues se pasa a
        # regular LPo, que para entonces ya significa algo.
        lo, hi = franja_util_de_ch2(float(args.pgaout))
        centro = (lo + hi) / 2.0
        print(f"\nFranja de ch2 donde LPo es legible: {lo:+.0f} a {hi:+.0f} mV "
              f"({hi - lo:.0f} mV de ancho, {(hi - lo) / 12.35:.1f} códigos)",
              flush=True)

        ch2, ok2 = leer(lab, CANAL_SUM)
        if ch2 is None or not ok2:
            print(f"\nEl sumador tambien esta fuera de rango ({ch2}). Ni "
                  "siquiera hay una variable observable para arrancar.",
                  flush=True)
            return 1
        print(f"Sumador de partida: {ch2:+.0f} mV. Falta llevarlo a "
              f"{centro:+.0f}.", flush=True)

        print("\nMidiendo el signo sobre el sumador:", flush=True)
        k2_sum, motivo = medir_signo(lab, ETAPA_FINA, CANAL_SUM, args.idac2,
                                     8, 3.0)
        if k2_sum is None:
            # El tap esta dentro de la ventana de lectura pero PEGADO: no
            # responde. Lo unico que lo puede mover es el actuador de entrada.
            print(f"  IDAC2 -> ch2: {motivo}. Se lleva ch2 a su zona lineal "
                  "moviendo IDAC0:", flush=True)
            args.idac0, ok_lineal = llevar_a_zona_lineal(
                lab, CANAL_SUM, ETAPA_FINA, 0, args.idac0,
                sentido=-1.0, espera_s=1.5 * TAU_REFERENCIA_S)
            if not ok_lineal:
                print("\n  ch2 no volvió a responder en todo el recorrido de "
                      "IDAC0. Sin un tap que responda no hay lazo posible, y "
                      "eso es un límite del hardware, no del control.",
                      flush=True)
                return 1
            k2_sum, motivo = medir_signo(lab, ETAPA_FINA, CANAL_SUM,
                                         args.idac2, 8, 3.0)
            if k2_sum is None:
                print(f"  IDAC2 -> ch2 sigue sin responder: {motivo}",
                      flush=True)
                return 1
        print(f"  IDAC2 -> ch2: {k2_sum:+.2f} mV/código", flush=True)
        # EL ACTUADOR GRUESO SE ELIGE MIDIENDO, NO POR DISENO.
        #
        # La cascada necesita uno con recorrido, pero cual de los dos candidatos
        # sirve depende del punto de trabajo. El 2026-09-12, con ch2 en +1.847,
        # IDAC1 no respondia (-0,00 mV/código) mientras IDAC0 acababa de sacar
        # al sumador de la saturacion en tres pasos.
        #
        # Fijarlo por diseno seria repetir el error que vengo corrigiendo todo
        # el dia: suponer en vez de medir. Se prueban los dos y gana el que
        # responda mas fuerte.
        etapa_gruesa, k1_sum = None, None
        for candidato, delta in ((ETAPA_GRUESA, 30), (0, 3)):
            pendiente, motivo = medir_signo(
                lab, candidato, CANAL_SUM,
                args.idac1 if candidato == ETAPA_GRUESA else args.idac0,
                delta, 1.5 * TAU_REFERENCIA_S)
            estado = (motivo if pendiente is None
                      else f"{pendiente:+.2f} mV/código")
            print(f"  IDAC{candidato} -> ch2: {estado}", flush=True)
            if pendiente is not None and (k1_sum is None
                                          or abs(pendiente) > abs(k1_sum)):
                etapa_gruesa, k1_sum = candidato, pendiente
        if k1_sum is None:
            print("  Ningun actuador grueso responde. Sin cascada no hay forma "
                  "de darle recorrido al lazo.", flush=True)
            return 1
        # LA GANANCIA DEL ACTUADOR LENTO SE CORRIGE A LA DE CONTINUA.
        #
        # `medir_signo` sondea con una espera de 1,5 tau, asi que devuelve una
        # ganancia parcialmente asentada. Para los actuadores que pasan por el
        # pasabanda eso no es una aproximacion razonable sino un error grande:
        # medido el 2026-09-13, el paso de IDAC1 sobre ch2 vale 72 mV por codigo
        # al instante y unos 540 en regimen. Siete veces y media.
        #
        # Importa porque el lazo divide el error por esta ganancia para pasar de
        # milivolts a codigos. Con la ganancia subestimada siete veces, el alpha
        # efectivo es siete veces el pedido: con alpha 0,15 queda 1,1, y el
        # limite de estabilidad es 2. Ahi es donde nacian los sobrepasos.
        #
        # La correccion NO es muestrear mas lento -eso tira informacion y agrega
        # tiempo muerto- sino darle al lazo la ganancia que la planta tiene de
        # verdad. Muestrear rapido y sintonizar lento.
        if etapa_gruesa in (0, ETAPA_GRUESA):
            k1_sum *= FACTOR_ASENTADO_GRUESO
            print(f"  Ganancia del grueso corregida a continua por "
                  f"x{FACTOR_ASENTADO_GRUESO:.1f}", flush=True)
        print(f"  Se usa IDAC{etapa_gruesa} como grueso "
              f"({k1_sum:+.2f} mV/código).", flush=True)
        codigo_grueso_inicial = (args.idac1 if etapa_gruesa == ETAPA_GRUESA
                                 else args.idac0)

        # ETAPA A: llevar ch2 a la consigna que deja LPo legible.
        #
        # Esa consigna se BUSCA, no se calcula. El modelo de la etapa LP fallo:
        # puso ch2 en -268,7 mV, dentro de la franja que el mismo predijo, y LPo
        # siguio clavado en -5.407. Ya van varios intentos de modelar esa
        # relacion sin acertar, asi que se deja de intentar.
        #
        # Buscarla es barato porque el lazo lleva ch2 a cualquier consigna con
        # precision de un milivolt, y porque el LADO en que satura LPo dice para
        # donde seguir aunque su valor no signifique nada.
        def hacer_regulador(consigna: float) -> ReguladorCascada:
            return ReguladorCascada(
                ganancia_fina=k2_sum, ganancia_gruesa=k1_sum,
                tau_s=TAU_REFERENCIA_S, periodo_s=args.periodo,
                referencia_real_mv=consigna, tolerancia_real_mv=40.0,
                codigo_fino=args.idac2,
                codigo_grueso=codigo_grueso_inicial)

        print(f"\nEtapa A: buscando la consigna de ch2 que deja LPo legible. "
              f"El modelo decia {(lo + hi) / 2.0:+.0f} mV; se verifica midiendo.",
              flush=True)
        if consigna_semilla is None:
            rango = (-1600.0, 1600.0)
            puntos = 9
        else:
            # Con una consigna conocida de la ganancia anterior, la prediccion
            # centra una busqueda estrecha: nueve puntos sobre 400 mV en vez de
            # sobre 3.200, o sea pasos de 50 mV donde la franja mide 22.
            centro_pred = consigna_esperada(consigna_semilla,
                                            ganancia_semilla,
                                            float(args.pgaout))
            rango = (centro_pred - 200.0, centro_pred + 200.0)
            puntos = 9
            print(f"\nSemilla: a x{ganancia_semilla:.0f} la consigna era "
                  f"{consigna_semilla:+.0f} mV, así que a x{args.pgaout} se "
                  f"predice {centro_pred:+.0f}. Se busca entre "
                  f"{rango[0]:+.0f} y {rango[1]:+.0f}.", flush=True)
        consigna, lpo_encontrado = buscar_consigna_por_lpo(
            lab, escritor, t0, hacer_regulador, args.minutos / 6.0,
            etapa_gruesa, rango[0], rango[1], puntos)
        if consigna is None:
            print("\nNinguna consigna de ch2 en cuatro voltios de rango deja "
                  "LPo legible. Con ch2 gobernado al milivolt, eso ya no es un "
                  "problema de control: la cadena no llega.", flush=True)
            return 1
        print(f"\nConsigna encontrada: ch2 = {consigna:+.0f} mV deja LPo en "
              f"{lpo_encontrado:+.0f} mV.", flush=True)
        ok_a, err_a = True, None
        reg_a = hacer_regulador(consigna)
        args.idac1, args.idac2 = reg_a.codigo_grueso, reg_a.codigo_fino

        # ETAPA B: ahora si, cerrar sobre LPo.
        lpo, valido = leer(lab, CANAL_LPO)
        if lpo is None or not valido:
            print(f"\nLPo sigue fuera de rango ({lpo}) pese a que el sumador "
                  "entro en su franja. El modelo de la franja esta mal.",
                  flush=True)
            return 1
        print(f"\nLPo ya es legible: {lpo:+.0f} mV. Se pasa a regularlo.",
              flush=True)

        k2_lpo, motivo = medir_signo(lab, ETAPA_FINA, CANAL_LPO,
                                     reg_a.codigo_fino, 2, 3.0)
        if k2_lpo is None:
            print(f"  IDAC2 -> LPo: {motivo}", flush=True)
            return 1
        k1_lpo = k2_lpo * (k1_sum / k2_sum)
        print(f"  IDAC2 -> LPo: {k2_lpo:+.0f} mV/código, "
              f"IDAC{etapa_gruesa} -> LPo: {k1_lpo:+.0f} (por proporción con "
              "lo medido sobre el sumador)", flush=True)

        reg_b = ReguladorCascada(ganancia_fina=k2_lpo, ganancia_gruesa=k1_lpo,
                                 tau_s=TAU_REFERENCIA_S,
                                 periodo_s=args.periodo,
                                 tolerancia_real_mv=args.tolerancia,
                                 codigo_fino=reg_a.codigo_fino,
                                 codigo_grueso=reg_a.codigo_grueso)
        print(f"Etapa B: alpha={reg_b.alpha:.3f}, tolera un error de ganancia "
              f"de hasta {tolerancia_de_ganancia(reg_b.alpha):.0f}x. "
              f"Vernier {'ACTIVO' if abs(k2_lpo) > args.tolerancia else 'no hace falta'}"
              f" (un código son {abs(k2_lpo):.0f} mV y se piden "
              f"{args.tolerancia:.0f}).\n", flush=True)
        ok_b, err_b = regular(lab, CANAL_LPO, reg_b, escritor, t0, "LPo",
                              args.minutos, args.tolerancia, etapa_gruesa)

        print("\n=== RESULTADO ===", flush=True)
        detalle_a = ("" if err_a is None
                     else f", error final {err_a:+.0f} mV")
        print(f"Etapa A (sumador): "
              f"{'convergió' if ok_a else 'no convergió'}{detalle_a}")
        print(f"Etapa B (LPo): {'CONVERGIO' if ok_b else 'no convergio'}"
              + (f", error final {err_b:+.0f} mV" if err_b is not None else ""))
        print(f"IDAC2 quedo en {reg_b.codigo_fino:+d} de ±{reg_b.limite_fino} "
              f"({100 * abs(reg_b.codigo_fino) // reg_b.limite_fino}% de su "
              "zona util)")
        print(f"IDAC1 quedo en {reg_b.codigo_grueso:+d}")
        print(f"Pasos con vernier: {reg_b.pasos_vernier}")
        # Linea analizable para encadenar ganancias sin intervencion.
        print(f"SEMILLA pgaout={args.pgaout} consigna={consigna:.1f} "
              f"idac0={args.idac0} error={0.0 if err_b is None else err_b:.1f} "
              f"idac2={reg_b.codigo_fino} ok={int(bool(ok_b))}")
        print(f"\nArchivo: {destino}", flush=True)
        return 0 if ok_b else 1

    finally:
        try:
            lab.set_gain("pgaout", 0)
            for etapa in range(4):
                lab.set_idac(etapa, 0)
        except Exception:
            pass
        salida.close()
        console.close()


if __name__ == "__main__":
    raise SystemExit(main())
