"""Calibración experimental para la topología GEO con referencia BP/SUM compartida.

Esta rutina vive primero en la PC para poder observar y guardar cada paso antes
de portar la misma ley al PSoC. El hardware que modela es:

    IDAC0 -> referencia PGAgain
    IDAC1 -> referencia compartida OPAbp + OPAsum
    IDAC2 -> referencia de PGAout
    IDAC3 -> referencia de LP

Los taps son ch0 PGAgain, ch1 BPo, ch2 OPA_SUMo, ch3 SUMo post-PGAout y
ch4 LPo. Todas las lecturas se comparan contra 1 V, que es el cero diferencial
de la cadena actual.

El orden tiene dos fases:

1. Reducir los errores locales: PGAgain, el compromiso BPo/OPA_SUMo, SUMo y LPo.
2. Si LPo no entra, redistribuir error aguas arriba sin violar los presupuestos
   pedidos por Elías (PGAgain/BPo +-500 mV y OPA_SUMo +-1 V).

No usa pendientes históricas: identifica la derivada local en cada ganancia y
la guarda en JSON. Así un cambio de resistencia no requiere adivinar de nuevo.
"""

from __future__ import annotations

import argparse
import json
import math
import time
from datetime import datetime
from pathlib import Path

from testbench.core import console as con
from testbench.core.lab import GAIN_CODES, Lab, TAP_NAMES
from testbench.core.session import Session


TARGET_UV = 1_000_000
SIGNAL_CHANNELS = tuple(range(5))
PGA_CODE_X50 = GAIN_CODES.index(50)
GAIN_TO_CODE = {gain: code for code, gain in enumerate(GAIN_CODES)}

# Presupuestos de offset respecto del cero diferencial pedido. ch3 necesita una
# guarda propia: si SUMo se raila, LP ya no puede recuperar la señal perdida.
ERROR_BUDGET_UV = {
    0: 500_000,
    1: 500_000,
    2: 1_000_000,
    3: 1_000_000,
}

# Primera validación del rango 255 uA: 32 códigos equivalen a la corriente
# máxima anterior (31,875 uA). Se amplía sólo después de comprobar tensiones.
PGAOUT_SAFE_MAX_CODE = 32


def clamp_code(value: float) -> int:
    return max(-255, min(255, int(round(value))))


class Calibrator:
    def __init__(self, lab: Lab, slow_wait: float, fast_wait: float,
                 output: Path, seed: list[int]) -> None:
        self.lab = lab
        self.slow_wait = slow_wait
        self.fast_wait = fast_wait
        self.output = output
        self.codes = list(seed)
        self.events: list[dict] = []
        self.results: list[dict] = []
        self.current_gain = 50

    def log(self, message: str, **data) -> None:
        print(message, flush=True)
        event = {"t": datetime.now().isoformat(timespec="seconds"),
                 "message": message}
        event.update(data)
        self.events.append(event)
        self.save()

    def save(self) -> None:
        self.output.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "topology": "IDAC1 shared OPAbp+OPAsum; IDAC2 PGAout; IDAC3 LP",
            "target_uv": TARGET_UV,
            "budgets_uv": ERROR_BUDGET_UV,
            "pga_x": 50,
            "current_pgaout_x": self.current_gain,
            "current_codes": self.codes,
            "events": self.events,
            "results": self.results,
        }
        self.output.write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                               encoding="utf-8")

    def wait(self, seconds: float, reason: str) -> None:
        if seconds <= 0:
            return
        self.log(f"    asentando {seconds:.1f} s: {reason}")
        end = time.monotonic() + seconds
        while True:
            remaining = end - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(5.0, remaining))

    def set_code(self, stage: int, code: int, reason: str = "", *,
                 force: bool = False) -> None:
        code = clamp_code(code)
        if self.codes[stage] == code and not force:
            return
        accepted = False
        for attempt in range(3):
            accepted = self.lab.set_idac(stage, code)
            if accepted:
                break
            self.log(f"    IDAC{stage}={code:+d} sin ACK, reintento {attempt + 1}/3",
                     kind="command_retry", command="idac", stage=stage,
                     code=code, attempt=attempt + 1)
            time.sleep(0.3)
        if not accepted:
            raise RuntimeError(f"el PSoC rechazó IDAC{stage}={code}")
        old = self.codes[stage]
        self.codes[stage] = code
        self.log(f"    IDAC{stage}: {old:+d} -> {code:+d} {reason}".rstrip(),
                 kind="idac", stage=stage, old=old, code=code, reason=reason)

    def snapshot(self, label: str, channels=SIGNAL_CHANNELS) -> dict[int, int]:
        values: dict[int, int] = {}
        pp: dict[int, int] = {}
        for ch in channels:
            point = None
            # El puente ESP/PSoC puede perder la primera respuesta justo
            # después de conmutar ganancia/IDAC. No confundir una trama
            # perdida con una etapa analógica rota: repetir de forma acotada.
            for attempt in range(3):
                point = self.lab.measure_dc(ch, 3)
                if point is not None and point.ok:
                    break
                self.log(
                    f"    ch{ch} sin respuesta, reintento {attempt + 1}/3",
                    kind="measurement_retry", channel=ch,
                    attempt=attempt + 1,
                )
                time.sleep(0.3)
            if point is None or not point.ok:
                raise RuntimeError(f"ch{ch} {TAP_NAMES[ch]} sin medida válida")
            values[ch] = point.mean_uv
            pp[ch] = point.pp_uv
        desc = "  ".join(
            f"ch{ch} {((values[ch] - TARGET_UV) / 1000):+8.2f} mV"
            for ch in channels
        )
        self.log(f"    {label}: {desc}", kind="snapshot", label=label,
                 values_uv=values, error_uv={ch: values[ch] - TARGET_UV for ch in values},
                 pp_uv=pp, codes=list(self.codes), pgaout_x=self.current_gain)
        return values

    def set_gain(self, gain: int) -> None:
        if gain not in GAIN_TO_CODE:
            raise ValueError(f"ganancia no soportada: {gain}")
        if not any(self.lab.set_gain("pga", PGA_CODE_X50) for _ in range(3)):
            raise RuntimeError("el PSoC no aceptó PGAgain x50")
        if not any(self.lab.set_gain("pgaout", GAIN_TO_CODE[gain]) for _ in range(3)):
            raise RuntimeError(f"el PSoC no aceptó PGAout x{gain}")
        self.current_gain = gain
        self.log(f"PGA=x50; PGAout=x{gain}", kind="gain", pgaout_x=gain)

    def apply_seed(self, seed: list[int]) -> None:
        for stage, code in enumerate(seed):
            # El proceso recién abrió el puerto y no conoce el estado que quedó
            # en el PSoC/EEPROM. Escribir aun cuando coincida con el shadow local.
            self.set_code(stage, code, "semilla segura", force=True)

    @staticmethod
    def _trial_code(base: int, delta: int, min_code: int = -255,
                    max_code: int = 255) -> int:
        if base + delta <= max_code:
            return base + delta
        if base - delta >= min_code:
            return base - delta
        raise RuntimeError("no hay lugar para identificar pendiente")

    def tune_scalar(self, stage: int, channel: int, *, wait_s: float,
                    delta: int, max_step: int, iterations: int,
                    tolerance_uv: int, name: str,
                    min_code: int = -255, max_code: int = 255) -> None:
        """Newton medido para un actuador y un tap."""
        for iteration in range(iterations):
            base_code = self.codes[stage]
            base = self.snapshot(f"{name} base {iteration}", (channel,))[channel]
            error = base - TARGET_UV
            if abs(error) <= tolerance_uv:
                self.log(f"    {name}: dentro de {tolerance_uv / 1000:.1f} mV")
                return

            trial = self._trial_code(base_code, delta, min_code, max_code)
            self.set_code(stage, trial, f"identificar {name}")
            self.wait(wait_s, f"pendiente {name}")
            moved = self.snapshot(f"{name} prueba {iteration}", (channel,))[channel]
            slope = (moved - base) / (trial - base_code)
            self.log(f"    {name}: pendiente {slope:+.1f} uV/código",
                     kind="slope", stage=stage, channel=channel,
                     slope_uv_per_code=slope, gain=self.current_gain)
            if not math.isfinite(slope) or abs(slope) < 10.0:
                # PGAout x1 cae acá: su referencia no tiene autoridad.
                self.set_code(stage, base_code, f"{name} sin autoridad")
                self.wait(wait_s, f"restaurar {name}")
                return

            requested = (TARGET_UV - base) / slope
            requested = max(-max_step, min(max_step, requested))
            target_code = max(min_code, min(max_code,
                                             clamp_code(base_code + requested)))
            self.set_code(stage, target_code, f"Newton {name}")
            self.wait(wait_s, f"aplicar {name}")

    def tune_shared(self, *, iterations: int = 3) -> None:
        """Un actuador, dos salidas: mínimos cuadrados normalizados."""
        channels = (1, 2)
        budgets = {1: 500_000.0, 2: 1_000_000.0}
        for iteration in range(iterations):
            base_code = self.codes[1]
            base = self.snapshot(f"BP/SUM compartida base {iteration}", channels)
            trial = self._trial_code(base_code, 6)
            self.set_code(1, trial, "identificar referencia compartida")
            self.wait(self.slow_wait, "BP y OPA_SUMo")
            moved = self.snapshot(f"BP/SUM compartida prueba {iteration}", channels)
            slopes = {ch: (moved[ch] - base[ch]) / (trial - base_code)
                      for ch in channels}

            numerator = 0.0
            denominator = 0.0
            for ch in channels:
                error = base[ch] - TARGET_UV
                weight = 1.0 / (budgets[ch] ** 2)
                numerator += slopes[ch] * error * weight
                denominator += slopes[ch] * slopes[ch] * weight
            self.log(
                "    referencia compartida: "
                + ", ".join(f"ch{ch} {slopes[ch]:+.1f} uV/código" for ch in channels),
                kind="shared_slope", slopes_uv_per_code=slopes,
                gain=self.current_gain,
            )
            if denominator < 1e-12:
                self.set_code(1, base_code, "sin pendiente compartida útil")
                self.wait(self.slow_wait, "restaurar referencia compartida")
                return

    def tune_upstream_joint(self, *, iterations: int = 2,
                            tolerance_uv: int = 20_000) -> None:
        """Ajusta IDAC0/1 juntos contra PGAgain, BPo y OPA_SUMo.

        OPA_SUMo recibe a la vez el desplazamiento de PGAgain y la referencia
        compartida BP/SUM. Dos PI escalares se persiguen entre sí; por eso se
        identifica una matriz 3x2 y se resuelve un único paso de mínimos
        cuadrados. OPA_SUMo pesa doble porque su residuo se multiplica en
        PGAout.
        """
        channels = (0, 1, 2)
        weights = (1.0, 1.0, 2.0)
        deltas = (3, 8)
        for iteration in range(iterations):
            base_codes = self.codes[:2]
            base = self.snapshot(f"conjunto aguas arriba base {iteration}", channels)
            if max(abs(base[ch] - TARGET_UV) for ch in channels) <= tolerance_uv:
                self.log(f"    conjunto aguas arriba: dentro de {tolerance_uv / 1000:.1f} mV")
                return

            columns: list[list[float]] = []
            for stage, delta in enumerate(deltas):
                trial = self._trial_code(base_codes[stage], delta)
                self.set_code(stage, trial, f"identificar columna IDAC{stage}")
                self.wait(self.slow_wait, f"propagación IDAC{stage}")
                moved = self.snapshot(
                    f"conjunto prueba IDAC{stage} iter {iteration}", channels)
                columns.append([
                    (moved[ch] - base[ch]) / (trial - base_codes[stage])
                    for ch in channels
                ])
                self.set_code(stage, base_codes[stage], f"restaurar IDAC{stage}")
                self.wait(self.slow_wait, f"restaurar base IDAC{stage}")

            # Ecuaciones normales 2x2 de min ||W^(1/2)(e + S du)||.
            a00 = a01 = a11 = b0 = b1 = 0.0
            for row, ch in enumerate(channels):
                w = weights[row]
                s0, s1 = columns[0][row], columns[1][row]
                e = float(base[ch] - TARGET_UV)
                a00 += w * s0 * s0
                a01 += w * s0 * s1
                a11 += w * s1 * s1
                b0 -= w * s0 * e
                b1 -= w * s1 * e
            # Regularización diminuta para matrices casi colineales.
            ridge = max(a00, a11, 1.0) * 1e-6
            a00 += ridge
            a11 += ridge
            det = a00 * a11 - a01 * a01
            if abs(det) < 1e-9:
                self.log("    conjunto aguas arriba: Jacobiano singular")
                return
            du0 = (b0 * a11 - b1 * a01) / det
            du1 = (a00 * b1 - a01 * b0) / det
            du0 = max(-8.0, min(8.0, du0))
            du1 = max(-48.0, min(48.0, du1))
            self.log(
                f"    conjunto aguas arriba: paso IDAC0 {du0:+.2f}, IDAC1 {du1:+.2f}",
                kind="joint_step", jacobian_uv_per_code=columns,
                step=[du0, du1], gain=self.current_gain)
            self.set_code(0, clamp_code(base_codes[0] + du0), "ajuste conjunto")
            self.set_code(1, clamp_code(base_codes[1] + du1), "ajuste conjunto")
            self.wait(self.slow_wait, "aplicar conjunto aguas arriba")
            requested = -numerator / denominator
            requested = max(-48.0, min(48.0, requested))
            target_code = clamp_code(base_code + requested)
            self.set_code(1, target_code, "mínimo conjunto BPo/OPA_SUMo")
            self.wait(self.slow_wait, "mínimo conjunto BPo/OPA_SUMo")
            final = self.snapshot(f"BP/SUM compartida resultado {iteration}", channels)
            norm = max(abs(final[ch] - TARGET_UV) / budgets[ch] for ch in channels)
            if norm <= 0.01 or target_code == base_code:
                return

    def constraints_ok(self, values: dict[int, int]) -> tuple[bool, list[str]]:
        problems = []
        for ch, budget in ERROR_BUDGET_UV.items():
            if ch in values and abs(values[ch] - TARGET_UV) > budget:
                problems.append(
                    f"ch{ch} {TAP_NAMES[ch]} excede {budget / 1000:.0f} mV")
        return not problems, problems

    def calibrate_gain(self, gain: int, seed: list[int], *,
                       observe_only: bool = False) -> dict:
        self.log("=" * 70)
        self.set_gain(gain)

        # Al x1 la referencia de PGAout tiene ganancia ideal cero. Dejarla en
        # cero evita guardar un código sin significado. En las demás ganancias
        # se conserva la semilla manual, que ya sacó la cadena de los rieles.
        local_seed = list(seed)
        # Cero es el arranque no saturado en todas las ganancias medidas. El
        # punto manual +250 sirve en x8, pero en x50 lleva SUMo al riel antes
        # de poder identificar la pendiente.
        local_seed[2] = 0
        local_seed[3] = 0
        self.apply_seed(local_seed)
        self.wait(self.slow_wait, f"estado inicial x{gain}")
        initial = self.snapshot("inicial")

        if observe_only:
            result = {
                "pga_x": 50,
                "pgaout_x": gain,
                "seed": local_seed,
                "initial_uv": initial,
                "codes": list(self.codes),
                "final_uv": initial,
                "error_mv": {
                    ch: (initial[ch] - TARGET_UV) / 1000.0 for ch in initial
                },
                "constraints_ok": self.constraints_ok(initial)[0],
                "problems": self.constraints_ok(initial)[1],
                "lp_abs_error_mv": abs(initial[4] - TARGET_UV) / 1000.0,
                "local_pass": False,
                "observe_only": True,
            }
            self.results.append(result)
            self.log(f"OBSERVACIÓN x{gain}: IDAC {self.codes}",
                     kind="result", result=result)
            return result

        # Primera fase: minimizar aguas arriba sin hacer que dos PI se
        # persigan. Si la semilla ya deja los tres taps a menos de 20 mV no se
        # toca: medir la placa demostró que mover PGAgain por unos pocos mV
        # puede costar decenas de mV en OPA_SUMo tras asentarse.
        self.tune_upstream_joint(iterations=2, tolerance_uv=20_000)

        if gain == 1:
            self.set_code(2, 0, "PGAout x1: referencia sin autoridad")
        else:
            probe_delta = max(6, int(round(256 / gain)))
            self.tune_scalar(2, 3, wait_s=self.fast_wait,
                             delta=probe_delta, max_step=255,
                             iterations=2, tolerance_uv=20_000,
                             name=f"SUMo con PGAout x{gain}",
                             min_code=0, max_code=PGAOUT_SAFE_MAX_CODE)

        # Aunque SUMo responde enseguida, LPo ve ese cambio a través del RC
        # de la planta. No identificar ningún actuador del LP hasta que esa
        # memoria haya desaparecido.
        self.wait(self.slow_wait, "propagación completa de SUMo hacia LP")

        # Segunda fase pedida: sólo si el fino no alcanza, redistribuir con la
        # referencia de PGAout mirando LPo. Esto sacrifica algo de SUMo, pero
        # nunca información: el presupuesto de ch3 se verifica al final.
        before_redistribution = self.snapshot("control previo a redistribución")
        if abs(before_redistribution[4] - TARGET_UV) > 20_000 and gain != 1:
            self.log("    LPo fuera de 20 mV: habilito redistribución con IDAC2")
            self.tune_scalar(2, 4, wait_s=self.slow_wait,
                             # El LP permanece en el riel durante buena parte
                             # del recorrido. Un paso local chico diría
                             # falsamente "sin autoridad"; medir el extremo
                             # identifica la autoridad global y cruza el umbral.
                             delta=PGAOUT_SAFE_MAX_CODE,
                             max_step=PGAOUT_SAFE_MAX_CODE, iterations=1,
                             tolerance_uv=8_000,
                             name=f"LPo grueso desde PGAout x{gain}",
                             min_code=0, max_code=PGAOUT_SAFE_MAX_CODE)

        # IDAC3 se usa al final tanto si hizo falta el grueso como si no. Sus
        # ~350 uV/código medidos son mucho menores que el cálculo ideal, pero
        # dan un ajuste fino útil cuando LP ya salió del riel.
        self.tune_scalar(3, 4, wait_s=self.slow_wait, delta=64,
                         max_step=255, iterations=2,
                         tolerance_uv=3_000, name="LPo refinado")
        final = self.snapshot("final fase local")
        ok, problems = self.constraints_ok(final)
        result = {
            "pga_x": 50,
            "pgaout_x": gain,
            "seed": local_seed,
            "initial_uv": initial,
            "codes": list(self.codes),
            "final_uv": final,
            "error_mv": {ch: (final[ch] - TARGET_UV) / 1000.0 for ch in final},
            "constraints_ok": ok,
            "problems": problems,
            "lp_abs_error_mv": abs(final[4] - TARGET_UV) / 1000.0,
            "local_pass": ok and abs(final[4] - TARGET_UV) <= 20_000,
        }
        self.results.append(result)
        self.log(
            f"RESULTADO x{gain}: IDAC {self.codes}; "
            f"LP {result['error_mv'][4]:+.2f} mV; "
            f"{'CUMPLE' if result['local_pass'] else 'requiere redistribución'}",
            kind="result", result=result,
        )
        return result


def parse_codes(text: str) -> list[int]:
    values = [int(part.strip()) for part in text.split(",")]
    if len(values) != 4 or any(not -255 <= value <= 255 for value in values):
        raise argparse.ArgumentTypeError("se esperan cuatro códigos -255..255")
    return values


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--unsafe-legacy",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--port", default="COM8")
    parser.add_argument("--gains", default="50,24,8,1")
    parser.add_argument("--seed", type=parse_codes, default=[0, -110, 250, -255])
    parser.add_argument("--slow-wait", type=float, default=31.0,
                        help="espera después de mover PGAgain/BP, segundos")
    parser.add_argument("--fast-wait", type=float, default=2.0,
                        help="espera después de mover PGAout/LP, segundos")
    parser.add_argument("--observe-only", action="store_true",
                        help="aplica ganancia/semilla, asienta y sólo mide")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    if not args.unsafe_legacy:
        parser.error(
            "rutina experimental retirada: use autocalibrar_seguro.py; "
            "la opción oculta --unsafe-legacy queda sólo para reproducir "
            "evidencia histórica bajo supervisión"
        )

    gains = [int(part.strip()) for part in args.gains.split(",")]
    unsupported = [gain for gain in gains if gain not in GAIN_TO_CODE]
    if unsupported:
        parser.error(f"ganancias no soportadas: {unsupported}")

    output = args.output or (
        Path(__file__).resolve().parents[3] / "lab" / "calibracion_nueva" /
        f"compartida_{datetime.now():%Y%m%d_%H%M%S}.json"
    )
    console = con.Console(args.port)
    print(f"Abriendo {args.port}...", flush=True)
    console.open(wait_ready=True, timeout=25.0)
    lab = Lab(Session(console))
    calibrator = Calibrator(lab, args.slow_wait, args.fast_wait, output, args.seed)
    try:
        # La configuración del ADC es global y persistente: fijarla antes de
        # interpretar cualquier lectura como tensión.
        if not lab.set_adc_config(1):
            raise RuntimeError("no se pudo seleccionar ADC +-2,5 V")
        for gain in gains:
            calibrator.calibrate_gain(gain, args.seed,
                                      observe_only=args.observe_only)
    except KeyboardInterrupt:
        calibrator.log("interrumpido por el operador")
        return 130
    except Exception as exc:
        calibrator.log(f"ERROR: {type(exc).__name__}: {exc}", kind="error")
        raise
    finally:
        calibrator.save()
        Path(str(output) + ".log").write_text(console.transcript.text(),
                                               encoding="utf-8")
        console.close()

    passed = sum(1 for result in calibrator.results if result["local_pass"])
    print(f"\n{passed}/{len(calibrator.results)} ganancias cumplen fase local")
    print(f"Evidencia: {output}")
    return 0 if passed == len(calibrator.results) else 2


if __name__ == "__main__":
    raise SystemExit(main())
