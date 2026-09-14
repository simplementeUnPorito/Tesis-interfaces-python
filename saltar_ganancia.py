"""Salta a la ganancia de una vez, sin dejar nunca a LPo en el riel.

LAS TRES COSAS QUE SE APRENDIERON A LOS GOLPES EL 2026-09-13

1. PGAout tiene punto fijo propio, cerca de -327 mV y no en cero, asi que subir
   la ganancia con la entrada centrada en cero manda SUMo contra el riel. La
   consigna correcta sale del modelo de la etapa y esta abajo.

2. Preparar esa consigna con PGAout todavia en x1 NO sirve, aunque parezca lo
   prudente. Mover IDAC2 los 14 codigos que hacen falta corre LPo mas de cuatro
   voltios -el pasabajos amplifica x18 desde SUMo- y lo clava. Los taps del
   medio quedan bien y LPo se pasa minutos en el riel, y de ahi la cadena entera
   se va: en la corrida de las 17:45 ch2 termino en -4.975 mV.

   O sea que NO existe un camino en el que los cinco taps esten siempre
   centrados: a x1 LPo se centra con ch2 en cero y a x2 con ch2 en -161 mV, y
   entre esos dos puntos LPo esta afuera. La transicion es forzosamente por
   afuera.

   La salida no es evitarla sino ACORTARLA. El escape tarda decenas de
   segundos en desarrollarse -medido: 45 s para mover ch2 1.664 mV-, asi que si
   el codigo nuevo de IDAC2 y la ganancia nueva se aplican uno detras del otro,
   LPo pasa por el riel un par de segundos y no pasa nada.

3. IDAC3 no tiene una pendiente: el mismo dia midio 6,78 y 16,99 mV por codigo
   en dos puntos distintos, o sea entre +-1.729 y +-4.332 mV de recorrido. Aun
   con el valor chico alcanza para absorber el error de cuantizacion de IDAC2
   hasta x16. Pero la calibracion a x1 lo deja clavado en +255, sin NADA hacia
   arriba, y por eso parecia no tener autoridad: la tiene entera, lo que no
   tiene es margen. Devolverselo es la fase 0 de este script.

EL MODELO, QUE ES TODO LO QUE HACE FALTA

    ch3 = G * ch2 - (G-1) * Vref_o          la etapa de ganancia
    LPo = -k_lp * (ch3 - ch3_cero)          el pasabajos

y de ahi, para que LPo quede en cero a la ganancia G:

    ch2* = (ch3_cero + (G-1) * Vref_o) / G

Los dos numeros hay que MEDIRLOS en el punto, no tabularlos: Vref_o dio -326,
-328, -372 y -395 en cuatro corridas del mismo dia, y ch3_cero se corre unos
1.650 mV cuando la fase 0 mueve IDAC3. Con Vref_o = -327 y ch3_cero = -353 la
consigna da -340 mV para x2, -347 para x4, -350 para x8 y -352 para x16: notar
que CONVERGE, asi que por este lado las ganancias altas no son mas dificiles
que x8.
"""
from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from centrar_lpo import CANAL_LPO, esperar_quieto, leer_promedio, poner_idac
from regulador_pi import ReguladorCascada
from sostener import informar, tramo
from testbench.core.console import Console
from testbench.core.lab import GAIN_CODES, Lab
from testbench.core.session import Session

CANAL_OPA_SUM = 2
CANAL_SUMO = 3
NOMBRES = {0: "SEo", 1: "BPo", 2: "OPA_SUMo", 3: "SUMo", 4: "LPo"}

#: Cuanto se le tolera a SUMo. Sale de dividir la tolerancia de LPo por la
#: ganancia del pasabajos, que esta entre x18 y x60 segun el punto: con 5 mV
#: LPo queda dentro de 300 mV incluso en el caso feo. Pedirle a ch3 la misma
#: tolerancia que a LPo fue el error que mato la corrida de las 18:05.
TOLERANCIA_CH3_MV = 5.0

#: Punto fijo de PGAout, en la escala del banco. Se remide en cada corrida.
VREF_PGAOUT_MV = -327.0
#: Ganancia del pasabajos desde SUMo, de respaldo si no se la puede medir.
#:
#: Durante casi todo el 2026-09-13 se uso -17,95, sacado de comparar dos
#: lecturas de corridas DISTINTAS: un numero sin control. En la fase 0, donde
#: IDAC3 e IDAC2 se mueven de a poco y LPo se mide en cada paso, se despeja
#: limpio y da tres veces seguidas lo mismo:
#:
#:   IDAC3 -16 e IDAC2 -2  ->  LPo +18,1 mV
#:   descontando los -103 mV que puso IDAC3, a IDAC2 le tocan +121 mV en dos
#:   codigos, o sea -60,5 mV/codigo; y sobre los 12,22 mV/codigo de IDAC2 en
#:   ch2, la etapa amplifica **x4,8**.
#:
#: La diferencia decide si las ganancias altas son alcanzables: con x18 el error
#: de cuantizacion de IDAC2 a x16 son 1.752 mV y no entra en el recorrido de
#: IDAC3; con x4,8 son 470 mV y entra con holgura hasta x50.
GANANCIA_LP = -4.8


def leer(lab: Lab, canal: int, n: int = 3) -> tuple[float, bool]:
    return leer_promedio(lab, canal, n=n)


def estado(lab: Lab, registro, sangria: str = "      ") -> tuple[bool, dict]:
    valores = {}
    todo_bien = True
    for canal in (0, 1, 2, 3, 4):
        valor, vivo = leer(lab, canal)
        valores[canal] = valor
        todo_bien = todo_bien and vivo
        registro(f"{sangria}ch{canal} ({NOMBRES[canal]:<8}) {valor:+9.1f} mV"
                 + ("" if vivo else "   FUERA DE VENTANA"))
    return todo_bien, valores


def medir_pendiente(lab: Lab, etapa: int, base: int, canal: int, delta: int,
                    espera: float, registro) -> float | None:
    """ABBA corto, con el escalon dado vuelta si no entra hacia arriba."""
    if not -255 <= base + delta <= 255:
        delta = -delta
    if not -255 <= base + delta <= 255:
        return None
    lecturas: dict[int, list[float]] = {}
    for codigo in (base, base + delta, base + delta, base):
        poner_idac(lab, etapa, codigo)
        time.sleep(espera)
        valor, vivo = leer(lab, canal, n=2)
        if not vivo:
            poner_idac(lab, etapa, base)
            registro(f"      IDAC{etapa}: no se puede medir, ch{canal} fuera")
            return None
        lecturas.setdefault(codigo, []).append(valor)
    poner_idac(lab, etapa, base)
    time.sleep(espera)
    return (statistics.fmean(lecturas[base + delta])
            - statistics.fmean(lecturas[base])) / delta


def margen_necesario(k2_lpo: float, k3: float, ganancia: int,
                     seguridad: float = 3.0) -> int:
    """Cuantos codigos de IDAC3 hacen falta como vernier, y no mas.

    El fino solo tiene que cubrir el error de cuantizacion del grueso, o sea
    medio paso de IDAC2 sobre LPo. A x2 eso son 60 mV, que a 6,8 mV por codigo
    son NUEVE codigos.

    Importa porque descargar IDAC3 no es gratis: cada codigo que baja hay que
    compensarlo moviendo IDAC2, y eso corre ch2. Vaciarlo entero de +255 a 0
    camina ch2 unos 300 mV, y el 2026-09-13 las tres corridas que lo hicieron
    terminaron con la cadena escapandose a los pocos segundos del salto. Pedir
    300 mV de excursion para necesitar 60 es lo que hay que dejar de hacer.

    Con el factor de seguridad de 3 se piden 27 codigos para x2 -que mueven ch2
    unos 37 mV- y 213 para x16, donde la excursion grande si esta justificada.
    """
    if not k3:
        return 255
    medio_paso = abs(k2_lpo) * ganancia / 2.0
    return min(255, int(seguridad * medio_paso / abs(k3)) + 1)


def descargar_idac3(lab: Lab, codigos: list[int], k3: float, k2: float,
                    espera: float, registro, objetivo: int = 0,
                    margen_mv: float = 350.0) -> None:
    """Devuelve a IDAC3 su recorrido, compensando con IDAC2 sobre LPo.

    Es la fase que faltaba. La calibracion a PGAout x1 deja a IDAC3 apoyado en
    +255 porque es el unico que puede centrar LPo desde donde arranca la
    cadena, y ahi se queda sin nada hacia arriba: cuando despues hace falta
    corregir en ese sentido, parece que el actuador no tuviera autoridad. La
    tiene entera; lo que no tiene es margen.

    LA COMPENSACION VA POR REALIMENTACION, NO POR CUENTA

    La primera version calculaba de antemano cuantos codigos de IDAC2 hacian
    falta por cada paso de IDAC3, y con los numeros de este punto -IDAC3 a
    6,78 mV/codigo contra los 219 mV/codigo de IDAC2- la cuenta daba 0,37
    codigos, que redondea a CERO. O sea que IDAC3 bajaba sin que nadie
    sostuviera LPo, y en tres pasos lo clavaba contra el riel.

    Ademas la pendiente de IDAC3 no es una constante: el mismo dia se midio
    6,78 y 16,99 mV por codigo en puntos distintos. Cualquier cosa calculada de
    antemano con ese numero esta mal por un factor dos y medio.

    Asi que aca IDAC3 baja de a poco y IDAC2 corrige SOBRE LO MEDIDO, cuando
    LPo se aparta mas de ``margen_mv``. No hace falta que la razon entre las dos
    pendientes sea entera ni que se la conozca bien.
    """
    if not k3 or not k2:
        registro("   sin pendientes; no se descarga IDAC3")
        return
    # Cuantos codigos de IDAC3 equivalen a uno de IDAC2. Se baja de a la mitad
    # de eso, para que nunca haga falta mas de un codigo de IDAC2 por vez.
    razon = abs(k2 / k3)
    paso3_mag = max(1, min(40, int(razon / 2.0) or 1))
    if abs(codigos[3]) <= abs(objetivo):
        registro(f"   IDAC3 esta en {codigos[3]:+d} y con eso alcanza; no se "
                 f"lo toca.")
        return
    registro(f"   IDAC3 esta en {codigos[3]:+d} y tiene que llegar a "
             f"{objetivo:+d}. Se lo baja de a {paso3_mag} codigos y IDAC2 "
             f"sostiene LPo sobre lo medido.")
    for _ in range(80):
        if abs(codigos[3]) <= abs(objetivo) + paso3_mag:
            break
        paso3 = -paso3_mag if codigos[3] > 0 else paso3_mag
        anterior3, anterior2 = codigos[3], codigos[2]
        codigos[3] += paso3
        poner_idac(lab, 3, codigos[3])
        time.sleep(espera)
        lpo, vivo = leer(lab, CANAL_LPO)
        if vivo and abs(lpo) > margen_mv:
            paso2 = int(round(-lpo / k2))
            paso2 = max(-3, min(3, paso2))
            if paso2:
                codigos[2] = max(-255, min(255, codigos[2] + paso2))
                poner_idac(lab, 2, codigos[2])
                time.sleep(espera)
                lpo, vivo = leer(lab, CANAL_LPO)
        registro(f"      IDAC3={codigos[3]:+4d} IDAC2={codigos[2]:+4d}  "
                 f"LPo {lpo:+8.1f} mV" + ("" if vivo else "   FUERA"))
        if not vivo:
            # Se deshace el ultimo intercambio y se corta. Seguir bajando a
            # ciegas es como se pierde el punto, y con IDAC3 a medio descargar
            # igual queda mucho mas margen que con el clavado en +255.
            codigos[3], codigos[2] = anterior3, anterior2
            poner_idac(lab, 3, codigos[3])
            poner_idac(lab, 2, codigos[2])
            registro("      se salio de ventana; se deshace y se para aca")
            return
    registro(f"   IDAC3 queda en {codigos[3]:+d}: {255 - abs(codigos[3])} "
             f"codigos de margen del lado corto")


def consigna_ch2(ganancia: int, vref_o: float, ch3_cero: float) -> float:
    if ganancia <= 1:
        return ch3_cero
    return (ch3_cero + (ganancia - 1) * vref_o) / ganancia


def afinar(lab: Lab, etapa: int, codigo: int, pendiente: float,
           tolerancia: float, espera: float, registro,
           intentos: int = 5) -> tuple[int, float, bool]:
    """Acerca LPo a cero con un actuador, sin insistir sobre un tap clavado.

    El limite de dos pasos a ciegas es deliberado. La corrida de las 17:45
    hizo seis intentos seguidos leyendo un LPo clavado en -5.537 mV, o sea
    seis pasos calculados sobre un numero que no era una tension, y termino
    empujando a IDAC2 cincuenta y ocho codigos de mas.
    """
    ciegos = 0
    for intento in range(1, intentos + 1):
        lpo, vivo = leer(lab, CANAL_LPO)
        registro(f"      intento {intento}: LPo {lpo:+.1f} mV"
                 + ("" if vivo else "   FUERA DE VENTANA"))
        if vivo and abs(lpo) <= tolerancia:
            return codigo, lpo, True
        if not vivo:
            ciegos += 1
            if ciegos > 2:
                registro("      dos pasos a ciegas y sigue fuera: se para")
                return codigo, lpo, False
        paso = int(round(-lpo / pendiente))
        if paso == 0:
            registro(f"      un codigo de IDAC{etapa} ya es mas grueso que lo "
                     f"que falta")
            return codigo, lpo, vivo
        paso = max(-30, min(30, paso))
        nuevo = max(-255, min(255, codigo + paso))
        if nuevo == codigo:
            registro(f"      IDAC{etapa} en su tope, {codigo:+d}")
            return codigo, lpo, False
        codigo = nuevo
        poner_idac(lab, etapa, codigo)
        registro(f"      IDAC{etapa} -> {codigo:+d}")
        time.sleep(espera)
    lpo, vivo = leer(lab, CANAL_LPO)
    return codigo, lpo, vivo and abs(lpo) <= tolerancia


def afinar_rapido(lab: Lab, etapa: int, canal: int, objetivo: float,
                  codigo: int, pendiente: float, tolerancia: float,
                  registro, espera: float = 0.5, intentos: int = 8,
                  paso_maximo: int = 30) -> tuple[int, float, bool]:
    """Corrige contra el reloj, porque la ventana para hacerlo es de segundos.

    POR QUE TAN RAPIDO

    Durante la transicion de ganancia LPo esta forzosamente fuera de rango, y
    quedarse ahi tiene un costo que crece con el tiempo: medido el 2026-09-13,
    el escape de la cadena tarda unos 45 s en desarrollarse y despues no
    vuelve. La corrida de las 18:20 se tomo mas de un minuto para afinar -cinco
    pasos con 5 s de espera cada uno- y en ese minuto ch2 se fue de -143 a
    -1.721 mV; a partir de ahi lo que se median no eran los actuadores sino el
    escape.

    Los 5 s no tenian ninguna justificacion: el sumador, PGAout y el pasabajos
    asientan en milisegundos. Los unicos lentos son IDAC0 e IDAC1 por el
    capacitor de 680 uF, y esta funcion no los toca.

    Y CONTRA QUE TAP

    Contra ``canal``, que en la fase gruesa conviene que sea ch3 y no LPo. El
    pasabajos amplifica entre x18 y x60 desde SUMo, asi que cuando LPo esta
    clavado ch3 todavia se lee perfectamente: es el unico que sigue diciendo
    algo util mientras se sale del pozo.
    """
    for intento in range(1, intentos + 1):
        valor, vivo = leer(lab, canal, n=2)
        if not vivo:
            registro(f"      ch{canal} fuera de ventana; no se puede afinar "
                     f"con IDAC{etapa}")
            return codigo, valor, False
        error = objetivo - valor
        if abs(error) <= tolerancia:
            registro(f"      ch{canal} = {valor:+.1f} mV, dentro de "
                     f"{tolerancia:.0f} en {intento - 1} pasos")
            return codigo, valor, True
        paso = int(round(error / pendiente))
        if paso == 0:
            registro(f"      ch{canal} = {valor:+.1f} mV; un codigo de "
                     f"IDAC{etapa} ya es mas grueso que lo que falta")
            return codigo, valor, True
        paso = max(-paso_maximo, min(paso_maximo, paso))
        nuevo = max(-255, min(255, codigo + paso))
        if nuevo == codigo:
            registro(f"      IDAC{etapa} en su tope, {codigo:+d}")
            return codigo, valor, False
        codigo = nuevo
        poner_idac(lab, etapa, codigo)
        time.sleep(espera)
    valor, vivo = leer(lab, canal, n=2)
    return codigo, valor, vivo and abs(objetivo - valor) <= tolerancia


def rescatar_lpo(lab: Lab, codigos: list[int], registro,
                 paso: int = 20, intentos: int = 30,
                 objetivo_mv: float = 600.0) -> bool:
    """Mete a LPo en ventana desde donde este, usando solo el signo.

    Hace falta porque el punto de partida esta calibrado para UNA ganancia de
    PGA. Con otra, la cadena aterriza en otro lado: a PGA x32 con los codigos
    de x50, LPo arranca en +2.093 mV y el procedimiento abortaba antes de
    probar nada.

    De una lectura saturada no se sabe el valor pero si el signo, y el signo
    alcanza para caminar hacia adentro. Primero con IDAC3, que es el que llega
    a LPo sin tocar nada mas; si se queda sin recorrido, con IDAC2.
    """
    # IDAC2 PRIMERO, aunque sea el grueso. A PGA x32 el rescate con IDAC3
    # camino 240 codigos y movio LPo sesenta y siete milivolts: la etapa esta
    # saturada y su propio actuador no la saca. El que tiene fuerza sobre LPo
    # es IDAC2, con unos 60 mV por codigo a x1 y mas a ganancia alta.
    for etapa in (2, 3):
        for _ in range(intentos):
            lpo, vivo = leer(lab, CANAL_LPO, n=2)
            # No alcanza con que ENTRE en ventana. La primera version volvia
            # con LPo en +2.027 mV -diez veces la tolerancia, y a un pelo del
            # borde- y despues se salia de nuevo al asentar.
            if vivo and abs(lpo) <= objetivo_mv:
                registro(f"   LPo entro en ventana en {lpo:+.1f} mV con "
                         f"IDAC2={codigos[2]:+d} IDAC3={codigos[3]:+d}")
                return True
            # IDAC3 sube LPo y IDAC2 lo baja: signos opuestos.
            d = -paso if lpo > 0 else paso
            if etapa == 2:
                d = -d
            nuevo = max(-255, min(255, codigos[etapa] + d))
            if nuevo == codigos[etapa]:
                break
            codigos[etapa] = nuevo
            poner_idac(lab, etapa, nuevo)
            time.sleep(1.0)
        registro(f"   IDAC{etapa} agoto su recorrido; se prueba con el otro")
    lpo, vivo = leer(lab, CANAL_LPO, n=2)
    return vivo


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM8")
    ap.add_argument("--pga", type=int, default=50)
    ap.add_argument("--punto", required=True, help="idac0,idac1,idac2,idac3")
    ap.add_argument("--ganancias", default="2,4,8,16,24")
    ap.add_argument("--vref-pgaout", type=float, default=VREF_PGAOUT_MV)
    ap.add_argument("--tolerancia", type=float, default=200.0)
    ap.add_argument("--asentar", type=float, default=100.0)
    ap.add_argument("--espera", type=float, default=5.0)
    ap.add_argument("--sostener", type=float, default=0.0)
    ap.add_argument("--congelado", type=float, default=0.0,
                    help="minutos de observacion SIN corregir despues de "
                         "llegar; es el experimento de control que dice si lo "
                         "que se mueve es la cadena o el lazo")
    ap.add_argument("--periodo", type=float, default=30.0)
    ap.add_argument("--salida", default=None)
    args = ap.parse_args()

    base = [int(x) for x in args.punto.split(",")]
    ganancias = [int(x) for x in args.ganancias.split(",")]
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
    vref_o = args.vref_pgaout
    logrado: list[tuple[int, list[int], float]] = []
    dejar_puesto = False
    try:
        lab.set_adc_config(1)
        lab.set_gain("pga", GAIN_CODES.index(args.pga))
        lab.set_gain("pgaout", GAIN_CODES.index(1))
        codigos = list(base)
        for etapa, codigo in enumerate(codigos):
            poner_idac(lab, etapa, codigo)
        registro(f"PGA x{args.pga} fijo. Punto de partida IDAC "
                 + " ".join(f"{c:+d}" for c in codigos))
        registro(f"Asentando {args.asentar:.0f} s...")
        time.sleep(args.asentar)
        esperar_quieto(lab, CANAL_OPA_SUM, registro, maximo_s=120.0)
        registro("\nEstado a x1:")
        bien, v = estado(lab, registro)
        if not bien:
            registro("La cadena no arranca en ventana; se la rescata por "
                     "signo antes de abandonar.")
            if not rescatar_lpo(lab, codigos, registro):
                registro("No se pudo. Ahora si, no tiene sentido seguir.")
                return 1
            time.sleep(20.0)
            esperar_quieto(lab, CANAL_OPA_SUM, registro, maximo_s=120.0)
            bien, v = estado(lab, registro)
            if not bien:
                registro("Sigue fuera de ventana; no tiene sentido seguir.")
                return 1

        # El cero del pasabajos: en que valor de ch3 queda LPo en cero.
        # LPo = a*(ch3 - ch3_cero)  =>  ch3_cero = ch3 - LPo/a, con a < 0.
        ch3_cero = v[CANAL_SUMO] - v[CANAL_LPO] / GANANCIA_LP
        registro(f"\nLPo se anula con ch3 = {ch3_cero:+.1f} mV")

        registro("\nPendientes en este punto:")
        k2 = medir_pendiente(lab, 2, codigos[2], CANAL_OPA_SUM, 20,
                             args.espera, registro) or 12.2
        registro(f"   IDAC2 -> ch2  {k2:+.2f} mV/codigo")
        k3 = medir_pendiente(lab, 3, codigos[3], CANAL_LPO, -20,
                             args.espera, registro) or 16.99
        registro(f"   IDAC3 -> LPo  {k3:+.2f} mV/codigo, o sea "
                 f"{255 * abs(k3):.0f} mV de recorrido a cada lado")
        # Se mide en vez de deducirla. Es el numero del que depende todo el
        # afinado, y un ABBA de dos codigos con IDAC2 cuesta segundos.
        k2_lpo = medir_pendiente(lab, 2, codigos[2], CANAL_LPO, 2,
                                 args.espera, registro)
        if k2_lpo is None or abs(k2_lpo) < 5.0:
            k2_lpo = k2 * GANANCIA_LP
            registro(f"   IDAC2 -> LPo  no medible; se usa "
                     f"{k2_lpo:+.0f} mV/codigo")
        else:
            registro(f"   IDAC2 -> LPo  {k2_lpo:+.1f} mV/codigo a x1")
        ganancia_lp = k2_lpo / k2
        registro(f"   o sea que el pasabajos amplifica x{abs(ganancia_lp):.1f} "
                 f"desde SUMo")

        registro("\n=== FASE 0: devolverle a IDAC3 el margen que haga falta "
                 "===")
        mayor = max(ganancias)
        margen = margen_necesario(k2_lpo, k3, mayor)
        registro(f"   para x{mayor} el vernier tiene que cubrir medio paso de "
                 f"IDAC2, o sea {margen} codigos")
        objetivo3 = (255 - margen) if codigos[3] > 0 else -(255 - margen)
        descargar_idac3(lab, codigos, k3, k2_lpo, 1.5, registro,
                        objetivo=objetivo3)
        # LA FASE 0 DEJA A LA CADENA EN MOVIMIENTO Y HAY QUE ESPERARLA.
        #
        # El intercambio camina ch2 unos 300 mV, y despues de eso la cadena
        # sigue acomodandose: el 2026-09-13 a las 19:05 el salto se hizo
        # enseguida y ch2 se corrio 47 mV en los veinte segundos siguientes,
        # o sea 140 mV/min, que a x2 son 1.400 mV/min sobre LPo. Ningun lazo
        # gana esa carrera, y el punto se perdio a los noventa segundos de
        # haberlo encontrado.
        registro("   esperando a que la cadena se aquiete tras la descarga")
        esperar_quieto(lab, CANAL_OPA_SUM, registro, tolerancia_mv=8.0,
                       periodo=15.0, maximo_s=300.0)
        registro("   estado despues de la descarga:")
        _, v = estado(lab, registro)

        # EL CERO DEL PASABAJOS SE MUEVE CON IDAC3, ASI QUE HAY QUE REHACERLO.
        #
        # `ch3_cero` es el valor de SUMo que deja LPo en cero, y depende de
        # donde este IDAC3. La fase 0 acaba de mover IDAC3 240 codigos, o sea
        # unos 1.650 mV de LPo: el cero de antes ya no vale.
        #
        # Usarlo igual fue lo que rompio la corrida de las 18:50. Con el cero
        # viejo la consigna de ch2 daba -171 mV cuando la correcta era -340, y
        # el salto movio ch2 170 mV PARA EL LADO EQUIVOCADO.
        ch3_cero = v[CANAL_SUMO] - v[CANAL_LPO] / ganancia_lp
        registro(f"   con IDAC3 en {codigos[3]:+d}, LPo se anula ahora con "
                 f"ch3 = {ch3_cero:+.1f} mV")

        for ganancia in ganancias:
            total = args.pga * ganancia
            registro(f"\n=== PGAout x{ganancia}, ganancia conjunta {total} ===")
            ch2_ahora, _ = leer(lab, CANAL_OPA_SUM)
            objetivo = consigna_ch2(ganancia, vref_o, ch3_cero)
            salto = int(round((objetivo - ch2_ahora) / k2))
            nuevo2 = max(-255, min(255, codigos[2] + salto))
            registro(f"   ch2 esta en {ch2_ahora:+.1f} y tiene que ir a "
                     f"{objetivo:+.1f} mV: IDAC2 {codigos[2]:+d} -> {nuevo2:+d}")
            registro("   se aplican el codigo y la ganancia seguidos, para que "
                     "LPo pase por el riel segundos y no minutos")
            # Los dos, uno detras del otro y sin nada en el medio.
            poner_idac(lab, 2, nuevo2)
            lab.set_gain("pgaout", GAIN_CODES.index(ganancia))
            codigos[2] = nuevo2
            # Un segundo, no cinco: el reloj del escape ya arranco.
            time.sleep(1.0)
            # Y una lectura a la basura. El propio firmware avisa que el primer
            # comando despues de un cambio de configuracion se ignora en
            # silencio, y en la corrida de las 18:50 ch3 devolvio "+0.0 fuera
            # de ventana" justo despues del cambio de ganancia, que es lo que
            # devuelve una lectura que no ocurrio.
            leer(lab, CANAL_SUMO, n=1)

            ch2, _ = leer(lab, CANAL_OPA_SUM, n=2)
            ch3, vivo3 = leer(lab, CANAL_SUMO, n=2)
            lpo, vivo4 = leer(lab, CANAL_LPO, n=2)
            registro(f"      ch2 {ch2:+.1f}  ch3 {ch3:+.1f}"
                     + ("" if vivo3 else " FUERA")
                     + f"  LPo {lpo:+.1f}" + ("" if vivo4 else " FUERA"))
            if vivo3:
                medido = (ganancia * ch2 - ch3) / (ganancia - 1)
                registro(f"      punto fijo de PGAout medido aca: "
                         f"{medido:+.0f} mV")
                vref_o = medido

            # A partir de aca se corre contra el reloj: LPo esta clavado y la
            # cadena empieza a escaparse en unos 45 s. Todo lo que sigue usa
            # esperas de medio segundo.
            registro("   afinado grueso contra ch3, que sigue legible")
            codigos[2], ch3, _ = afinar_rapido(
                lab, 2, CANAL_SUMO, ch3_cero, codigos[2], k2 * ganancia,
                max(3.0, args.tolerancia / abs(ganancia_lp) / 3.0),
                registro)
            registro("   afinado fino de LPo con IDAC3")
            codigos[3], lpo, _ = afinar_rapido(
                lab, 3, CANAL_LPO, 0.0, codigos[3], k3, args.tolerancia,
                registro)
            # Y recien ahora se deja asentar y se vuelve a mirar, ya sin apuro.
            time.sleep(args.espera)
            lpo, _ = leer(lab, CANAL_LPO)
            if abs(lpo) > args.tolerancia:
                registro(f"   LPo quedo en {lpo:+.1f} mV; una pasada mas")
                codigos[3], lpo, _ = afinar_rapido(
                    lab, 3, CANAL_LPO, 0.0, codigos[3], k3, args.tolerancia,
                    registro, espera=1.0)

            registro("   estado final:")
            todo, _ = estado(lab, registro)
            gana = todo and abs(lpo) <= args.tolerancia
            registro(f"   >>> ganancia {total}: "
                     + ("CALIBRA" if gana else "no llega")
                     + f", LPo {lpo:+.1f} mV con "
                     + " ".join(f"{c:+d}" for c in codigos))
            if args.congelado > 0:
                # EL CONTROL, Y CORRE CALIBRE O NO.
                #
                # Un control que solo se ejecuta cuando el experimento sale
                # bien no es un control. Lo que hay que saber es si el punto se
                # pierde porque el lazo lo empuja mal o porque se pierde solo,
                # y eso se contesta congelando los cuatro codigos y mirando.
                registro("")
                registro(f"=== CONGELADO {args.congelado:.0f} MINUTOS A "
                         f"GANANCIA {total}: nadie toca nada ===")
                r = tramo(lab, args.congelado, 10.0, args.tolerancia, None,
                          list(codigos), f"CONGELADO a ganancia {total}",
                          registro)
                informar(r, args.tolerancia, registro)
                registro("   estado al terminar el congelado:")
                estado(lab, registro)
            if not gana:
                registro("   se vuelve a x1 y se corta la escalera.")
                lab.set_gain("pgaout", GAIN_CODES.index(1))
                break
            logrado.append((total, list(codigos), lpo))

        if logrado and args.sostener > 0:
            total, cod, _ = logrado[-1]
            ganancia = total // args.pga
            lab.set_gain("pgaout", GAIN_CODES.index(ganancia))
            for etapa, codigo in enumerate(cod):
                poner_idac(lab, etapa, codigo)
            time.sleep(args.espera)
            registro(f"\n=== SOSTENIENDO {total} DURANTE "
                     f"{args.sostener:.0f} MINUTOS CON EL PI ===")
            # LAS PENDIENTES SE REMIDEN ACA, A LA GANANCIA REAL.
            #
            # Usar las de PGAout x1 costo la ganancia 128: a x8 el signo de
            # IDAC3 sobre LPo esta DADO VUELTA respecto de lo medido a x1, y un
            # PI con el signo al reves es realimentacion positiva. Se ve claro
            # en la traza del 2026-09-14: IDAC3 de +74 a -251 mientras LPo sube
            # de +348 a +835, el fino satura, el mid-ranging mueve IDAC2 y todo
            # se da vuelta. Oscilacion de +-800 mV, no deriva.
            #
            # Son dos ABBA cortos sobre actuadores que asientan en
            # milisegundos: cuesta segundos y evita el modo de falla mas caro
            # que tiene este lazo.
            k3_aqui = medir_pendiente(lab, 3, cod[3], CANAL_LPO, 20,
                                      args.espera, registro)
            if k3_aqui is not None and abs(k3_aqui) >= 1.0:
                if (k3_aqui > 0) != (k3 > 0):
                    registro(f"   OJO: IDAC3 cambio de signo con la ganancia, "
                             f"{k3:+.2f} a x1 y {k3_aqui:+.2f} aca")
                k3 = k3_aqui
            k2_aqui = medir_pendiente(lab, 2, cod[2], CANAL_LPO, 1,
                                      args.espera, registro)
            k2g = (k2_aqui if k2_aqui is not None and abs(k2_aqui) >= 5.0
                   else k2_lpo * ganancia)
            registro(f"   pendientes en el punto: IDAC3 {k3:+.2f} y IDAC2 "
                     f"{k2g:+.1f} mV/codigo")
            reg = ReguladorCascada(
                ganancia_fina=k3,
                ganancia_gruesa=k2g,
                # El lazo va contra una deriva rapida y sobre actuadores que
                # asientan en milisegundos, asi que no hay razon para pedirle
                # prudencia: alpha = T/tau = 0,5 y el proporcional encima dejan
                # el primer paso en tres cuartos del error.
                tau_s=max(10.0, args.periodo * 2.0), periodo_s=args.periodo,
                referencia_real_mv=0.0,
                tolerancia_real_mv=args.tolerancia,
                codigo_fino=cod[3], codigo_grueso=cod[2],
                limite_fino=255, zona_comoda=200)
            codigos = list(cod)
            r = tramo(lab, args.sostener, args.periodo, args.tolerancia, reg,
                      codigos, f"CON EL PI a ganancia {total}", registro)
            sostuvo = informar(r, args.tolerancia, registro)
            registro(f"   la ganancia {total} "
                     + ("SE SOSTIENE" if sostuvo else "NO se sostiene"))
            registro("   estado al terminar:")
            estado(lab, registro)
            dejar_puesto = True

        registro("\n=== RESUMEN ===")
        if not logrado:
            registro("Ninguna ganancia por encima de x1 quedo calibrada.")
        for total, cod, lpo in logrado:
            registro(f"   ganancia {total:>5}: LPo {lpo:+8.1f} mV con IDAC "
                     + " ".join(f"{c:+d}" for c in cod))
        return 0 if logrado else 1
    finally:
        if dejar_puesto:
            print("\nSe deja la placa con la ganancia puesta.", flush=True)
        else:
            try:
                lab.set_gain("pgaout", GAIN_CODES.index(1))
                for etapa in range(4):
                    lab.set_idac(etapa, 0)
            except Exception:
                pass
        console.close()


if __name__ == "__main__":
    raise SystemExit(main())
