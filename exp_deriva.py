# -*- coding: utf-8 -*-
"""EXP4c - ¿Se va solo el punto calibrado? Y de paso EXP4d, el ruido.

LA PREGUNTA, Y POR QUE LAS DOS RESPUESTAS SIRVEN
Ya esta medido que la autocalibracion es lo que hace que la mayoria de las
configuraciones existan: 12 de 14 arrancan con una etapa que no transmite. Pero
eso justificaria un ajuste DE FABRICA, hecho una vez en el banco.

Lo que justificaria que sea AUTOMATICA Y EN CAMPO es otra cosa: que el punto se
vaya solo. Si el tap del LP se queda quieto durante horas, un trim fijo alcanzaba
y hay que decirlo -seria un argumento EN CONTRA de la complejidad, y la tesis es
mas fuerte por haberlo medido y no por haberlo evitado-. Si se va, no alcanza.

QUE HACE
Calibra una vez, en la configuracion de campo, y despues NO TOCA NADA. Registra
los cuatro taps cada minuto y, cada cinco muestras, el ruido del tap del LP. El
ruido sale gratis y cierra la objecion obvia -que calibrar meta ruido- con una
serie de horas en vez de una medida suelta.

PREFIERE LA CALIBRACION DEL FIRMWARE, y no es un detalle: lo que la tesis afirma
es que el NODO se calibra y sostiene el punto, no que lo sostiene un punto que le
puso una PC. Si el firmware no llega, cae al procedimiento desde la PC y lo deja
anotado en el registro, para que despues nadie confunda una cosa con la otra.

LA VENTANA TERMICA
Da igual de dia o de noche: lo que se busca es la RELACION entre temperatura y
punto de operacion, y no depende del signo de la rampa. De noche la casa esta mas
quieta y la serie sale mas limpia; de dia la excursion es la misma y el ruido
ambiente mayor. Por eso el veredicto se da sobre la TENDENCIA y no sobre muestras
sueltas.

Uso:  python exp_deriva.py [--pc]      # --pc fuerza el procedimiento de la PC
"""
from __future__ import annotations
import sys
import os
import time
import json
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from testbench.core.console import Console          # noqa: E402
from testbench.core.session import Session          # noqa: E402
from testbench.core.lab import Lab                  # noqa: E402
from escala_banco import a_voltios, VREF_V, lectura_valida   # noqa: E402
from asentamiento import esperar_quieto, TAU_S      # noqa: E402

PUERTO = os.environ.get("BANCO_COM", "COM8")
CAMPO_PGA, CAMPO_PGAOUT = 8, 0
PERIODO_S = 60
CADA_CUANTO_RUIDO = 5
PLAZO_CAL_S = 1500.0


def log(t=""):
    print(t, flush=True)


def calibrar_con_firmware(s, lab):
    """Devuelve (ok, segundos) o (None, 0) si ni siquiera contesto."""
    log("calibrando con el firmware del nodo (`cal`)...")
    t0 = time.time()
    ok = None
    for ln in s.raw("cal", idle=PLAZO_CAL_S, timeout=PLAZO_CAL_S + 20,
                    until=lambda l: l.startswith("#CAL")):
        if ln.startswith("#CAL"):
            try:
                ok = int(ln.split()[1])
            except (IndexError, ValueError):
                pass
            log("  " + ln)
    return ok, time.time() - t0


def calibrar_con_pc(lab):
    from buscar_max_pgaout import gruesa, refinar, CANALES
    log("calibrando desde la PC (procedimiento de respaldo)...")
    dac = gruesa(lab, TAU_S, lambda t: log("  " + t))
    if dac is None:
        return None
    return refinar(lab, dac, 2 * TAU_S, 2, lambda t: log("  " + t))


def main(forzar_pc=False):
    ruta = r'C:\Github\Tesis\lab\planta\deriva_%s.json' % datetime.now().strftime('%Y%m%d_%H%M')
    os.makedirs(os.path.dirname(ruta), exist_ok=True)

    c = Console(PUERTO)
    c.open()
    time.sleep(1.5)
    s = Session(c)
    lab = Lab(s)

    if not lab.set_adc_config(1):
        c.close()
        raise SystemExit("ABORTA: el firmware no acepto el ADC en +-2,5 V")
    lab.set_gain("pga", CAMPO_PGA)
    lab.set_gain("pgaout", CAMPO_PGAOUT)

    mal = [k for k in range(4) if not lab.set_idac(k, 0)]
    if mal:
        c.close()
        raise SystemExit("ABORTA: el firmware rechazo los IDAC %s" % mal)

    quien = None
    if not forzar_pc:
        ok, seg = calibrar_con_firmware(s, lab)
        v, _, _ = esperar_quieto(lab, log=lambda t: log("  " + t))
        # El criterio no es el `ok` del firmware sino DONDE quedo el tap que se
        # captura: un ok=1 con el LP lejos de Vref no sirve para este ensayo.
        if v.get(3) is not None and lectura_valida(v[3]) and \
           abs(a_voltios(v[3]) - VREF_V) * 1000 < 300:
            quien = "firmware"
            log("  el nodo se calibro solo: LP a %+.0f mV de Vref en %.0f s"
                % ((a_voltios(v[3]) - VREF_V) * 1000, seg))
        else:
            log("  el firmware no dejo el LP en rango util; se cae al respaldo")

    if quien is None:
        if calibrar_con_pc(lab) is None:
            c.close()
            raise SystemExit("ABORTA: no se pudo calibrar de ninguna de las dos formas")
        quien = "pc"

    v, _, _ = esperar_quieto(lab, log=None)
    reg = {"inicio": datetime.now().isoformat(timespec="seconds"),
           "pga_x": 50, "pgaout_x": 1,
           "calibrado_por": quien,
           "nota": "punto FIJO toda la corrida; no se recalibra",
           "punto_inicial_mv": {str(k): v.get(k) for k in range(4)},
           "muestras": [], "lecturas_fallidas": 0}

    log("")
    log("calibrado por: %s. Registrando cada %d s en %s"
        % (quien, PERIODO_S, os.path.basename(ruta)))
    log("%8s %9s %9s %9s %9s   LP real" % ("hora", "ch0", "ch1", "ch2", "ch3"))

    n = 0
    try:
        while True:
            v = {}
            for ch in range(4):
                try:
                    p = lab.measure_dc(ch, 3)
                    v[ch] = (p.mean_uv / 1000.0) if (p and p.ok) else None
                except Exception:
                    v[ch] = None
                    reg["lecturas_fallidas"] += 1
            fila = {"t": datetime.now().isoformat(timespec="seconds"),
                    "taps_mv": {str(k): v[k] for k in range(4)}}
            if n % CADA_CUANTO_RUIDO == 0:
                try:
                    a = lab.measure_ac(3)
                    if a and a.ok:
                        fila["ruido_ch3"] = {"rms_uv": a.rms_uv, "pp_uv": a.pp_uv,
                                             "hz50_uv": a.hz50_uv}
                except Exception:
                    reg["lecturas_fallidas"] += 1
                r = a_voltios(v[3]) if v.get(3) is not None else None
                log("%s %s   %s" % (
                    datetime.now().strftime("%H:%M:%S"),
                    " ".join(("%9.2f" % v[k]) if v.get(k) is not None else "%9s" % "-"
                             for k in range(4)),
                    ("%.3f V" % r) if r is not None else ""))
            reg["muestras"].append(fila)
            with open(ruta, "w", encoding="utf-8") as f:
                json.dump(reg, f, indent=1)
            n += 1
            time.sleep(PERIODO_S)
    except KeyboardInterrupt:
        log("")
        log("cortado a mano con %d muestras" % len(reg["muestras"]))
    finally:
        c.close()
        log("datos -> %s" % ruta)
        log("analisis: python analizar_deriva.py")


if __name__ == "__main__":
    main("--pc" in sys.argv[1:])
