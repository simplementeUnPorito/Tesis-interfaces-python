# -*- coding: utf-8 -*-
"""EXP4c: cuanto se mueve solo el punto calibrado, y si eso justifica recalibrar.

LA PREGUNTA QUE CONTESTA
Ya esta medido que la autocalibracion es lo que hace que la mayoria de las
configuraciones existan. Eso justificaria un ajuste DE FABRICA, hecho una vez en
el banco. Lo que justifica que sea AUTOMATICA Y EN CAMPO es otra cosa: que el
punto se vaya solo.

El experimento es dejar el nodo calibrado y no tocarlo. Si el tap del LP se
queda quieto toda la noche, un trim fijo alcanzaba y la complejidad no se paga.
Si se va, no alcanza. Las dos respuestas sirven; la que no sirve es no medirlo.

COMO SE LEE EL RESULTADO
Lo que importa es el tap del LP -canal 3-, porque es el que se captura. Los
otros tres son contexto: dicen si lo que se movio fue toda la cadena o una etapa.

El criterio de aceptacion es el de Elias: el LP al minimo, y ninguna etapa
saturada nunca. Asi que el veredicto se da contra dos varas:
  - el error que la calibracion consigue cuando se la corre (unos pocos mV);
  - la distancia al riel, que es donde el nodo deja de capturar.

Uso:  python analizar_deriva.py [ruta.json]
"""
from __future__ import annotations
import sys, json, glob, os
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from escala_banco import a_voltios, VREF_V, BANCO_MIN_VALIDO_MV, BANCO_MAX_VALIDO_MV

LAB = r'C:\Github\Tesis\lab\planta'


def cargar(ruta=None):
    if ruta is None:
        cands = sorted(glob.glob(os.path.join(LAB, 'deriva_*.json')))
        if not cands:
            raise SystemExit("no hay ningun deriva_*.json en %s" % LAB)
        ruta = cands[-1]
    return ruta, json.load(open(ruta, encoding='utf-8'))


def main():
    ruta, d = cargar(sys.argv[1] if len(sys.argv) > 1 else None)
    m = d.get("muestras", [])
    print("archivo   : %s" % os.path.basename(ruta))
    print("desde     : %s" % d.get("inicio"))
    print("ganancias : PGA x%s, PGAout x%s" % (d.get("pga_x"), d.get("pgaout_x")))
    print("punto fijo: IDAC %s" % d.get("dac"))
    print("muestras  : %d" % len(m))
    if d.get("lecturas_fallidas"):
        print("            (%d lecturas perdidas, ignoradas)" % d["lecturas_fallidas"])
    if len(m) < 3:
        raise SystemExit("todavia no hay suficientes muestras para decir nada")

    t0 = datetime.fromisoformat(m[0]["t"])
    horas = (datetime.fromisoformat(m[-1]["t"]) - t0).total_seconds() / 3600.0
    print("duracion  : %.1f h" % horas)
    print()

    # Serie de cada tap en VOLTIOS REALES, que es la unidad en la que el
    # criterio esta escrito. En unidades de banco un milivolt parece poco y son
    # 19,9 mV de verdad.
    series = {}
    for ch in range(4):
        v = [(datetime.fromisoformat(s["t"]) - t0).total_seconds() / 3600.0
             for s in m if s["taps_mv"].get(str(ch)) is not None]
        y = [a_voltios(s["taps_mv"][str(ch)]) for s in m
             if s["taps_mv"].get(str(ch)) is not None]
        series[ch] = (v, [q for q in y if q is not None])

    print("%-6s %10s %10s %10s %10s" % ("tap", "inicial", "final", "deriva", "excursion"))
    print("%-6s %10s %10s %10s %10s" % ("", "mV de Vref", "mV de Vref", "mV", "mV"))
    deriva_lp = None
    for ch in range(4):
        _, y = series[ch]
        if len(y) < 2:
            continue
        ini = (y[0] - VREF_V) * 1000
        fin = (y[-1] - VREF_V) * 1000
        der = fin - ini
        exc = (max(y) - min(y)) * 1000
        nombre = {0: "PGA", 1: "BP", 2: "ADDER", 3: "LP"}[ch]
        print("ch%d %-3s %10.0f %10.0f %10.0f %10.0f" % (ch, nombre, ini, fin, der, exc))
        if ch == 3:
            deriva_lp = (der, exc, y)
    print()

    if deriva_lp is None:
        raise SystemExit("sin datos del tap del LP; no hay veredicto")

    der, exc, y = deriva_lp
    # Cuanto le quedaba al LP hasta el riel, en voltios reales.
    v_riel_bajo = a_voltios(BANCO_MIN_VALIDO_MV)
    v_riel_alto = a_voltios(BANCO_MAX_VALIDO_MV)
    margen = min(abs(y[-1] - v_riel_bajo), abs(y[-1] - v_riel_alto)) * 1000

    print("EL TAP QUE IMPORTA (LP, canal 3)")
    print("  se movio          : %.0f mV en %.1f h  (%.0f mV/h)"
          % (der, horas, der / horas if horas else 0))
    print("  excursion total   : %.0f mV" % exc)
    print("  margen al riel    : %.0f mV al cierre" % margen)
    print()

    # El veredicto. Un trim de fabrica sirve si, y solo si, lo que el punto se
    # mueve solo es chico comparado con lo que la calibracion consigue.
    print("QUE DICE ESTO DE UN TRIM DE FABRICA")
    if abs(der) < 50:
        print("  La deriva es de %.0f mV en %.1f h. Es CHICA: un ajuste fijo" % (abs(der), horas))
        print("  aguantaria una noche sin sacar la cadena de rango, y la")
        print("  justificacion de que la calibracion sea automatica NO puede")
        print("  apoyarse en la deriva de esta noche. Hay que buscarla en otro")
        print("  lado -excursion termica mas grande, o el envejecimiento- o")
        print("  aceptar que para este nodo alcanza con calibrar al desplegar.")
    else:
        print("  La deriva es de %.0f mV en %.1f h, o sea %.0f mV/h."
              % (abs(der), horas, abs(der) / horas if horas else 0))
        print("  Un trim de fabrica NO alcanza: el punto se va solo, sin que")
        print("  nadie toque nada, y el que lo devuelve es el lazo. Eso es lo")
        print("  que justifica que la calibracion sea automatica y corra en")
        print("  campo, no una vez en el banco.")
        if margen > 0:
            print("  Al ritmo medido, tocaria el riel en %.1f h." % (margen / (abs(der) / horas)))

    # La figura, si hay matplotlib. No es obligatoria para el veredicto.
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(9, 5))
        for ch, etiqueta in ((0, "PGA"), (1, "BP"), (2, "ADDER"), (3, "LP")):
            x, yy = series[ch]
            if yy:
                ax.plot(x[:len(yy)], [(q - VREF_V) * 1000 for q in yy],
                        label="ch%d %s" % (ch, etiqueta),
                        linewidth=2.0 if ch == 3 else 1.0)
        ax.axhline(0, color="k", linewidth=0.6)
        ax.set_xlabel("horas desde que se calibro")
        ax.set_ylabel("desvio de Vref [mV reales]")
        ax.set_title("Deriva del punto calibrado (PGA x%s, PGAout x%s), punto fijo"
                     % (d.get("pga_x"), d.get("pgaout_x")))
        ax.legend(); ax.grid(alpha=0.3)
        salida = os.path.join(LAB, "figuras", "deriva_%s.png"
                              % os.path.basename(ruta).replace("deriva_", "").replace(".json", ""))
        os.makedirs(os.path.dirname(salida), exist_ok=True)
        fig.tight_layout(); fig.savefig(salida, dpi=140)
        print()
        print("figura -> %s" % salida)
    except ImportError:
        pass


if __name__ == "__main__":
    main()
