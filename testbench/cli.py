"""Modo terminal del banco: automático para scripts, interactivo para el banco.

Automático, que es lo que se invoca sin mirar:

    python -m testbench run --json artifacts/corrida.json --figs artifacts/figs

Devuelve 0 si la placa está apta y la cobertura es completa, 1 si hay FAIL o la
corrida quedó trunca, 2 ante un error de puerto y 3 si no hubo FAIL pero la
corrida fue parcial a propósito por PSoC ausente. Son los mismos códigos que
``autotest_runner.py`` para que un script no tenga que aprender dos convenios.

Interactivo:

    python -m testbench consola

Sin placa a mano, todo se puede revisar con una captura guardada:

    python -m testbench replay corrida.log --figs /tmp/figs
"""

from __future__ import annotations

import argparse
import os
import queue
import re
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from .core import console as con
from .core.checklist import ChecklistParser, evaluate, fmt_mv
from .core.session import GROUPS, INTERACTIVE, Session

# --------------------------------------------------------------------------
# Color
# --------------------------------------------------------------------------
_ANSI = {
    "PASS": "\033[32m",
    "FAIL": "\033[31m",
    "WARN": "\033[33m",
    "SKIP": "\033[90m",
    "INFO": "\033[36m",
    "TX": "\033[35m",
    "RX": "\033[0m",
    "PSoC": "\033[36m",
    "--": "\033[90m",
    "bold": "\033[1m",
    "off": "\033[0m",
}
_use_color = True


def color(texto: str, clave: str) -> str:
    if not _use_color:
        return texto
    return f"{_ANSI.get(clave, '')}{texto}{_ANSI['off']}"


def _init_color(forzar_off: bool) -> None:
    global _use_color
    _use_color = not forzar_off and sys.stdout.isatty()
    if _use_color and os.name == "nt":
        # Habilitar secuencias ANSI en la consola clásica de Windows.
        try:
            import ctypes

            k = ctypes.windll.kernel32
            k.SetConsoleMode(k.GetStdHandle(-11), 7)
        except Exception:
            _use_color = False


# --------------------------------------------------------------------------
# Impresión
# --------------------------------------------------------------------------
def print_item(item) -> None:
    codigo = f"[{item.code}]".ljust(8)
    nombre = item.name[:44].ljust(44, ".")
    print(f"{codigo} {nombre} {color(item.verdict.ljust(4), item.verdict)}  {item.detail}")


def print_verdict(result) -> None:
    v = result.verdict
    print()
    print(color("RESUMEN", "bold"), result.summary())
    if v["fails"]:
        print("  FAIL       :", ", ".join(v["fails"]))
    if v["warns"]:
        print("  WARN       :", ", ".join(v["warns"]))
    for p in v["problems"]:
        print("  PROBLEMA   :", p)
    print("  COBERTURA  :", v["coverage"])
    print("  DURACION   :", f"{result.seconds:.1f} s")
    if not result.completed:
        print(color("  La corrida no llego al #JSON: quedo trunca.", "FAIL"))


def print_measurements(parser: ChecklistParser) -> None:
    m = parser.meas
    if any(v is not None for fila in m.d2 for v in fila):
        from .core.checklist import D2_MIN_SLOPE_UV_PLACA, STAGE_NAMES

        print()
        print(color("Diagonal de D2 con el umbral de esta placa", "bold"),
              f"(>= {D2_MIN_SLOPE_UV_PLACA:.0f} uV/codigo)")
        for nombre, pend, ok in m.d2_veredicto_placa():
            txt = "sin dato" if pend is None else f"{pend:9.1f} uV/cod"
            print(f"  {nombre:<12} {txt}  {color('OK' if ok else 'BAJA', 'PASS' if ok else 'FAIL')}")
    if m.d6:
        print()
        print(color("Piso de ruido por tap", "bold"))
        for tap in sorted(m.d6):
            d = m.d6[tap]
            print(f"  ch{tap}  RMS {d.get('rms_uv', 0):8.0f} uV   "
                  f"pp {d.get('pp_uv', 0):8.0f} uV   50Hz {d.get('hz50_uv', 0):6.0f} uV")
    if m.d7:
        print()
        print(color("Intentos de D7", "bold"))
        for t in m.d7:
            print(f"  #{t['intento']:<3} {color(t['verdict'].ljust(4), t['verdict'])} "
                  f"pico {str(t['pico']):>6}  fondo pp {str(t['fondo_pp']):>6}  {t['polaridad'] or ''}")


# --------------------------------------------------------------------------
# Salidas
# --------------------------------------------------------------------------
def write_outputs(args, result, sess: Optional[Session], taps=None, started_at="") -> None:
    from .core import evidence, figures

    parser = result.parser
    if args.json:
        data = evidence.build(
            result,
            port=getattr(args, "port", "") or "",
            started_at=started_at or evidence.utc_now(),
            hw_profile=dict(parser.hw),
            taps=taps,
            note=getattr(args, "note", None),
        )
        p = evidence.save(data, args.json)
        print(f"Evidencia  : {p}")
    if args.transcript and sess is not None:
        p = evidence.save_transcript(sess.console.transcript.text(), args.transcript)
        print(f"Transcript : {p}")
    if args.figs:
        rutas = figures.save_all(
            args.figs, parser.items, result.verdict, parser.meas,
            taps=taps if taps else None,
        )
        for r in rutas:
            print(f"Figura     : {r}")


def exit_code(result) -> int:
    v = result.verdict
    if not v["ok"]:
        return 1
    return 3 if v["partial"] else 0


# --------------------------------------------------------------------------
# Conexión
# --------------------------------------------------------------------------
def open_session(args, verbose: bool = True) -> tuple[con.Console, Session]:
    puerto = args.port or con.default_port()
    if not puerto:
        p = con.find_ports()
        raise SystemExit(
            "No se pudo elegir el puerto solo. Detectados: "
            f"ESP={p['esp'] or '-'} PSoC={p['psoc'] or '-'} otros={p['otros'] or '-'}. "
            "Indicar con --port."
        )
    args.port = puerto
    c = con.Console(puerto)
    print(f"Abriendo {puerto} a {con.BAUD} baud (abrir resetea el ESP; esperando su arranque)...")
    listo = c.open(wait_ready=True, timeout=args.boot_timeout)
    if not listo:
        print(color("Aviso: no se vio el banner de arranque del autotest.", "WARN"))
        print("       Puede que el ESP tenga el firmware de campo en vez de slaveTest.")

    def on_line(linea: str) -> None:
        if verbose:
            print("  " + linea)

    def on_phase(texto: str) -> None:
        print(color(f"-- {texto}", "INFO"))

    sess = Session(c, on_line=None if verbose else None, on_item=print_item, on_phase=on_phase)
    return c, sess


# --------------------------------------------------------------------------
# Subcomandos
# --------------------------------------------------------------------------
def cmd_ports(args) -> int:
    p = con.find_ports()
    print(color("Puertos detectados", "bold"))
    print(f"  ESP32 (consola del autotest) : {', '.join(p['esp']) or '(ninguno)'}")
    print(f"  PSoC  (KitProg, no sirve aca): {', '.join(p['psoc']) or '(ninguno)'}")
    print(f"  Otros                        : {', '.join(p['otros']) or '(ninguno)'}")
    elegido = con.default_port()
    print(f"\n  Se usaria por defecto: {elegido or '(ambiguo: usar --port)'}")
    return 0 if elegido else 1


def cmd_probe(args) -> int:
    c, sess = open_session(args)
    try:
        link = sess.probe()
        hw = sess.hw()
        estado = "ARRIBA" if link.up else "CAIDO"
        print()
        print(color(f"Enlace con el PSoC: {estado}", "PASS" if link.up else "FAIL"))
        print(f"  tramas ok={link.frames_ok} malas={link.frames_bad} "
              f"pings={link.pings} diag={link.diags} overruns={link.overruns}")
        print(color("Perfil de hardware", "bold"),
              "  ".join(f"{k}={v}" for k, v in sorted(hw.items())) or "(sin respuesta)")
        return 0 if link.up else 1
    finally:
        c.close()


def cmd_run(args) -> int:
    from .core import evidence

    started = evidence.utc_now()
    c, sess = open_session(args)
    try:
        if args.arm and not args.group:
            pass
        if args.group:
            result = sess.run_group(args.group, arm_first=not args.no_arm)
        else:
            result = sess.run_full()
        print_verdict(result)
        print_measurements(result.parser)
        write_outputs(args, result, sess, started_at=started)
        return exit_code(result)
    finally:
        c.close()


def cmd_tap(args) -> int:
    from .core import evidence

    started = evidence.utc_now()
    c, sess = open_session(args)
    try:
        print()
        print(color("D7 necesita que golpees el suelo al lado del geofono.", "bold"))
        print(f"Se van a hacer {args.repeat} intentos seguidos. Golpea a ritmo parejo,")
        print("uno cada dos segundos: cada intento captura 1,47 s y varios caeran bien.")
        print()
        taps = sess.tap(repeat=args.repeat, arm_first=not args.no_arm)
        buenos = [t for t in taps if t["verdict"] == "PASS"]
        print()
        for t in taps:
            print(f"  #{t['intento']:<3} {color(t['verdict'].ljust(4), t['verdict'])} "
                  f"pico {str(t['pico']):>6}  fondo pp {str(t['fondo_pp']):>6}  "
                  f"{t['polaridad'] or ''}")
        print()
        if buenos:
            polaridades = {t["polaridad"] for t in buenos}
            print(color(f"{len(buenos)} de {len(taps)} intentos con golpe adentro.", "PASS"))
            print(f"Polaridad de la primera excursion: {', '.join(sorted(polaridades))}")
        else:
            print(color("Ningun intento capturo un golpe.", "FAIL"))
            print("Si todos dicen 'no se pudo capturar el fondo', falto armar el SYNC: "
                  "correr el grupo B en el mismo arranque del ESP.")

        # D7 no emite #JSON, así que se arma un resultado equivalente a mano.
        from .core.session import RunResult

        result = RunResult(
            command="tap",
            parser=sess.parser,
            verdict=evaluate(sess.parser),
            seconds=0.0,
            completed=bool(buenos),
        )
        write_outputs(args, result, sess, taps=taps, started_at=started)
        return 0 if buenos else 1
    finally:
        c.close()


def cmd_hw(args) -> int:
    c, sess = open_session(args)
    try:
        if args.parte is None:
            hw = sess.hw()
        else:
            hw = sess.set_hw(args.parte, args.valor)
        nombres = {0: "ausente", 1: "presente", 2: "auto"}
        print()
        print(color("Perfil de hardware presente", "bold"))
        for k in ("oled", "btn", "geo", "sd", "psoc"):
            if k in hw:
                print(f"  {k:<6} = {hw[k]}  ({nombres.get(hw[k], '?')})")
        return 0
    finally:
        c.close()


# --------------------------------------------------------------------------
# Modo manual
# --------------------------------------------------------------------------
def _lab(args):
    """Abre la consola y devuelve (console, session, lab)."""
    from .core.lab import Lab

    c, sess = open_session(args, verbose=False)
    return c, sess, Lab(sess)


def _print_dc(pt) -> None:
    from .core.lab import SETTLE_MS, describe_tap

    if pt is None:
        print(color("  sin respuesta del PSoC", "FAIL"))
        return
    estado = "ok" if pt.ok else color("ERROR", "FAIL")
    print(f"  ch{pt.ch} {describe_tap(pt.ch):<9} "
          f"{fmt_mv(pt.mean_uv):>14}   pp {pt.pp_uv:>8,} uV   "
          f"(asentamiento {SETTLE_MS[pt.settle_sel]} ms)  {estado}".replace(",", " "))


def cmd_taps(args) -> int:
    """Los cuatro taps de una: el estado completo de la cadena en un vistazo."""
    c, sess, lab = _lab(args)
    try:
        print()
        print(color("Reposo DC de los taps", "bold"))
        puntos = lab.read_all_taps(settle_sel=args.settle)
        for ch in sorted(puntos):
            _print_dc(puntos[ch])
        return 0 if len(puntos) == 4 else 1
    finally:
        c.close()


def cmd_dc(args) -> int:
    c, sess, lab = _lab(args)
    try:
        print()
        _print_dc(lab.measure_dc(args.canal, args.settle))
        return 0
    finally:
        c.close()


def cmd_ac(args) -> int:
    from .core.lab import AC_SAMPLES, describe_tap

    c, sess, lab = _lab(args)
    try:
        pt = lab.measure_ac(args.canal, args.n)
        print()
        if pt is None:
            print(color("  sin respuesta del PSoC", "FAIL"))
            return 1
        print(color(f"Medicion AC del tap ch{pt.ch} ({describe_tap(pt.ch)})", "bold"),
              f"sobre {AC_SAMPLES[pt.n_sel]} muestras")
        print(f"  media {fmt_mv(pt.mean_uv):>14}")
        print(f"  RMS   {pt.rms_uv:>10,} uV".replace(",", " "))
        print(f"  pp    {pt.pp_uv:>10,} uV".replace(",", " "))
        print(f"  50 Hz {pt.hz50_uv:>10,} uV".replace(",", " "))
        return 0 if pt.ok else 1
    finally:
        c.close()


def cmd_idac(args) -> int:
    from .core.lab import describe_stage
    from .core.checklist import LSB_UV_BY_STAGE

    c, sess, lab = _lab(args)
    try:
        ok = lab.set_idac(args.etapa, args.codigo)
        print()
        if ok:
            print(color(f"IDAC de {describe_stage(args.etapa)} en el codigo "
                        f"{args.codigo}", "PASS"))
            lsb_uv = LSB_UV_BY_STAGE[args.etapa]
            print(f"  son {args.codigo * lsb_uv / 1000.0:.1f} mV sobre Vref "
                  f"en esta placa ({lsb_uv:.1f} uV por codigo)")
        else:
            print(color("  el PSoC no acepto la escritura", "FAIL"))
        if args.medir:
            print()
            print(color("Taps despues del cambio", "bold"))
            for ch, pt in sorted(lab.read_all_taps().items()):
                _print_dc(pt)
        return 0 if ok else 1
    finally:
        c.close()


def cmd_gain(args) -> int:
    from .core.lab import GAIN_CODES

    c, sess, lab = _lab(args)
    try:
        ok = lab.set_gain(args.cual, args.codigo)
        print()
        print(color(f"{args.cual} en codigo {args.codigo} "
                    f"({GAIN_CODES[args.codigo]}x)", "PASS" if ok else "FAIL"))
        return 0 if ok else 1
    finally:
        c.close()


def cmd_mon(args) -> int:
    """Osciloscopio lento sobre un tap. Se corta con Ctrl+C."""
    from .core.lab import describe_tap

    c, sess, lab = _lab(args)
    cortar = {"si": False}

    def stop() -> bool:
        return cortar["si"]

    def on_sample(m) -> None:
        print(f"  {m.t_ms / 1000.0:7.2f} s   {fmt_mv(m.mean_uv):>14}   "
              f"pp {m.pp_uv:>7,} uV".replace(",", " "))

    try:
        print()
        print(color(f"Monitor del tap ch{args.canal} ({describe_tap(args.canal)})", "bold"),
              f"cada ~{args.periodo} ms, {args.n} muestras. Ctrl+C corta.")
        print()
        try:
            muestras = lab.monitor(args.canal, args.periodo, args.n,
                                   on_sample=on_sample, stop=stop)
        except KeyboardInterrupt:
            cortar["si"] = True
            muestras = []
            print(color("\n  cortado", "WARN"))
        if muestras:
            vals = [m.mean_uv for m in muestras]
            print()
            print(f"  {len(muestras)} muestras   "
                  f"min {min(vals) / 1000.0:.3f} mV   max {max(vals) / 1000.0:.3f} mV   "
                  f"excursion {(max(vals) - min(vals)):,} uV".replace(",", " "))
            if args.figs:
                from .core import figures
                from pathlib import Path as _P
                import matplotlib.pyplot as _plt

                d = _P(args.figs); d.mkdir(parents=True, exist_ok=True)
                f = figures.fig_monitor(muestras, ch=args.canal)
                ruta = d / f"monitor_ch{args.canal}.png"
                f.savefig(ruta, facecolor=figures.SURFACE, bbox_inches="tight")
                _plt.close(f)
                print(f"  figura: {ruta}")
        return 0
    finally:
        c.close()


def cmd_sweep(args) -> int:
    """Barrido de un IDAC midiendo los taps: la matriz D2, pero con la curva entera."""
    from .core.lab import describe_stage, describe_tap

    c, sess, lab = _lab(args)
    try:
        puntos = len(range(args.lo, args.hi + 1, args.paso))
        canales = 4 if args.canal < 0 else 1
        print()
        print(color(f"Barrido del IDAC de {describe_stage(args.etapa)}", "bold"),
              f"de {args.lo} a {args.hi} paso {args.paso}")
        print(f"  {puntos} puntos x {canales} canal(es), ~500 ms cada medicion: "
              f"unos {puntos * canales * 0.6:.0f} s")
        print()

        def on_point(ch: int, code: int, media: int) -> None:
            print(f"  codigo {code:>4}  ch{ch}  {media / 1000.0:10.3f} mV")

        sw = lab.sweep(args.etapa, args.lo, args.hi, args.paso, args.canal,
                       on_point=on_point)
        print()
        print(color("Pendientes ajustadas por minimos cuadrados", "bold"))
        for ch in sorted(sw.points):
            pend = sw.slope_uv_per_code(ch)
            gan = sw.gain_from_reference(ch)
            if pend is None:
                continue
            print(f"  ch{ch} {describe_tap(ch):<9} {pend:>10.1f} uV/codigo   "
                  f"ganancia {gan:>8.3f}x   ({len(sw.points[ch])} puntos)")
        if sw.final_code is not None:
            print(f"\n  El IDAC quedo en el codigo {sw.final_code}.")
        if args.figs:
            from .core import figures
            from pathlib import Path as _P
            import matplotlib.pyplot as _plt

            d = _P(args.figs); d.mkdir(parents=True, exist_ok=True)
            f = figures.fig_sweep(sw)
            ruta = d / f"sweep_etapa{args.etapa}.png"
            f.savefig(ruta, facecolor=figures.SURFACE, bbox_inches="tight")
            _plt.close(f)
            print(f"  figura: {ruta}")
        return 0 if sw.points else 1
    finally:
        c.close()


# `  12.345  RX   [B1] Subida I2C ...` -> `[B1] Subida I2C ...`
TRANSCRIPT_RE = re.compile(r"^\s*\d+\.\d{3}\s{2}(TX|RX|PSoC|--)\s{2,}(.*)$")


def normalize_capture(texto: str) -> str:
    """Acepta tanto la salida cruda del firmware como un transcript del banco.

    El transcript lleva hora y dirección adelante de cada línea; sin sacarlas,
    el parser no reconoce un solo ítem y una corrida perfecta se reporta como
    trunca. Lo que NO se acepta es la salida por pantalla del propio CLI: esa
    reformatea el RESUMEN y ya no es lo que dijo el firmware.
    """
    salida = []
    for linea in texto.splitlines():
        m = TRANSCRIPT_RE.match(linea)
        salida.append(m.group(2) if m else linea)
    return "\n".join(salida)


def cmd_replay(args) -> int:
    """Evalúa una captura guardada. No toca hardware."""
    texto = normalize_capture(
        Path(args.archivo).read_text(encoding="utf-8", errors="replace"))
    parser = ChecklistParser()
    parser.feed_many(texto)
    v = evaluate(parser)

    for it in parser.items:
        print_item(it)

    from .core.session import RunResult

    result = RunResult(
        command="replay",
        parser=parser,
        verdict=v,
        seconds=0.0,
        completed=parser.run_finished,
    )
    print_verdict(result)
    print_measurements(parser)
    args.port = f"(replay de {Path(args.archivo).name})"
    write_outputs(args, result, None)
    return exit_code(result)


def cmd_selftest(args) -> int:
    from .core.checklist import self_test

    return self_test()


# --------------------------------------------------------------------------
# Consola interactiva
# --------------------------------------------------------------------------
AYUDA = """
Comandos del banco (además de todos los del firmware, que pasan tal cual):

  a b c d        corre ese grupo (c y d arman el SYNC antes si hace falta)
  run            corrida completa
  tap [N]        D7 N veces (por defecto 1)
  botones        los cuatro pulsadores del ESP
  boton          el pulsador del PSoC
  probe          estado del enlace con el PSoC
  hw             perfil de hardware; `hw geo 1` lo cambia
  diag on|off    eco de los eventos que manda el PSoC
  reset          resetea el ESP (desarma el SYNC)
  figs [DIR]     escribe las figuras de lo que se lleva medido
  save [ARCH]    guarda la evidencia JSON
  log [ARCH]     guarda el transcript completo con hora y direccion
  medidas        imprime D2/D6/D7 de la corrida actual

  -- modo manual (no necesitan el SYNC armado) --
  taps           reposo DC de los cuatro taps
  dc N [S]       una medida DC del canal N
  ac N [S]       media, RMS, pp y 50 Hz del canal N
  idac E C       fija el IDAC de la etapa E en el codigo C
  pga C          ganancia del PGA de entrada
  pgaout C       ganancia de la etapa entre el sumador y el pasabajos
  mon N [ms] [n] osciloscopio lento sobre el canal N
  sweep E lo hi paso [N]   barre un IDAC y ajusta la pendiente
  ayuda          esta lista
  salir          cerrar

Cualquier otra cosa se le manda al firmware tal cual.
"""


def cmd_consola(args) -> int:
    from .core import evidence, figures

    c, sess = open_session(args, verbose=False)

    # El eco en vivo se hace desde el callback del transcript, así se ve
    # también lo que se manda, no sólo lo que llega.
    def on_entry(e: con.Entry) -> None:
        if e.direction == con.TX:
            print(color(f"  > {e.text}", "TX"))
        elif e.direction == con.INFO:
            print(color(f"  . {e.text}", "--"))
        elif e.direction == con.PSOC:
            print(color(f"  | {e.text}", "PSoC"))
        else:
            print(f"  | {e.text}")

    c._on_entry = on_entry  # noqa: SLF001 - la consola es nuestra

    entradas: queue.Queue[str] = queue.Queue()

    def leer_teclado() -> None:
        for linea in sys.stdin:
            entradas.put(linea.rstrip("\n"))
        entradas.put("salir")

    hilo = threading.Thread(target=leer_teclado, daemon=True)
    hilo.start()

    print(AYUDA)
    print(color("Listo. Escribi un comando.", "bold"))

    try:
        while True:
            # Bombear la serie aunque no se escriba nada: así se ven los
            # eventos que manda el PSoC solo.
            c.poll()
            try:
                linea = entradas.get(timeout=0.1)
            except queue.Empty:
                continue

            texto = linea.strip()
            if not texto:
                continue
            partes = texto.split()
            cmd, resto = partes[0].lower(), partes[1:]

            try:
                if cmd in ("salir", "quit", "exit"):
                    break
                if cmd in ("ayuda", "help", "?"):
                    print(AYUDA)
                elif cmd in GROUPS and cmd != "run":
                    r = sess.run_group(cmd)
                    print_verdict(r)
                elif cmd == "run":
                    r = sess.run_full()
                    print_verdict(r)
                    print_measurements(sess.parser)
                elif cmd == "tap":
                    n = int(resto[0]) if resto else 1
                    for t in sess.tap(repeat=n):
                        print(f"  #{t['intento']:<3} "
                              f"{color(t['verdict'].ljust(4), t['verdict'])} "
                              f"pico {str(t['pico']):>6}  fondo pp {str(t['fondo_pp']):>6}  "
                              f"{t['polaridad'] or ''}")
                elif cmd == "botones":
                    print_verdict(sess.buttons_esp())
                elif cmd == "boton":
                    print_verdict(sess.button_psoc())
                elif cmd == "probe":
                    link = sess.probe()
                    print(color(f"  enlace {'ARRIBA' if link.up else 'CAIDO'}",
                                "PASS" if link.up else "FAIL"),
                          f"pings={link.pings} diag={link.diags} malas={link.frames_bad}")
                elif cmd == "hw":
                    if len(resto) == 2:
                        sess.set_hw(resto[0], int(resto[1]))
                    else:
                        sess.hw()
                    print("  " + "  ".join(f"{k}={v}" for k, v in sorted(sess.parser.hw.items())))
                elif cmd == "diag":
                    sess.diag(bool(resto) and resto[0].lower() in ("on", "1", "si"))
                elif cmd == "reset":
                    c.reset_esp()
                    sess.note_esp_reset()
                    print(color("  ESP reseteado; el SYNC quedo desarmado.", "WARN"))
                elif cmd in ("idac", "dc", "ac", "mon", "sweep", "pga",
                             "pgaout", "taps"):
                    # El modo manual dentro de la consola: se delega al
                    # firmware, que ya contesta lineas '#' parseables, y ademas
                    # se resume lo que llego.
                    from .core.lab import Lab

                    lab = Lab(sess)
                    if cmd == "taps":
                        for ch, pt in sorted(lab.read_all_taps().items()):
                            print(f"  ch{ch}  {fmt_mv(pt.mean_uv):>14}  "
                                  f"pp {pt.pp_uv} uV")
                    elif cmd == "mon" and resto:
                        canal = int(resto[0])
                        periodo = int(resto[1]) if len(resto) > 1 else 200
                        n = int(resto[2]) if len(resto) > 2 else 100
                        lab.monitor(canal, periodo, n,
                                    on_sample=lambda m: print(
                                        f"  {m.t_ms / 1000.0:7.2f} s "
                                        f"{fmt_mv(m.mean_uv):>14}"))
                    elif cmd == "sweep" and len(resto) >= 4:
                        sw = lab.sweep(int(resto[0]), int(resto[1]), int(resto[2]),
                                       int(resto[3]),
                                       int(resto[4]) if len(resto) > 4 else -1)
                        for ch in sorted(sw.points):
                            pend = sw.slope_uv_per_code(ch)
                            if pend is not None:
                                print(f"  ch{ch}: {pend:.1f} uV/codigo  "
                                      f"ganancia {sw.gain_from_reference(ch):.3f}x")
                    else:
                        sess.raw(texto)
                elif cmd == "medidas":
                    print_measurements(sess.parser)
                elif cmd == "figs":
                    destino = resto[0] if resto else (args.figs or "figuras")
                    rutas = figures.save_all(destino, sess.parser.items,
                                             evaluate(sess.parser), sess.parser.meas)
                    for r in rutas:
                        print(f"  figura: {r}")
                    if not rutas:
                        print(color("  todavia no hay nada medido para graficar", "WARN"))
                elif cmd == "save":
                    from .core.session import RunResult

                    destino = resto[0] if resto else (args.json or "corrida.json")
                    r = RunResult("consola", sess.parser, evaluate(sess.parser), 0.0,
                                  sess.parser.run_finished)
                    p = evidence.save(
                        evidence.build(r, port=args.port, started_at=evidence.utc_now(),
                                       hw_profile=dict(sess.parser.hw)),
                        destino,
                    )
                    print(f"  evidencia: {p}")
                elif cmd == "log":
                    destino = resto[0] if resto else (args.transcript or "transcript.txt")
                    p = evidence.save_transcript(c.transcript.text(), destino)
                    print(f"  transcript: {p}")
                else:
                    sess.raw(texto)
            except Exception as exc:  # el banco no se cae por un comando
                print(color(f"  error: {type(exc).__name__}: {exc}", "FAIL"))
    except KeyboardInterrupt:
        pass
    finally:
        c.close()
    return 0


# --------------------------------------------------------------------------
# Argumentos
# --------------------------------------------------------------------------
def cmd_informe(args) -> int:
    """Resumen de una sesión de la ventana, para leer sin haber estado ahí.

    Existe para que otra herramienta —o yo mismo mañana— pueda reconstruir un
    experimento hecho a mano: qué IDAC se movió, a qué código, qué contestó y
    qué se midió alrededor. Una captura de pantalla no lo dice y la ventana lo
    olvida al cerrarse.
    """
    from pathlib import Path

    from .core import bitacora as bit

    if args.listar:
        rutas = bit.ultimas(20)
        if not rutas:
            print(f"No hay bitacoras en {bit.DIRECTORIO}")
            return 1
        print(color(f"Bitacoras en {bit.DIRECTORIO}", "bold"))
        for i, r in enumerate(rutas, 1):
            filas = bit.leer(r)
            dur = max((f.get("t", 0) for f in filas), default=0)
            print(f"  {i:2d}. {r.name}   {len(filas):5d} eventos, {dur:6.0f} s")
        return 0

    if args.archivo:
        ruta = Path(args.archivo)
        if not ruta.exists():
            print(color(f"No existe: {ruta}", "FAIL"))
            return 2
    else:
        rutas = bit.ultimas(args.cual)
        if len(rutas) < args.cual:
            print(color(f"No hay {args.cual} bitacoras en {bit.DIRECTORIO}", "FAIL"))
            return 1
        ruta = rutas[args.cual - 1]

    print(color(f"Bitacora: {ruta}", "bold"))
    print()
    print(bit.resumir(bit.leer(ruta)))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="testbench",
        description="Banco de pruebas de placas del nodo esclavo, por serie.",
    )
    p.add_argument("--no-color", action="store_true", help="salida sin ANSI")

    sub = p.add_subparsers(dest="cmd", required=True)

    def comunes(sp, con_puerto=True):
        if con_puerto:
            sp.add_argument("--port", default=None,
                            help="COM del ESP; si se omite se detecta solo")
            sp.add_argument("--boot-timeout", type=float, default=25.0,
                            help="segundos de espera del arranque del ESP (default 25)")
        sp.add_argument("--json", default=None, help="archivo de evidencia JSON")
        sp.add_argument("--figs", default=None, help="carpeta donde escribir las figuras")
        sp.add_argument("--transcript", default=None, help="archivo del transcript")
        sp.add_argument("--note", default=None, help="nota libre que va a la evidencia")

    sp = sub.add_parser("puertos", help="listar los COM detectados y cual se usaria")
    sp.set_defaults(func=cmd_ports)

    sp = sub.add_parser("probe", help="estado del enlace con el PSoC y perfil de hardware")
    comunes(sp)
    sp.set_defaults(func=cmd_probe)

    sp = sub.add_parser("run", help="corrida automatica; sin --group hace la completa")
    comunes(sp)
    sp.add_argument("--group", choices=sorted(k for k in GROUPS if k != "run"),
                    help="correr un solo grupo")
    sp.add_argument("--no-arm", action="store_true",
                    help="no correr el grupo B antes aunque el grupo capture")
    sp.add_argument("--arm", action="store_true", help=argparse.SUPPRESS)
    sp.set_defaults(func=cmd_run)

    sp = sub.add_parser("tap", help="D7: golpe al geofono, repetido")
    comunes(sp)
    sp.add_argument("--repeat", type=int, default=8, help="intentos seguidos (default 8)")
    sp.add_argument("--no-arm", action="store_true", help="no armar el SYNC antes")
    sp.set_defaults(func=cmd_tap)

    sp = sub.add_parser("hw", help="ver o cambiar el perfil de hardware presente")
    comunes(sp)
    sp.add_argument("parte", nargs="?", choices=["oled", "btn", "geo", "sd", "psoc"])
    sp.add_argument("valor", nargs="?", type=int, choices=[0, 1, 2])
    sp.set_defaults(func=cmd_hw)

    sp = sub.add_parser("consola", help="consola interactiva contra la placa")
    comunes(sp)
    sp.set_defaults(func=cmd_consola)

    # -- modo manual ------------------------------------------------------
    sp = sub.add_parser("taps", help="reposo DC de los cuatro taps")
    comunes(sp)
    sp.add_argument("--settle", type=int, default=3, choices=range(8),
                    help="selector de asentamiento 0-7 (3 = 500 ms, el de D2)")
    sp.set_defaults(func=cmd_taps)

    sp = sub.add_parser("dc", help="una medida DC de un canal del AMux")
    comunes(sp)
    sp.add_argument("canal", type=int, choices=range(5))
    sp.add_argument("--settle", type=int, default=3, choices=range(8))
    sp.set_defaults(func=cmd_dc)

    sp = sub.add_parser("ac", help="media, RMS, pp y 50 Hz de un canal")
    comunes(sp)
    sp.add_argument("canal", type=int, choices=range(5))
    sp.add_argument("--n", type=int, default=0, choices=range(8),
                    help="selector de cantidad de muestras 0-7")
    sp.set_defaults(func=cmd_ac)

    sp = sub.add_parser("idac", help="fijar el IDAC de una etapa")
    comunes(sp)
    sp.add_argument("etapa", type=int, choices=range(4))
    sp.add_argument("codigo", type=int)
    sp.add_argument("--medir", action="store_true",
                    help="medir los cuatro taps despues de escribir")
    sp.set_defaults(func=cmd_idac)

    sp = sub.add_parser("gain", help="fijar PGA o PGAout")
    comunes(sp)
    sp.add_argument("cual", choices=["pga", "pgaout"])
    sp.add_argument("codigo", type=int, choices=range(9))
    sp.set_defaults(func=cmd_gain)

    sp = sub.add_parser("mon", help="osciloscopio lento sobre un tap")
    comunes(sp)
    sp.add_argument("canal", type=int, choices=range(5))
    sp.add_argument("--periodo", type=int, default=200, help="ms entre muestras")
    sp.add_argument("--n", type=int, default=200, help="cantidad de muestras")
    sp.set_defaults(func=cmd_mon)

    sp = sub.add_parser("sweep", help="barrer un IDAC midiendo los taps")
    comunes(sp)
    sp.add_argument("etapa", type=int, choices=range(4))
    sp.add_argument("--lo", type=int, default=-240)
    sp.add_argument("--hi", type=int, default=240)
    sp.add_argument("--paso", type=int, default=16)
    sp.add_argument("--canal", type=int, default=-1,
                    help="canal a medir; -1 mide los cuatro")
    sp.set_defaults(func=cmd_sweep)

    sp = sub.add_parser("replay", help="evaluar una captura guardada, sin hardware")
    sp.add_argument("archivo")
    comunes(sp, con_puerto=False)
    sp.set_defaults(func=cmd_replay)

    sp = sub.add_parser("self-test", help="pruebas del parser, sin hardware")
    sp.set_defaults(func=cmd_selftest)

    sp = sub.add_parser(
        "informe",
        help="resumen de lo que se hizo en la ventana (bitacora de sesion)")
    sp.add_argument("--cual", type=int, default=1,
                    help="1 = la ultima sesion, 2 = la anterior, etc.")
    sp.add_argument("--archivo", help="una bitacora concreta, en vez de la ultima")
    sp.add_argument("--listar", action="store_true",
                    help="listar las bitacoras disponibles y salir")
    sp.set_defaults(func=cmd_informe)

    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    _init_color(args.no_color)
    try:
        return args.func(args)
    except SystemExit:
        raise
    except KeyboardInterrupt:
        print("\ninterrumpido")
        return 130
    except Exception as exc:
        print(color(f"ERROR: {type(exc).__name__}: {exc}", "FAIL"), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
