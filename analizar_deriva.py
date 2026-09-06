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
    """El registro mas reciente POR FECHA DE ARCHIVO, no por nombre.

    Ordenar por nombre parecia alcanzar mientras todos se llamaban con fecha,
    pero `deriva_nocturna.json` gana alfabeticamente contra
    `deriva_20260906_0954.json` -la 'n' va despues del '2'- y el analisis salia
    sobre el registro viejo sin que nada avisara. El nombre no es una fecha.
    """
    if ruta is None:
        cands = glob.glob(os.path.join(LAB, 'deriva_*.json'))
        if not cands:
            raise SystemExit("no hay ningun deriva_*.json en %s" % LAB)
        ruta = max(cands, key=os.path.getmtime)
    return ruta, json.load(open(ruta, encoding='utf-8'))


def main():
    ruta, d = cargar(sys.argv[1] if len(sys.argv) > 1 else None)
    m = d.get("muestras", [])
    print("archivo   : %s" % os.path.basename(ruta))
    print("desde     : %s" % d.get("inicio"))
    print("ganancias : PGA x%s, PGAout x%s" % (d.get("pga_x"), d.get("pgaout_x")))
    print("calibrado : %s" % d.get("calibrado_por", d.get("dac", "?")))
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

    # ------------------------------------------------------------------
    # EL VEREDICTO, Y CUANDO NO CORRESPONDE DARLO
    #
    # Dos horas es el minimo para decir algo. Por debajo de eso lo que se ve
    # NO es deriva termica: es la cola del transitorio de la propia
    # calibracion -tau son 29,5 s, y despues de mover las referencias la
    # cadena sigue acomodandose un buen rato- mezclada con el ruido ambiente.
    # Una version anterior de esto dictaminaba sobre doce minutos y decia "la
    # deriva es CHICA" con 41 mV, que extrapolados son 257 mV/h, o sea
    # cualquier cosa menos chica.
    #
    # Y el criterio es la TASA, no el total: 41 mV en doce minutos y 41 mV en
    # ocho horas son dos mundos distintos, y el numero suelto no los separa.
    # La vara son 10 mV/h, que es el orden del error que la calibracion
    # consigue -34 mV- repartido en un turno de campo.
    # ------------------------------------------------------------------
    HORAS_MINIMAS = 2.0
    #: Vara de comparacion: el error que la calibracion consigue cuando se la
    #: corre. Todo se juzga contra esto, porque es lo que un trim fijo tendria
    #: que sostener para reemplazarla.
    ERROR_CALIBRACION_MV = 34.0

    # ------------------------------------------------------------------
    # DERIVA NO ES LO MISMO QUE VAGABUNDEO, y confundirlos cambia el argumento.
    #
    # Una RAMPA termica tiene signo constante y residuo chico contra una recta:
    # el punto se va para un lado y se queda yendo. Un VAGABUNDEO va y viene,
    # con la tasa cambiando de signo entre tramos. Y una COLA de transitorio
    # decae: su tasa se achica monotonamente.
    #
    # Los tres justifican recalibrar, pero por razones distintas y con
    # argumentos distintos, asi que hay que decir cual es. Una version anterior
    # de esto informaba solo la tasa de la recta y habria llamado "deriva de
    # 28 mV/h" a una serie cuyas tasas por tramo eran -36, +22, -47, -38 y +2.
    # ------------------------------------------------------------------
    N_TRAMOS = 5
    xs = [(datetime.fromisoformat(s_["t"]) - t0).total_seconds() / 3600.0
          for s_ in m if s_["taps_mv"].get("3") is not None]
    ys = [(a_voltios(s_["taps_mv"]["3"]) - VREF_V) * 1000
          for s_ in m if s_["taps_mv"].get("3") is not None]

    def pendiente(px, py):
        if len(px) < 3:
            return None
        mx_ = sum(px) / len(px)
        my_ = sum(py) / len(py)
        den_ = sum((x - mx_) ** 2 for x in px)
        return (sum((x - mx_) * (y - my_) for x, y in zip(px, py)) / den_) if den_ else None

    paso = max(1, len(xs) // N_TRAMOS)
    tasas = []
    for i in range(N_TRAMOS):
        a_, b_ = i * paso, ((i + 1) * paso if i < N_TRAMOS - 1 else len(xs))
        pe = pendiente(xs[a_:b_], ys[a_:b_])
        if pe is not None:
            tasas.append((xs[a_], xs[b_ - 1], pe))

    tasa_global = pendiente(xs, ys) or 0.0
    banda = max(ys) - min(ys)
    if tasas:
        res = [y - (sum(ys) / len(ys) + tasa_global * (x - sum(xs) / len(xs)))
               for x, y in zip(xs, ys)]
        residuo = (sum(r * r for r in res) / len(res)) ** 0.5
    else:
        residuo = 0.0

    print("FORMA DE LO QUE SE MUEVE")
    print("  banda recorrida   %.0f mV   (el tap anduvo entre %.0f y %.0f de Vref)"
          % (banda, min(ys), max(ys)))
    print("  recta             %.0f mV/h, con residuo RMS de %.0f mV"
          % (tasa_global, residuo))
    for a_, b_, pe in tasas:
        print("    %.2f a %.2f h    %+7.1f mV/h" % (a_, b_, pe))
    signos = set(1 if pe > 0 else -1 for _, _, pe in tasas)
    decae = all(abs(tasas[i][2]) >= abs(tasas[i + 1][2]) for i in range(len(tasas) - 1))
    print()

    print("QUE DICE ESTO DE UN TRIM DE FABRICA")
    if horas < HORAS_MINIMAS:
        print("  TODAVIA NO SE PUEDE DECIR. Van %.1f h y hacen falta %.0f."
              % (horas, HORAS_MINIMAS))
        print("  Por debajo de eso lo que se ve no es deriva sino la cola del")
        print("  transitorio de la propia calibracion mezclada con el ambiente.")
    elif decae and len(tasas) >= 3:
        print("  ES UNA COLA, NO UNA DERIVA: la tasa por tramo se achica")
        print("  monotonamente. Hay que dejar correr mas antes de concluir.")
    elif banda < ERROR_CALIBRACION_MV:
        print("  El tap se movio %.0f mV en %.1f h, MENOS que los %.0f mV que la"
              % (banda, horas, ERROR_CALIBRACION_MV))
        print("  calibracion consigue. Un trim fijo aguantaria: la justificacion")
        print("  de que sea AUTOMATICA no puede apoyarse en esto, y hay que")
        print("  escribirlo asi. Es un resultado, no un fracaso del experimento.")
    elif abs(tasa_global) * horas > 2.0 * residuo:
        # La recta explica bastante mas de lo que queda de residuo: hay una
        # tendencia real, con vagabundeo encima. Que un tramo suelto cambie de
        # signo NO la refuta, porque con %.0f mV de residuo un tramo de media
        # hora admite pendientes de cualquier signo.
        print("  HAY UNA TENDENCIA, con vagabundeo encima.")
        print("  La recta se lleva %.0f mV en %.1f h (%.0f mV/h) y encima queda un"
              % (abs(tasa_global) * horas, horas, abs(tasa_global)))
        print("  vagabundeo de %.0f mV RMS. Los dos juntos hacen la banda de %.0f mV."
              % (residuo, banda))
        if len(signos) > 1:
            print("  (Que un tramo suelto salga con el signo contrario no refuta la")
            print("   tendencia: con ese residuo, media hora admite cualquier signo.)")
        print("")
        print("  Contra los %.0f mV que la calibracion consigue, la tendencia sola"
              % ERROR_CALIBRACION_MV)
        print("  se los come en %.1f h. UN TRIM DE FABRICA NO ALCANZA para un turno"
              % (ERROR_CALIBRACION_MV / abs(tasa_global)))
        print("  de campo, y ese es el argumento que faltaba.")
        if margen > 0:
            print("  Al ritmo medido tocaria el riel en %.1f h." % (margen / abs(tasa_global)))
    elif len(signos) > 1:
        print("  ES VAGABUNDEO, NO UNA TENDENCIA: la recta solo se lleva %.0f mV en"
              % (abs(tasa_global) * horas))
        print("  %.1f h y el residuo es de %.0f mV, o sea que el punto no se va" % (horas, residuo))
        print("  para ningun lado: va y viene dentro de una banda de %.0f mV." % banda)
        print("")
        print("  Para el argumento de la tesis esto es MAS fuerte que una rampa, no")
        print("  menos: contra una rampa un trim fijo al menos se puede dimensionar")
        print("  para el promedio del turno. Contra vagabundeo no hay valor fijo")
        print("  que sirva, porque no hay un valor al que apuntar.")
    else:
        print("  ES UNA RAMPA: el signo se mantiene en todos los tramos y va a")
        print("  %.0f mV/h. Contra los %.0f mV que la calibracion consigue, se los"
              % (abs(tasa_global), ERROR_CALIBRACION_MV))
        print("  come en %.1f h." % (ERROR_CALIBRACION_MV / abs(tasa_global)))
        print("  Un trim de fabrica NO alcanza para un turno de campo.")
        if margen > 0 and abs(tasa_global) > 0:
            print("  Al ritmo medido tocaria el riel en %.1f h." % (margen / abs(tasa_global)))

    # ---- EXP4d: el ruido, que viaja en el mismo registro -------------------
    ruidos = [(datetime.fromisoformat(x["t"]), x["ruido_ch3"])
              for x in m if "ruido_ch3" in x]
    if len(ruidos) >= 2:
        rms = [r["rms_uv"] for _, r in ruidos]
        hz50 = [r["hz50_uv"] for _, r in ruidos]
        print()
        print("EXP4d - EL RUIDO DEL TAP DEL LP, con la cadena calibrada y quieta")
        print("  %d medidas a lo largo de %.1f h" % (len(ruidos), horas))
        print("  RMS      %6.0f uV de banco  (min %.0f, max %.0f)"
              % (sum(rms) / len(rms), min(rms), max(rms)))
        print("  a 50 Hz  %6.0f uV de banco  (min %.0f, max %.0f)"
              % (sum(hz50) / len(hz50), min(hz50), max(hz50)))
        deriva_rms = rms[-1] - rms[0]
        print()
        if abs(deriva_rms) < 0.25 * (sum(rms) / len(rms) or 1):
            print("  El ruido NO crece con el tiempo: la cadena calibrada no se")
            print("  degrada por estar calibrada. Cierra la objecion obvia al")
            print("  sistema, que es que corregir el offset meta ruido.")
        else:
            print("  OJO: el RMS cambio %+.0f uV entre la primera y la ultima" % deriva_rms)
            print("  medida. Hay que mirar si acompana a la deriva del punto o si")
            print("  es ruido ambiente -de dia hay gente y autos-.")

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
