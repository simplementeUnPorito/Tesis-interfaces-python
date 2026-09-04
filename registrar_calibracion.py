"""Graba la evolucion de una calibracion, no solo su veredicto.

El firmware ya emite telemetria por iteracion -DAC, medida, error, celda
cuantizada y racha estable, cada CAL_PI_TELEM_PERIOD = 256 muestras- y el ESP la
echa por USB cuando `diag on` esta puesto. Lo que faltaba era alguien que la
capture con marca de tiempo y la deje en un archivo.

Sirve para lo que hoy no se puede ver: si el lazo converge de verdad o se
engancha en una meseta de un transitorio. El HANDOFF documenta el caso de una
corrida que informo 4/4 con 0,27 mV y diez minutos despues estaba en -50 mV; con
esta traza eso se ve mientras pasa, no despues.

TRAMPA IMPORTANTE, ya contemplada: los valores de 32 bits NO vienen en un evento.
`cal_diag_i32()` los manda como CUATRO eventos consecutivos con el mismo codigo,
MSB primero (calibration.c:164). Si se lee cada evento como un valor suelto, un
error de -1000 se lee como cuatro numeros sin sentido. Aca se reensamblan.

Uso:
    python registrar_calibracion.py --port COM8                # dispara el grupo C
    python registrar_calibracion.py --port COM8 --disparo d
    python registrar_calibracion.py --port COM8 --solo-escuchar --segundos 600
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SALIDA = REPO / "lab" / "calibracion"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from testbench.core import console as con                     # noqa: E402
from testbench.core.session import Session                    # noqa: E402

RE_DIAG = re.compile(r"<psoc\s+(\S+)\s+val=(\d+)\s+estado=(\d+)>")

# Eventos cuyo valor real son 4 bytes MSB-first repartidos en 4 eventos iguales.
EVENTOS_32 = {
    "CAL_STAGE_TARGET32", "CAL_STAGE_MEAS32", "CAL_PI_ERROR32",
    "CAL_PI_BUCKET32", "CAL_PI_GAIN32", "CAL_SWEEP_MEAS32",
    "0x41", "0x42", "0x3F",
}
EVENTOS_16 = {"CAL_STAGE_MEAS", "CAL_SWEEP_DAC", "CAL_STAGE_DAC"}

CUENTAS_POR_MV = 262144.0 * 1000.0 / 5_000_000.0
# El firmware compara contra 1 V, asi que el error crudo viene descentrado.
# Es el mismo bug que el HANDOFF documenta del lado del ESP: hasta que se grabe
# el arreglo, los logs hay que leerlos restando esto a mano.
CAL_TARGET_1V_COUNTS = 52429


def a_con_signo(u: int, bits: int) -> int:
    tope = 1 << bits
    return u - tope if u >= tope // 2 else u


class Reensamblador:
    """Junta los eventos multibyte en un solo valor con signo."""

    def __init__(self) -> None:
        self.pendiente: list[tuple[str, int]] = []

    def alimentar(self, nombre: str, val: int) -> tuple[str, int] | None:
        ancho = 4 if nombre in EVENTOS_32 else (2 if nombre in EVENTOS_16 else 1)
        if ancho == 1:
            return (nombre, val)
        if self.pendiente and self.pendiente[0][0] != nombre:
            self.pendiente = []       # secuencia cortada: se descarta, no se inventa
        self.pendiente.append((nombre, val))
        if len(self.pendiente) < ancho:
            return None
        u = 0
        for _, b in self.pendiente:
            u = (u << 8) | (b & 0xFF)
        self.pendiente = []
        return (nombre, a_con_signo(u, ancho * 8))


def registrar(port: str, disparo: str | None, segundos: float) -> dict:
    c = con.Console(port)
    print(f"Abriendo {port} (abrir resetea el ESP)...")
    c.open(wait_ready=True, timeout=25.0)
    sess = Session(c)

    eventos: list[dict] = []
    crudo: list[str] = []
    re_ens = Reensamblador()
    etapa_actual = -1
    t0 = time.monotonic()

    def consumir(linea: str) -> None:
        nonlocal etapa_actual
        crudo.append(f"{time.monotonic()-t0:9.3f}  {linea}")
        m = RE_DIAG.search(linea)
        if not m:
            return
        nombre, val, estado = m.group(1), int(m.group(2)), int(m.group(3))
        listo = re_ens.alimentar(nombre, val)
        if listo is None:
            return
        nombre, valor = listo
        if nombre.endswith("CAL_STAGE_BEGIN") or nombre == "CAL_STAGE_BEGIN":
            etapa_actual = valor
        ev = {"t": round(time.monotonic() - t0, 3), "evento": nombre,
              "valor": valor, "estado": estado, "etapa": etapa_actual}
        # Lo unico que se interpreta: pasar la medida a mV centrada en el
        # objetivo. El resto se guarda tal cual vino.
        if nombre.endswith("MEAS32"):
            ev["mv"] = round((valor - CAL_TARGET_1V_COUNTS) / CUENTAS_POR_MV, 3)
        eventos.append(ev)

    print("Encendiendo el eco de diagnostico del PSoC...")
    sess.console.send("diag on")
    time.sleep(0.8)

    if disparo:
        print(f"Disparando '{disparo}' y escuchando hasta {segundos:.0f} s...")
        sess.console.send(disparo)
    else:
        print(f"Solo escuchando durante {segundos:.0f} s (Ctrl+C corta)...")

    try:
        while time.monotonic() - t0 < segundos:
            for linea in c.read_lines(timeout=1.0) if hasattr(c, "read_lines") else []:
                consumir(linea)
            else:
                # La consola del banco entrega por callback; si no hay API de
                # lectura directa se usa raw() con ventana corta.
                for linea in sess.raw("", idle=0.4, timeout=1.5):
                    consumir(linea)
    except KeyboardInterrupt:
        print("\n  cortado")
    finally:
        sess.console.send("diag off")
        c.close()

    return {"port": port, "disparo": disparo, "segundos": segundos,
            "eventos": eventos, "crudo": crudo}


def resumir(d: dict) -> None:
    ev = d["eventos"]
    print(f"\n  {len(ev)} eventos en {d['segundos']:.0f} s")
    if not ev:
        print("  Ninguno. Si el enlace con el PSoC esta caido, esto es lo esperado:")
        print("  correr antes  -m testbench run --port COM8 --group b")
        return
    por_etapa: dict[int, list[dict]] = {}
    for e in ev:
        por_etapa.setdefault(e["etapa"], []).append(e)
    print(f"\n{'etapa':>6} {'eventos':>8} {'t inicio':>9} {'t fin':>8} "
          f"{'primer mV':>10} {'ultimo mV':>10}")
    for et in sorted(por_etapa):
        xs = por_etapa[et]
        mvs = [x["mv"] for x in xs if "mv" in x]
        print(f"{et:>6} {len(xs):>8} {xs[0]['t']:>8.1f}s {xs[-1]['t']:>7.1f}s "
              f"{(mvs[0] if mvs else float('nan')):>10.3f} "
              f"{(mvs[-1] if mvs else float('nan')):>10.3f}")


def graficar(d: dict, destino: Path) -> None:
    ev = [e for e in d["eventos"] if "mv" in e]
    if not ev:
        return
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    FONDO = "#faf9f7"
    col = {0: "#c1440e", 1: "#1f6f8b", 2: "#3f7d20", 3: "#7d3c98", -1: "#888"}
    fig, ax = plt.subplots(figsize=(11, 4.5), facecolor=FONDO)
    ax.set_facecolor(FONDO)
    for et in sorted({e["etapa"] for e in ev}):
        xs = [e["t"] for e in ev if e["etapa"] == et]
        ys = [e["mv"] for e in ev if e["etapa"] == et]
        ax.plot(xs, ys, ".-", ms=3, lw=1, color=col.get(et, "#888"),
                label=f"etapa {et}")
    ax.axhspan(-20, 20, color="#3f7d20", alpha=0.08)
    ax.axhline(0, lw=0.8, color="#666")
    ax.set_xlabel("tiempo [s]"); ax.set_ylabel("medida centrada [mV]")
    ax.set_title("Evolucion de la calibracion, medida en la placa")
    ax.legend(fontsize=8, ncol=4)
    fig.tight_layout()
    fig.savefig(destino, dpi=140, facecolor=FONDO)
    print(f"  figura -> {destino}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", default="COM8")
    ap.add_argument("--disparo", default="c",
                    help="comando que dispara la calibracion (grupo c por defecto)")
    ap.add_argument("--solo-escuchar", action="store_true",
                    help="no dispara nada, solo captura lo que el PSoC emita")
    ap.add_argument("--segundos", type=float, default=420.0)
    args = ap.parse_args()

    d = registrar(args.port, None if args.solo_escuchar else args.disparo,
                  args.segundos)
    resumir(d)

    SALIDA.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    ruta = SALIDA / f"calibracion_{stamp}.json"
    ruta.write_text(json.dumps(d, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"\n  crudo -> {ruta}")
    graficar(d, SALIDA / f"calibracion_{stamp}.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
