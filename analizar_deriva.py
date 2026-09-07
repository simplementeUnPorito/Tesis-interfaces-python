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
import sys, json, glob, os, math
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from escala_banco import a_voltios, VREF_V, BANCO_MIN_VALIDO_MV, BANCO_MAX_VALIDO_MV

LAB = r'C:\Github\Tesis\lab\planta'


def temperatura_exterior(instantes):
    """Interpola el proxy horario de Open-Meteo sin extrapolarlo."""
    candidatos = glob.glob(os.path.join(LAB, "temperatura_exterior_*.json"))
    if not candidatos:
        return [], None
    with open(max(candidatos, key=os.path.getmtime), encoding="utf-8") as fh:
        datos = json.load(fh)
    serie = sorted((datetime.fromisoformat(x["t"]), float(x["temperature_2m_c"]))
                   for x in datos.get("horas", []))
    valores = []
    j = 0
    for instante in instantes:
        while j + 1 < len(serie) and serie[j + 1][0] < instante:
            j += 1
        if j + 1 >= len(serie) or instante < serie[j][0]:
            valores.append(None)
            continue
        ta, va = serie[j]
        tb, vb = serie[j + 1]
        ancho = (tb - ta).total_seconds()
        f = (instante - ta).total_seconds() / ancho if ancho else 0.0
        valores.append(va + f * (vb - va))
    return valores, datos


def correlacion(a, b):
    pares = [(x, y) for x, y in zip(a, b) if y is not None]
    if len(pares) < 3:
        return None
    aa, bb = zip(*pares)
    ma, mb = sum(aa) / len(aa), sum(bb) / len(bb)
    num = sum((x - ma) * (y - mb) for x, y in pares)
    da = sum((x - ma) ** 2 for x in aa)
    db = sum((y - mb) ** 2 for y in bb)
    return num / math.sqrt(da * db) if da and db else None


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
    nulos_por_canal = [sum(1 for x in m if x["taps_mv"].get(str(ch)) is None)
                       for ch in range(4)]
    nulos = sum(nulos_por_canal)
    if nulos:
        print("nulos     : %d de %d taps (ch0=%d, ch1=%d, ch2=%d, ch3=%d)"
              % (nulos, 4 * len(m), *nulos_por_canal))
    contador = d.get("lecturas_fallidas", 0)
    if contador != nulos:
        print("            contador legado=%d; no incluia respuestas vacias" % contador)
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

    # ------------------------------------------------------------------
    # ¿LA BANDA CRECE O SE SATURA? Es el discriminador que faltaba.
    #
    # Comparar la recta contra su residuo NO distingue una rampa de un
    # vagabundeo acotado, porque a un vagabundeo acotado tambien se le puede
    # ajustar una recta y le da pendiente distinta de cero. Lo que los separa es
    # otra cosa: si el punto se VA, la banda crece sin parar y la pendiente se
    # mantiene; si VAGABUNDEA dentro de un rango, la banda se satura y la
    # pendiente ajustada se achica sola a medida que entran muestras.
    #
    # La lectura provisional a 7,1 h parecia una banda cerrada, pero el registro
    # posterior cruzo el origen y siguio creciendo. Es el ejemplo de por que
    # esta clasificacion no debe convertirse en conclusion antes de cerrar la
    # ventana temporal del experimento.
    # ------------------------------------------------------------------
    bandas = []
    for frac in (0.2, 0.4, 0.6, 0.8, 1.0):
        k = max(3, int(len(ys) * frac))
        bandas.append((xs[k - 1], max(ys[:k]) - min(ys[:k])))
    # ¿Cuanto agrego el ultimo 40 % del registro a la banda?
    crecio_al_final = bandas[-1][1] - bandas[-3][1]
    se_saturo = (bandas[-1][1] > 0 and
                 crecio_al_final < 0.10 * bandas[-1][1] and horas >= HORAS_MINIMAS)

    print("CRECIMIENTO DE LA BANDA")
    for h, b in bandas:
        print("  hasta %5.1f h    %5.0f mV" % (h, b))
    print("  el ultimo 40 %% del registro le agrego %.0f mV" % crecio_al_final)
    print()

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
    elif se_saturo:
        print("  NO ES UNA RAMPA SOSTENIDA. Que clase de movimiento es, todavia")
        print("  no se puede decir con este registro.")
        print("  La banda dejo de crecer -el ultimo 40 % del registro le agrego")
        print("  %.0f mV sobre %.0f- y la pendiente ajustada se viene achicando"
              % (crecio_al_final, bandas[-1][1]))
        print("  sola: es lo que le pasa a una recta ajustada a algo que va y")
        print("  viene. Llamarlo 'deriva de %.0f mV/h' seria afirmar algo que no"
              % abs(tasa_global))
        print("  esta pasando.")
        print("")
        print("  Pero quedan DOS explicaciones en pie y este registro no las separa:")
        print("    - vagabundeo acotado, el punto yendo y viniendo en un rango;")
        print("    - un asentamiento lento hacia una asintota, con constante de")
        print("      HORAS -mucho mayor que los 29,5 s del polo conocido, pero")
        print("      compatible con la absorcion dielectrica del electrolitico-.")
        print("  Separarlas necesita mas horas y la temperatura anotada en")
        print("  paralelo: si el punto la sigue, es lo segundo.")
        print("")
        print("  IGUAL UN TRIM DE FABRICA NO ALCANZA, en las dos:")
        print("  el punto recorre %.0f mV, o sea %.1f veces los %.0f mV que la"
              % (bandas[-1][1], bandas[-1][1] / ERROR_CALIBRACION_MV, ERROR_CALIBRACION_MV))
        print("  calibracion consigue. Contra una rampa un valor fijo al menos se")
        print("  puede dimensionar para el promedio del turno; contra vagabundeo")
        print("  no hay valor al que apuntar, porque el punto no tiene uno.")
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
        print("  La recta resume solamente esta ventana; no se extrapola hasta el riel.")
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
        print("  La recta resume solamente esta ventana; no se extrapola hasta el riel.")

    instantes_lp = [datetime.fromisoformat(x["t"]) for x in m
                    if x["taps_mv"].get("3") is not None]
    temp, _ = temperatura_exterior(instantes_lp)
    corr = correlacion(ys, temp) if temp else None
    temp_validas = [x for x in temp if x is not None]
    if corr is not None and temp_validas:
        print()
        print("TEMPERATURA EXTERIOR (PROXY, NO SENSOR DEL BANCO)")
        print("  %.1f a %.1f C; correlacion contemporanea con LP: r=%+.2f"
              % (min(temp_validas), max(temp_validas), corr))
        print("  Es un indicio meteorologico, no prueba causalidad termica:")
        print("  falta medir la temperatura junto a la placa.")

    # ---- EXP4d: el ruido, que viaja en el mismo registro -------------------
    ruidos = [(datetime.fromisoformat(x["t"]), x["ruido_ch3"])
              for x in m if "ruido_ch3" in x]
    if len(ruidos) >= 2:
        rms = [r["rms_uv"] for _, r in ruidos]
        hz50 = [r["hz50_uv"] for _, r in ruidos]
        ruido_esperado = (len(m) + 4) // 5
        print()
        # LA MEDIANA, NO EL PROMEDIO. Un solo pico -8010 uV a las 5,2 h, veinte
        # veces el resto, casi seguro alguien caminando cerca- corre el promedio
        # de 400 a 493 uV, un 23 %. El ruido de la CADENA es lo que se quiere
        # medir, y ese numero no puede depender de si paso un auto.
        def mediana(v):
            o = sorted(v)
            n = len(o)
            return o[n // 2] if n % 2 else (o[n // 2 - 1] + o[n // 2]) / 2.0

        med_rms, med_hz = mediana(rms), mediana(hz50)
        # Cuantas se salen mucho de la mediana: son ambiente, no cadena.
        picos = [r for r in rms if r > 2.0 * med_rms]

        print("EXP4d - EL RUIDO DEL TAP DEL LP, con la cadena calibrada y quieta")
        print("  %d medidas a lo largo de %.1f h" % (len(ruidos), horas))
        if len(ruidos) != ruido_esperado:
            print("  faltan %d de %d bloques previstos" %
                  (ruido_esperado - len(ruidos), ruido_esperado))
        print("  RMS      %6.0f uV de banco (mediana)   min %.0f" % (med_rms, min(rms)))
        print("  a 50 Hz  %6.0f uV de banco (mediana)" % med_hz)
        if picos:
            print("  %d medida%s por encima del doble de la mediana (hasta %.0f uV):"
                  % (len(picos), "s" if len(picos) > 1 else "", max(rms)))
            print("     son ruido AMBIENTE -alguien caminando, un auto en la lomada-,")
            print("     no de la cadena. Por eso se informa la mediana y no el promedio,")
            print("     que con esos picos se corre un %.0f %%."
                  % (100 * (sum(rms) / len(rms) - med_rms) / med_rms))
        deriva_rms = mediana(rms[len(rms) // 2:]) - mediana(rms[:len(rms) // 2])
        print()
        # Se comparan las medianas de las dos mitades, por lo mismo.
        if abs(deriva_rms) < 0.25 * (med_rms or 1):
            print("  El ruido NO crece con el tiempo: la cadena calibrada no se")
            print("  degrada por estar calibrada. Cierra la objecion obvia al")
            print("  sistema, que es que corregir el offset meta ruido.")
        else:
            print("  OJO: la mediana del RMS cambio %+.0f uV entre la primera" % deriva_rms)
            print("  mitad del registro y la segunda. Hay que mirar si acompana al")
            print("  movimiento del punto o si es ruido ambiente.")

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
