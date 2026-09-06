# -*- coding: utf-8 -*-
"""Lo que comparten todos los ensayos del lunes.

CADA GUARDA DE ESTE ARCHIVO COSTO UN ENSAYO ARRUINADO el fin de semana del
2026-09-05, y por eso estan todas juntas y se aplican siempre, en vez de
dejarlas a criterio de cada script:

  - `abrir_banco()` pide la configuracion del ADC EXPLICITAMENTE. Es global y
    persistente: la deja puesta el ultimo que la toco. Un ensayo de rangos la
    dejo en +-0,625 V y el siguiente leyo los cuatro taps como
    [21,5  66,7  171,1  -510,4] -un tap por arriba de Vref informando tension
    NEGATIVA-, porque los taps se sientan en ~1 V a la entrada del ADC y eso
    supera el fondo de escala de las configuraciones angostas.

  - `poner_idac()` REINTENTA y devuelve si el firmware acepto. Un set_idac
    perdido no avisa por si mismo, y corto un barrido de 17 puntos en el 16.

  - `esperar_quieto()` -en asentamiento.py- espera a que la cadena deje de
    moverse en vez de un tiempo fijo, y no mira los taps que estan en la zona
    ciega, porque ese ruido no se asienta nunca.

  - `describir_taps()` no convierte a milivolts las lecturas fuera de la
    ventana observable: abajo de 880 de banco la lectura no mide el tap -da
    tensiones negativas contra masa- y convertirla produce una cifra con
    aspecto de medida que no lo es.
"""
from __future__ import annotations
import os
import sys
import time
import json
from datetime import datetime

_AQUI = os.path.dirname(os.path.abspath(__file__))
_PY = os.path.dirname(_AQUI)
if _PY not in sys.path:
    sys.path.insert(0, _PY)

from testbench.core.console import Console          # noqa: E402
from testbench.core.session import Session          # noqa: E402
from testbench.core.lab import Lab                  # noqa: E402
from escala_banco import (a_voltios, VREF_V, VDDA_V,  # noqa: E402
                          BANCO_MIN_VALIDO_MV, BANCO_MAX_VALIDO_MV,
                          lectura_valida, delta_a_voltios)
from asentamiento import esperar_quieto, TAU_S      # noqa: E402

PUERTO = os.environ.get("BANCO_COM", "COM8")
SALIDA = r'C:\Github\Tesis\lab\lunes'

#: Codigos de ganancia del firmware -> factor. `pga 8` es x50.
GANANCIA = {0: 1, 1: 2, 2: 4, 3: 8, 4: 16, 5: 24, 6: 32, 7: 48, 8: 50}
#: La configuracion que Elias valido en campo.
CAMPO_PGA, CAMPO_PGAOUT = 8, 0

#: A menos de esto de un riel la etapa no transmite, en mV de banco.
MARGEN_RIEL_MV = 25.0


def abrir_banco(pga=None, pgaout=None, log=print):
    """Abre el puerto y deja el banco en un estado CONOCIDO. Devuelve (c, s, lab).

    No confia en como quedo de la corrida anterior: pide el ADC y, si se le
    pasan, las ganancias. Si algo no se puede fijar, levanta SystemExit en vez
    de seguir midiendo sobre un estado que no es el que se cree.
    """
    c = Console(PUERTO)
    c.open()
    time.sleep(2.0)
    s = Session(c)
    lab = Lab(s)

    if not s.probe().up:
        c.close()
        raise SystemExit("ABORTA: no hay enlace con el nodo en %s" % PUERTO)

    if not lab.set_adc_config(1):
        c.close()
        raise SystemExit("ABORTA: el firmware no acepto el ADC en +-2,5 V")

    if pga is not None and not lab.set_gain("pga", pga):
        c.close()
        raise SystemExit("ABORTA: el firmware no acepto PGA codigo %d" % pga)
    if pgaout is not None and not lab.set_gain("pgaout", pgaout):
        c.close()
        raise SystemExit("ABORTA: el firmware no acepto PGAout codigo %d" % pgaout)

    log("banco abierto en %s, ADC en +-2,5 V%s" % (
        PUERTO,
        "" if pga is None else ", PGA x%d / PGAout x%d" % (GANANCIA[pga], GANANCIA[pgaout])))
    return c, s, lab


def poner_idac(lab, etapa, code, intentos=3):
    """set_idac con reintento. Devuelve True solo si el firmware lo acepto."""
    for _ in range(intentos):
        if lab.set_idac(etapa, code):
            return True
        time.sleep(0.5)
    return False


def cerar_idacs(lab, etapas=(0, 1, 2, 3)):
    """Los IDAC en cero, VERIFICADO. Devuelve la lista de los que fallaron."""
    return [k for k in etapas if not poner_idac(lab, k, 0)]


def leer_taps(lab, canales=(0, 1, 2, 3)):
    """Los taps en mV de banco, sin esperar. None si el firmware dijo que no."""
    v = {}
    for ch in canales:
        try:
            p = lab.measure_dc(ch, 3)
            v[ch] = (p.mean_uv / 1000.0) if (p and p.ok) else None
        except Exception:
            v[ch] = None
    return v


def en_riel(mv):
    """True si esa lectura corresponde a una etapa que ya no transmite."""
    if mv is None:
        return True
    return (mv <= BANCO_MIN_VALIDO_MV + MARGEN_RIEL_MV or
            mv >= BANCO_MAX_VALIDO_MV - MARGEN_RIEL_MV)


def describir_taps(v, canales=(0, 1, 2, 3)):
    """Una linea por tap, con los volts SOLO donde la lectura significa algo."""
    nombres = {0: "PGA", 1: "BP", 2: "ADDER", 3: "LP"}
    filas = []
    for ch in canales:
        mv = v.get(ch)
        if mv is None:
            filas.append("   ch%d %-5s sin lectura" % (ch, nombres.get(ch, "")))
        elif not lectura_valida(mv):
            filas.append("   ch%d %-5s %9.2f de banco  ->  FUERA DE LA VENTANA "
                         "OBSERVABLE (no es una medida del tap)"
                         % (ch, nombres.get(ch, ""), mv))
        else:
            r = a_voltios(mv)
            filas.append("   ch%d %-5s %9.2f de banco  ->  %.3f V  (%+.0f mV de Vref)"
                         % (ch, nombres.get(ch, ""), mv, r, (r - VREF_V) * 1000))
    return "\n".join(filas)


def guardar(nombre, datos):
    """Un JSON por ensayo, con fecha y hora al SEGUNDO. Devuelve la ruta.

    Los segundos no son decoracion: T4 tarda menos de un segundo y se corre
    varias veces seguidas comparando pares de curvas. Con resolucion de minuto,
    tres corridas se pisaban entre si y quedaba una sola, sin que nada avisara.
    Encontrado probando T4 con datos sinteticos el 2026-09-06.
    """
    os.makedirs(SALIDA, exist_ok=True)
    ruta = os.path.join(SALIDA, "%s_%s.json" % (nombre, datetime.now().strftime("%Y%m%d_%H%M%S")))
    datos.setdefault("cuando", datetime.now().isoformat(timespec="seconds"))
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(datos, f, indent=1, ensure_ascii=False)
    return ruta


def pendiente_por_codigo(puntos):
    """Ajusta una recta a (codigo, mV de banco) por minimos cuadrados.

    Devuelve (mV de banco por codigo, uV REALES por codigo, n usados). Solo usa
    los puntos cuya lectura cae en la ventana observable: fuera de ella no hay
    pendiente que medir, hay ruido, y meterlo en el ajuste corre la recta.
    """
    xs = [c for c, mv in puntos if mv is not None and lectura_valida(mv)]
    ys = [mv for c, mv in puntos if mv is not None and lectura_valida(mv)]
    n = len(xs)
    if n < 2:
        return None, None, n
    mx = sum(xs) / n
    my = sum(ys) / n
    den = sum((x - mx) ** 2 for x in xs)
    if den == 0:
        return None, None, n
    m = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den
    # delta_a_voltios devuelve VOLTIOS para una diferencia en mV de banco.
    return m, delta_a_voltios(m) * 1e6, n
