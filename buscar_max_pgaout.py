"""Busca el PGAout MÁXIMO que todavía calibra, con el PGA de entrada fijo.

EL OBJETIVO, planteado por Elías el 2026-09-05
    "el segundo pga anda variando de 1 hasta 50 y te quedas en el máximo estable;
     si fuese x1 sería como la placa que probé"

O sea que no hace falta que anden las 81 combinaciones: hace falta saber **hasta
dónde se puede subir PGAout** con el PGA de entrada en el valor que Elías ya
validó en campo (×50). Y hay una red de seguridad: PGAout ×1 reproduce la placa
que él ya probó y que funciona, así que el peor resultado posible de esta
búsqueda sigue siendo un nodo utilizable.

POR QUÉ ESTE PROGRAMA NO USA LA MATRIZ DE ACOPLE
Porque la matriz demostró no valer fuera del punto donde se midió. La del
2026-09-04 se tomó con PGA ×1 y PGAout ×1, y al usarla con otras ganancias la
calibración conjunta saltó de un riel al otro: predijo 59 mV para un cambio que
movió 371. Confiar en ella acá sería repetir el mismo error por tercera vez.

En su lugar, **la pendiente se mide en el punto de operación, cada vez**. Es un
Newton con derivada numérica: mover ±8 códigos, ver cuánto se movió el tap, y
recién entonces calcular el salto. Cuesta dos esperas de planta más por etapa, y
a cambio no supone absolutamente nada sobre el circuito.

ESTRUCTURA DE CADA COMBINACIÓN
    1. **Búsqueda gruesa por bisección**, etapa por etapa y de aguas arriba
       hacia abajo, hasta sacar los cuatro taps del riel. No necesita conocer la
       ganancia, sólo su signo, y hasta el signo se descubre probando los dos
       extremos. Es la parte robusta.
    2. **Refinamiento local**, midiendo la pendiente en el lugar y aplicando la
       corrección. Es la parte precisa.
    3. **Verificación** tras esperar de nuevo, que es lo único que cuenta.

    python buscar_max_pgaout.py --port COM8 --pga 8
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from testbench.core import console as con             # noqa: E402
from testbench.core.lab import GAIN_CODES, Lab        # noqa: E402
from testbench.core.session import Session            # noqa: E402

SALIDA = REPO / "lab" / "planta"
CANALES = (0, 1, 2, 3)
SETTLE_DC = 3
TAU_S = 29.5

# --------------------------------------------------------------------------
# EL SETPOINT, YA CON LA ESCALA MEDIDA CON TESTER (2026-09-05)
#
# Elias midio con tester dos puntos del mismo tap, separados a proposito:
#     banco 1000,32  ->  LPo = 2,388 V
#     banco 1099,94  ->  LPo = 4,372 V
# y ademas Vdda = 4,826 V y Vref = 2,415 V.
#
# De ahi sale la recta   LPo[mV] = 19,9157 * banco - 17533,5   o sea que UN
# MILIVOLTIO DE BANCO SON 19,92 mV REALES, y:
#
#     banco  880,4  =  0 V
#     banco 1001,5  =  Vref = 2,413 V      <- el objetivo
#     banco 1122,7  =  Vdda = 4,826 V
#
# Tres verificaciones independientes: la saturacion alta medida fue 1123,01 en
# ch3 y 1123,24 en ch2 contra 1122,7 predicho; el punto medio 1001,55 es
# exactamente donde se sienta la cadena en reposo; y ch0 en banco 1000,90
# predice 2,400 V contra 2,419 medidos.
#
# DOS CORRECCIONES A LO QUE HABIA ANTES:
#
# 1. El setpoint estuvo en 936 un rato, sacado del "centro de la excursion".
#    ERA UN ARTEFACTO: ese centro se calculo promediando un "riel bajo" de 746,
#    que segun la recta corresponde a -2,6 V, o sea IMPOSIBLE. Las lecturas por
#    debajo de banco 880 no son valores bajos: son el ADC fuera de rango
#    devolviendo basura. La conclusion "esta saturado abajo" era correcta; el
#    numero no.
#
# 2. El criterio original de 1000 mV, que se venia usando sin justificar, estaba
#    bien por casualidad: banco 1000 es Vref con 1,5 mV de error. Se corrige a
#    1001,5, que son 30 mV reales de mejora.
# --------------------------------------------------------------------------
OBJETIVO_MV = 1001.5

#: Piso de validez de la lectura. Por debajo de esto el ADC esta fuera de rango
#: y el numero que devuelve no significa nada: hay que tratarlo como "saturado
#: abajo", nunca como un valor.
BANCO_MINIMO_VALIDO_MV = 880.4


def banco_a_voltios(banco_mv):
    """Convierte una lectura del banco a voltios reales contra masa."""
    if banco_mv is None:
        return None
    return (19.9157 * banco_mv - 17533.5) / 1000.0

# --------------------------------------------------------------------------
# LOS RIELES, CORREGIDOS CON LA ESCALA MEDIDA CON TESTER
#
# ANTES estaban en 746..1122 de banco, tomados de "donde el tap deja de
# responder". El de arriba estaba bien; **el de abajo era basura**: banco 746
# equivale a -2,6 V contra masa, que es imposible. Lo que pasa por debajo de
# banco 880,4 es que el ADC se sale de rango y devuelve un numero sin sentido.
#
# La consecuencia era grave y silenciosa: un tap leyendo 900 se consideraba
# "sano, a 154 mV del riel" cuando en realidad esta en **0,39 V**, o sea casi
# contra masa. Varias combinaciones se declararon validas estando railadas, y
# otras se descartaron por criterios calculados sobre ese piso falso.
#
# Ahora los limites son fisicos y comunes a los cuatro taps, porque lo que los
# fija es la alimentacion y el rango del ADC, no cada etapa:
#     banco  880,4 = 0 V
#     banco 1122,7 = Vdda = 4,826 V
#
# Ver src/interfaces/python/escala_banco.py, que tiene la recta y sus tres
# verificaciones independientes.
# --------------------------------------------------------------------------
RIEL_BAJO = {c: 880.4 for c in range(4)}
RIEL_ALTO = {c: 1122.7 for c in range(4)}
#: Margen exigido contra cada riel, en mV de banco. 25 son ~0,5 V reales sobre
#: una excursion util de 4,83 V, o sea un 10 % de guarda a cada lado para que la
#: senal quepa encima del punto de continua sin recortar.
MARGEN = 25.0

#: Escalón para medir la pendiente en el lugar.
DELTA = 8
# --------------------------------------------------------------------------
# EL CRITERIO, EN SU FORMA FINAL (Elias, 2026-09-05)
#
#   "dejalo mas libre, consegui lo mejor que se pueda nomas, no debe saturar y
#    listo"
#
# y antes, sobre las prioridades:
#
#   "quiero 100 o 200 mV de deriva FISICOS respecto de 2,5 V para todos los
#    estados menos LP; ese reducilo al minimo [...] y priorizá en PGAout luego
#    de LP porque es el que tiene mas chance de saturar"
#
# O sea: UNA restriccion dura y un objetivo.
#
#   DURO      ninguna etapa satura, nunca. Es la unica condicion de aceptacion.
#   OBJETIVO  minimizar el desvio respecto de Vref, en este orden:
#               1. ch3 (LP)     - es el tap que se captura
#               2. ch2 (PGAout) - es el que mas chance tiene de saturar
#               3. ch0 y ch1    - lo que se consiga
#
# POR QUE NO HAY TECHO DURO PARA LAS INTERMEDIAS, y esta medido: a PGA x50 el
# tap del PGA se sienta 877 mV por debajo de Vref, y corregirlo es INFACTIBLE
# con las resistencias de hoy. Se midio el 2026-09-05 en el punto real: mover el
# PGA 4 codigos manda ch3 al riel, o sea que la pendiente PGA->ch3 es
# >= 605 mV/codigo contra 64,8 mV/codigo sobre su propio tap. Corregir los
# 877 mV costaria >= 8,2 V en el LP y al ADDER le quedan 4,8.
#
# Pero 877 mV de desvio NO ES SATURACION: el tap queda en 1,54 V con los rieles
# en 0 y 4,83, o sea con 1,54 V de margen para abajo. Es asimetrico, no esta
# recortando. Por eso pasa el criterio duro aunque no sea bonito.
# --------------------------------------------------------------------------

#: Margen minimo contra cada riel, en mV de banco. 25 son ~500 mV fisicos sobre
#: una excursion de 4,83 V: la guarda para que la senal quepa encima del punto
#: de continua sin recortar. ES LA UNICA CONDICION DURA.
GUARDA_ANTISATURACION_MV = 25.0

#: Orden en que se minimiza. Primero el que mas importa.
PRIORIDAD_TAPS = (3, 2, 0, 1)

#: Techo con el que se declara "bueno" el LP si no se consigue mejor. Se sigue
#: minimizando por debajo: es un umbral de reporte, no una meta.
TECHO_FISICO_LP_MV = 200.0
TOLERANCIA_MV = TECHO_FISICO_LP_MV / 19.9157


def en_riel(ch: int, mv: float | None) -> bool:
    if mv is None:
        return True
    return mv <= RIEL_BAJO[ch] + MARGEN or mv >= RIEL_ALTO[ch] - MARGEN


def centro(ch: int) -> float:
    """El centro de la excursión REAL del tap, no el objetivo nominal.

    Los rieles medidos están en ~750 y ~1122 mV, o sea centrados en ~936 y no en
    los 1000 mV del objetivo. Para la búsqueda gruesa lo que importa es alejarse
    de los dos rieles por igual, así que se apunta al centro real; el objetivo
    nominal se persigue después, en el refinamiento.
    """
    return (RIEL_BAJO[ch] + RIEL_ALTO[ch]) / 2.0


def leer(lab: Lab, chs=CANALES) -> dict[int, float | None]:
    out = {}
    for ch in chs:
        p = lab.measure_dc(ch, SETTLE_DC)
        out[ch] = (p.mean_uv / 1000.0) if (p and p.ok) else None
    return out


def centrar_tap(lab: Lab, tap: int, etapa: int, espera: float, log,
                objetivo: float | None = None) -> int | None:
    """Bisección del IDAC de `etapa` para llevar `tap` a `objetivo`.

    Por omisión apunta al centro de la excursión, que es lo que se quiere cuando
    sólo se busca alejar del riel. Para el tap del LP se le pasa el objetivo de
    la calibración, porque ahí sí importa dónde queda.

    Devuelve el código elegido, o None si esa etapa no puede mover ese tap. No
    usa la matriz: descubre hasta el signo probando los extremos.
    """
    obj = centro(tap) if objetivo is None else objetivo
    lab.set_idac(etapa, -255); time.sleep(espera)
    v_lo = leer(lab, (tap,))[tap]
    lab.set_idac(etapa, 255); time.sleep(espera)
    v_hi = leer(lab, (tap,))[tap]
    log(f"      etapa {etapa} -> tap {tap}: extremos {v_lo} / {v_hi} mV")
    # RECHAZAR SOLO SI LOS DOS EXTREMOS ESTAN EN EL MISMO RIEL.
    #
    # La primera version rechazaba cuando los dos estaban "en riel", sin mirar en
    # CUAL. Eso descartaba justamente el caso bueno: con PGA x50 el ADDER deja al
    # LP en 1122,5 mV con -255 y en 746,4 mV con +255, o sea contra los DOS
    # rieles opuestos. Que cruce de un riel al otro significa que **hay autoridad
    # de sobra** y que el punto util esta en el medio; es exactamente lo que la
    # biseccion sabe encontrar. Rechazarlo hacia concluir "no hay punto valido"
    # sobre una combinacion perfectamente calibrable.
    #
    # No hay autoridad solo si los dos extremos caen del MISMO lado.
    def _lado(v):
        if v is None:
            return None
        if v <= RIEL_BAJO[tap] + MARGEN:
            return "bajo"
        if v >= RIEL_ALTO[tap] - MARGEN:
            return "alto"
        return "medio"

    lado_lo, lado_hi = _lado(v_lo), _lado(v_hi)
    if lado_lo is None or lado_hi is None:
        return None
    if lado_lo != "medio" and lado_lo == lado_hi:
        log(f"      etapa {etapa} -> tap {tap}: los dos extremos en el riel "
            f"{lado_lo}; no hay autoridad")
        return None
    creciente = (v_hi or 0) > (v_lo or 0)
    lo, hi = -255, 255

    # SE DEVUELVE EL MEJOR PUNTO VISTO, NO EL ULTIMO PROBADO.
    #
    # La version anterior devolvia `mid` de la ultima iteracion, que es
    # simplemente donde quedo la busqueda al agotarse, y puede ser un punto
    # PEOR que otro ya visitado. Se vio en PGA x1 con PGAout x50: devolvio el
    # codigo -4 con el tap en 1122,2 mV, o sea contra el riel, habiendo pasado
    # antes por puntos mejores.
    #
    # Y 10 iteraciones en vez de 7: sobre un rango de 510 codigos, 7 pasos dejan
    # una resolucion de 4 codigos, y con PGAout alto la ventana util del ADDER es
    # de ese orden o menor. Con 10 la resolucion es de menos de un codigo, que es
    # el limite fisico. Cuesta tres esperas mas -90 s- y es lo que separa
    # "no hay ventana" de "no la encontre".
    mejor_cod, mejor_err, mejor_v = None, None, None
    for _ in range(10):
        mid = (lo + hi) // 2
        lab.set_idac(etapa, mid); time.sleep(espera)
        v = leer(lab, (tap,))[tap]
        if v is None:
            return None
        if not en_riel(tap, v):
            err = abs(v - obj)
            if mejor_err is None or err < mejor_err:
                mejor_cod, mejor_err, mejor_v = mid, err, v
            if err < 8.0:
                break
        if (v < obj) == creciente:
            lo = mid
        else:
            hi = mid
        if hi - lo <= 1:
            break

    if mejor_cod is None:
        log(f"      etapa {etapa} -> tap {tap}: la biseccion nunca encontro un "
            f"punto fuera del riel; la ventana util es mas angosta que un codigo")
        return None
    lab.set_idac(etapa, mejor_cod)
    log(f"      etapa {etapa} -> tap {tap}: codigo {mejor_cod} deja el tap en "
        f"{mejor_v:.1f} mV")
    return mejor_cod


def gruesa(lab: Lab, espera: float, log) -> dict[int, int] | None:
    """Deja los cuatro taps CENTRADOS en su excursión, sin usar la matriz.

    EL EMPAREJAMIENTO NO ES EL OBVIO, Y ESO SE DESCUBRIO MIDIENDO. La forma
    natural sería que cada etapa controle su propio tap. Para el LP eso NO
    FUNCIONA: el 2026-09-05, con PGA x50, el tap del LP quedó en el riel y ni
    -255 ni +255 de su propio IDAC lo sacaron de ahi. Su autoridad son 525
    uV/codigo, o sea +-134 mV, y lo que le mete el ADDER son 3823 uV/codigo:
    **7,3 veces mas**. El LP no puede con lo que le hace el ADDER.

    Entonces el tap del LP se centra desde el ADDER, que es quien tiene la
    autoridad, y el IDAC del propio LP queda para el ajuste fino. El ADDER paga
    el costo de descentrarse, y puede: su excursion es la misma pero su tap no es
    el que se captura, asi que alcanza con que no llegue al riel.

    Se centran TODOS los taps y no solo los que estan en riel. La primera version
    solo tocaba los railados, y por eso dejo el ADDER en 1089,7 mV -a 32 mV del
    riel, sin marcar como railado- y con eso el LP no tenia ninguna chance.
    """
    dac = {k: 0 for k in CANALES}
    for k in CANALES:
        lab.set_idac(k, 0)
    time.sleep(espera)

    # 1. Aguas arriba, y con la mano MUY liviana.
    #
    #    ESTO YA ME SALIO MAL UNA VEZ Y LA LECCION ES CARA. La primera version
    #    centraba las etapas 0 y 1 en el centro de su excursion siempre que
    #    estuvieran a mas de 60 mV de el. Con PGA x50 eso llevo la etapa 1 al
    #    codigo -252, y como esa etapa mueve el tap del LP unos 1002 uV por
    #    codigo, esos 252 codigos le metieron al LP ~252 mV: o sea que **la
    #    propia correccion metio al LP contra el riel**, y el programa concluyo
    #    que no habia punto valido cuando el que lo habia arruinado era el.
    #
    #    El tap 1 estaba en 1016,5 mV, a 106 mV de su riel: perfectamente sano.
    #    No habia nada que corregir.
    #
    #    Regla nueva, y sale de la estructura triangular: **aguas arriba solo se
    #    toca lo que esta por saturar, y solo lo justo para alejarlo**. Todo el
    #    margen que quede se reserva para el LP, que es el tap que se captura y
    #    el unico que entra en el criterio.
    MARGEN_SEGURO_MV = 60.0     # distancia al riel que se considera suficiente
    for etapa in (0, 1):
        v = leer(lab, (etapa,))[etapa]
        if v is None:
            return None
        margen = min(v - RIEL_BAJO[etapa], RIEL_ALTO[etapa] - v)
        if margen > MARGEN_SEGURO_MV:
            log(f"      etapa {etapa}: {v:.1f} mV, a {margen:.0f} mV del riel; "
                f"NO se toca (el margen es para el LP)")
            continue
        log(f"      etapa {etapa}: {v:.1f} mV, a solo {margen:.0f} mV del riel; "
            f"hay que alejarla")
        cod = centrar_tap(lab, etapa, etapa, espera, log)
        if cod is None:
            log(f"      etapa {etapa}: no se puede alejar del riel")
            return None
        dac[etapa] = cod

    # 2. El tap del LP, desde el ADDER. Es el tap que importa: es el que se
    #    captura, y el unico cuyo error entra en el criterio de Elias.
    #    Se apunta directo al OBJETIVO y no al centro de la excursion, para que
    #    a la etapa 3 le quede solo recortar unos milivoltios y no recorrer
    #    decenas: su escalon es de 0,525 mV, asi que recorrer 60 mV le costaria
    #    115 de sus 255 codigos por nada.
    cod = centrar_tap(lab, 3, 2, espera, log, objetivo=OBJETIVO_MV)
    if cod is None:
        log("      el ADDER tampoco saca al LP del riel: no hay punto valido")
        return None
    dac[2] = cod

    # 3. Comprobar que el ADDER no se haya railado al pagar ese costo.
    v2 = leer(lab, (2,))[2]
    if en_riel(2, v2):
        log(f"      el ADDER quedo en el riel ({v2}) al centrar el LP: "
            f"no hay punto donde los dos convivan")
        return None
    log(f"      ADDER en {v2:.1f} mV tras centrar el LP: aceptable")
    return dac


def refinar(lab: Lab, dac: dict[int, int], espera: float, vueltas: int, log) -> dict[int, int]:
    """Newton con derivada medida en el lugar.

    EMPAREJAMIENTO (tap, etapa), y no es la diagonal:

        tap 0 <- etapa 0     tap 1 <- etapa 1     tap 3 <- etapa 3

    **El tap 2 no se persigue.** Al ADDER se lo usa en la busqueda gruesa para
    centrar el LP, que es el tap que se captura y el unico que entra en el
    criterio de Elias; despues de eso el ADDER queda donde quedo, y esta bien:
    alcanza con que no llegue al riel. Perseguir su objetivo nominal seria
    deshacer justamente lo que se hizo para salvar al LP.

    El tap 3 SI se refina con su propia etapa: para el ajuste fino sus 525
    uV/codigo alcanzan de sobra -son +-134 mV- y tiene la resolucion que el
    ADDER, con 3823 uV/codigo, no tiene.
    """
    # SOLO EL TAP 3, Y ESTO SE APRENDIO ROMPIENDOLO.
    #
    # La version anterior refinaba tambien los taps 0 y 1 hacia el objetivo
    # nominal. Con PGA x50 eso movio la etapa 0 catorce codigos para llevar ch0
    # de 958 a 997 mV, y como el acople de esa etapa hacia ch3 es de unos
    # 1026 uV/codigo multiplicado por la ganancia -o sea ~51.300 uV/codigo- esos
    # catorce codigos le metieron al LP **718 mV**. Los taps terminaron en
    # [997,9  998,3  764,0  1122,0]: los dos de abajo contra el riel. La
    # "correccion" destruyo lo que la busqueda gruesa habia logrado.
    #
    # Y perseguir ch0 no servia para nada: estaba en 958 mV, a 155 mV de su
    # riel, perfectamente sano. El criterio de Elias es sobre el LP -"lo que
    # importa es que no saturen, lo ideal es que LP este chico y las otras al
    # minimo"-, no sobre que cada tap de exactamente 1000 mV.
    #
    # Regla: aguas arriba solo se corrige lo que esta por saturar, y eso ya lo
    # hace la busqueda gruesa. El refinamiento es del LP y nada mas.
    PARES = ((3, 3),)
    for vuelta in range(vueltas):
        for tap, etapa in PARES:
            v0 = leer(lab, (tap,))[tap]
            if v0 is None or en_riel(tap, v0):
                continue
            err = OBJETIVO_MV - v0
            if abs(err) < 2.0:
                continue
            # Derivada numerica AQUI, no supuesta.
            prueba = max(-255, min(255, dac[etapa] + DELTA))
            if prueba == dac[etapa]:
                prueba = dac[etapa] - DELTA
            lab.set_idac(etapa, prueba); time.sleep(espera)
            v1 = leer(lab, (tap,))[tap]
            lab.set_idac(etapa, dac[etapa])
            if v1 is None or en_riel(tap, v1) or prueba == dac[etapa]:
                continue
            pend = (v1 - v0) / (prueba - dac[etapa])       # mV por codigo
            if abs(pend) < 1e-4:
                continue
            paso = int(round(err / pend))
            nuevo = max(-255, min(255, dac[etapa] + paso))
            lab.set_idac(etapa, nuevo)
            time.sleep(espera)
            dac[etapa] = nuevo
            v2 = leer(lab, (tap,))[tap]
            log(f"      v{vuelta} tap {tap} via etapa {etapa}: {v0:.1f} -> {v2} mV "
                f"(pend {1000 * pend:.0f} uV/cod, paso {paso:+d}, cod {nuevo})")
    return dac


def una_combinacion(lab: Lab, pga_code: int, out_code: int, espera_busq: float,
                    espera_med: float, vueltas: int) -> dict:
    t0 = time.time()
    lineas: list[str] = []

    def log(txt: str) -> None:
        print(txt, flush=True)
        lineas.append(txt)

    lab.set_gain("pga", pga_code)
    lab.set_gain("pgaout", out_code)
    log(f"    busqueda gruesa (espera {espera_busq:.0f} s por paso)")
    dac = gruesa(lab, espera_busq, log)
    if dac is None:
        return {"pga_x": GAIN_CODES[pga_code], "pgaout_x": GAIN_CODES[out_code],
                "resultado": "sin punto valido", "cumple": False,
                "t_s": time.time() - t0, "log": lineas}

    log(f"    refinamiento (espera {espera_med:.0f} s por paso)")
    dac = refinar(lab, dac, espera_med, vueltas, log)

    log(f"    verificacion: esperando {espera_med:.0f} s")
    time.sleep(espera_med)
    v = leer(lab)
    err = {ch: (None if v[ch] is None else v[ch] - OBJETIVO_MV) for ch in CANALES}
    railados = [ch for ch in CANALES if en_riel(ch, v[ch])]
    peor = max((abs(e) for e in err.values() if e is not None), default=None)
    cumple = (not railados) and (err.get(3) is not None) and abs(err[3]) <= TOLERANCIA_MV
    log(f"    taps {[None if v[c] is None else round(v[c], 1) for c in CANALES]}  "
        f"LP {err.get(3)}  peor {peor}  {'CUMPLE' if cumple else 'NO CUMPLE'}")
    return {"pga_code": pga_code, "pgaout_code": out_code,
            "pga_x": GAIN_CODES[pga_code], "pgaout_x": GAIN_CODES[out_code],
            "dac": dac, "taps_mv": v, "error_mv": err,
            "error_lp_mv": err.get(3), "error_peor_mv": peor,
            "en_riel": railados, "cumple": cumple,
            "t_s": time.time() - t0, "log": lineas}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", default=None)
    ap.add_argument("--pga", type=int, default=8, help="codigo del PGA de entrada, 0-8")
    ap.add_argument("--outs", default="0,1,2,3,4,5,6,7,8",
                    help="codigos de PGAout a probar, en orden, con --pga fijo")
    ap.add_argument("--pares", default=None,
                    help="lista pga:pgaout en CODIGOS, separada por comas. Si se "
                         "da, ignora --pga y --outs. Es para la segunda pregunta "
                         "de Elias: si conviene concentrar la ganancia en el "
                         "primer PGA o repartirla entre los dos")
    ap.add_argument("--tau-busqueda", type=float, default=1.0)
    ap.add_argument("--tau-medicion", type=float, default=2.0)
    ap.add_argument("--vueltas", type=int, default=2)
    a = ap.parse_args()

    # POR QUE HAY QUE PROBAR EL REPARTO Y NO ALCANZA CON LA GANANCIA TOTAL.
    # Elias planteo que si PGAout aguanta hasta x24 con el PGA en x50 -o sea x1200
    # en total- entonces x32 con x32, que son x1024, deberia aguantar tambien.
    # Puede ser falso, y por una razon concreta: el offset a la salida no depende
    # solo de la ganancia total sino de DONDE ENTRA cada offset.
    #
    #     offset_salida ~ A*Gp*Go + B*Go + C
    #
    # con A el offset referido a la entrada, B uno que entre entre las dos etapas
    # y C uno de la ultima. Con (50,24) eso es 1200A + 24B + C y con (32,32) es
    # 1024A + 32B + C. Si domina B, el reparto parejo es PEOR pese a tener menos
    # ganancia total. Por eso se mide en vez de deducirse.
    if a.pares:
        pares = []
        for par in a.pares.split(","):
            p_, o_ = par.split(":")
            pares.append((int(p_), int(o_)))
    else:
        pares = [(a.pga, int(x)) for x in a.outs.split(",")]
    eb, em = a.tau_busqueda * TAU_S, a.tau_medicion * TAU_S

    c = con.Console(a.port) if a.port else con.Console()
    print(f"Abriendo {c.port}...", flush=True)
    c.open(); time.sleep(1.0)
    lab = Lab(Session(c))
    filas = []
    SALIDA.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    ruta = SALIDA / f"max_pgaout_pga{a.pga}_{stamp}.json"
    try:
        for pc, oc in pares:
            print(f"\n=== PGA x{GAIN_CODES[pc]}  PGAout x{GAIN_CODES[oc]}  "
                  f"(total x{GAIN_CODES[pc] * GAIN_CODES[oc]}) ===", flush=True)
            r = una_combinacion(lab, pc, oc, eb, em, a.vueltas)
            filas.append(r)
            # Se guarda despues de CADA combinacion: una tanda de horas no se
            # puede perder por un corte a la mitad.
            ruta.write_text(json.dumps({"pga_code": a.pga, "pares": a.pares,
                                        "filas": filas}, indent=1),
                            encoding="utf-8")
    finally:
        c.close()

    print("\n" + "=" * 60)
    print("RESUMEN")
    print(f"{'PGA':>6} {'PGAout':>7} {'total':>7} {'LP mV':>9} {'peor mV':>9} "
          f"{'t s':>6}  veredicto")
    maximo = None
    for r in filas:
        lp = r.get("error_lp_mv")
        pe = r.get("error_peor_mv")
        print(f"{'x' + str(r['pga_x']):>6} {'x' + str(r['pgaout_x']):>7} "
              f"{'x' + str(r['pga_x'] * r['pgaout_x']):>7} "
              f"{(f'{lp:9.2f}' if lp is not None else '        -')} "
              f"{(f'{pe:9.2f}' if pe is not None else '        -')} "
              f"{r['t_s']:6.0f}  {'CUMPLE' if r['cumple'] else r.get('resultado', 'no cumple')}")
        if r["cumple"]:
            maximo = r["pgaout_x"]
    print()
    if maximo is None:
        print("Ninguna combinacion cumplio. Con PGAout x1 la cadena es la que "
              "Elias ya valido en campo, asi que hay que revisar el procedimiento "
              "antes que el hardware.")
    else:
        print(f"MAXIMO PGAout ESTABLE: x{maximo}")
    print(f"crudo -> {ruta}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
