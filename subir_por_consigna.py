"""Sube PGAout PREPARANDO la entrada antes de cambiar la ganancia.

EL HALLAZGO QUE ORIGINA ESTE SCRIPT (2026-09-13, 17:40)

Un barrido de PGAout con los cuatro IDAC congelados, partiendo del punto
calibrado a PGA x50, midio esto al pasar de x1 a x2:

    ch2 (OPA_SUMo, ANTES de PGAout)   se movio   +6,8 mV
    ch3 (SUMo,    DESPUES de PGAout)  se movio +470,3 mV

O sea que PGAout no perturba al sumador: el salto es todo suyo. Y con la entrada
en +8,2 mV la salida quedo en +471,5. Eso identifica la etapa como

    ch3 = Vref_o + G * (ch2 - Vref_o)

con una referencia propia Vref_o = (2*8,2 - 471,5) / (2-1) = -455 mV, o sea NO
en el cero del ADC. De donde sale ese numero NO se sabe: en la placa real todas
las referencias son Vdda/2 = 2,5 V, que es tambien el cero del ADC, asi que la
prediccion seria cero. Por eso el script lo remide en cada punto en vez de
confiar en la constante.

LA CONSECUENCIA

Centrar OPA_SUMo en cero -que es lo que venian haciendo centrar_lpo.py y
subir_ganancia.py- es exactamente lo que garantiza el fracaso. Con ch2 en cero,
cada duplicacion de ganancia empuja SUMo (G-1)*455 mV hacia arriba:

    x2  ->  +455 mV      todavia en ventana, pero LPo lo amplifica y se clava
    x4  -> +1365 mV      contra el riel
    x8  -> +3185 mV      imposible
    x16 -> +6825 mV      imposible

El punto fijo de una etapa de ganancia es su propia referencia, asi que la
consigna correcta para ch2 no es cero sino

    ch2* = (G-1)/G * Vref_o

que da -228 mV para x2, -341 para x4, -398 para x8 y -427 para x16. A la
autoridad medida de IDAC2 sobre ch2 eso son entre 45 y 90 codigos: sobra rango.

Y POR QUE SE PREPARA ANTES DE CAMBIAR LA GANANCIA

Porque el orden importa. En la corrida de las 17:22 se cambio primero la
ganancia y despues se busco el centro, y en los 45 segundos que LPo estuvo
clavado la cadena entera se fue: ch2 paso de +8 a +1672 mV y ch0 se salio de
ventana, con los cuatro IDAC quietos. Una vez que eso arranca no se vuelve, que
es lo que ya sabiamos del camino: los codigos no definen el punto de trabajo.

Asi que aca se lleva ch2 a su consigna con PGAout todavia en x1 -donde ch3 = ch2
y nada puede clavarse- y recien entonces se cambia la ganancia. Si el modelo
vale, SUMo aterriza cerca de cero de una vez y nunca hay un instante saturado.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from centrar_lpo import (CANAL_LPO, esperar_quieto, leer_promedio, poner_idac)
from regulador_pi import ReguladorCascada
from sostener import informar, tramo
from testbench.core.console import Console
from testbench.core.lab import GAIN_CODES, Lab
from testbench.core.session import Session

CANAL_OPA_SUM = 2
CANAL_SUMO = 3
NOMBRES = {0: "SEo", 1: "BPo", 2: "OPA_SUMo", 3: "SUMo", 4: "LPo"}

#: Referencia propia de PGAout, en la escala del banco. Medida el 2026-09-13
#: con el barrido de ganancia a IDAC congelados. Se vuelve a medir en cada
#: corrida si x2 deja ch3 dentro de ventana; esto es solo el punto de partida.
VREF_PGAOUT_MV = -455.0

#: Pendiente de IDAC2 sobre ch2, en mV por codigo, a PGAout x1. Se mide.
PENDIENTE_IDAC2_INICIAL = 5.1
#: Pendiente de IDAC3 sobre LPo. Medida el 2026-09-13.
PENDIENTE_IDAC3_INICIAL = 6.68


def consigna_ch2(ganancia: int, vref_o: float) -> float:
    """Donde tiene que estar la entrada para que la salida quede en cero."""
    if ganancia <= 1:
        return 0.0
    return (ganancia - 1) / ganancia * vref_o


def medir_pendiente(lab: Lab, etapa: int, base: int, canal: int, delta: int,
                    espera: float, registro) -> float | None:
    """ABBA corto. Devuelve None si algo se salio de ventana."""
    # Si el escalon no entra hacia arriba se lo da hacia abajo. Sin esto, un
    # actuador apoyado en su tope -IDAC3 en +255, que es donde lo deja la
    # calibracion a x1- queda como "no medible" y se termina usando una
    # constante vieja en vez del numero de este punto.
    if not -255 <= base + delta <= 255:
        delta = -delta
    if not -255 <= base + delta <= 255:
        return None
    lecturas: dict[int, list[float]] = {}
    for codigo in (base, base + delta, base + delta, base):
        poner_idac(lab, etapa, codigo)
        time.sleep(espera)
        valor, vivo = leer_promedio(lab, canal, n=2)
        if not vivo:
            poner_idac(lab, etapa, base)
            registro(f"      la pendiente de IDAC{etapa} no se puede medir: "
                     f"ch{canal} salio de ventana")
            return None
        lecturas.setdefault(codigo, []).append(valor)
    poner_idac(lab, etapa, base)
    time.sleep(espera)
    bajo = sum(lecturas[base]) / len(lecturas[base])
    alto = sum(lecturas[base + delta]) / len(lecturas[base + delta])
    return (alto - bajo) / delta


def llevar_a(lab: Lab, etapa: int, canal: int, objetivo: float, codigo: int,
             pendiente: float, tolerancia: float, espera: float,
             registro, intentos: int = 6) -> tuple[int, float, bool]:
    """Mueve un IDAC a pasos proporcionales hasta poner un tap en su consigna.

    Deliberadamente NO es una biseccion. Una biseccion necesita dos extremos con
    signos opuestos, y conseguirlos obliga a visitar los rieles: eso fue lo que
    destruyo el punto en las corridas anteriores, porque cada excursion al riel
    carga el acople y no se vuelve. Con la pendiente medida no hace falta
    ningun extremo, y cada paso es corto.
    """
    for intento in range(1, intentos + 1):
        valor, vivo = leer_promedio(lab, canal, n=3)
        error = objetivo - valor
        estado = "" if vivo else "   FUERA DE VENTANA"
        registro(f"      intento {intento}: ch{canal} = {valor:+.1f} mV, "
                 f"faltan {error:+.1f}{estado}")
        if vivo and abs(error) <= tolerancia:
            return codigo, valor, True
        paso = int(round(error / pendiente))
        # Un paso de cero significa que ya no se puede acercar mas: la
        # resolucion del actuador es el limite, no el algoritmo.
        if paso == 0:
            registro("      un codigo ya es mas grueso que lo que falta; "
                     "es todo lo que este actuador puede")
            return codigo, valor, vivo and abs(error) <= tolerancia * 3
        # Se limita el paso para no cruzar de un saque a la otra saturacion.
        paso = max(-40, min(40, paso))
        nuevo = max(-255, min(255, codigo + paso))
        if nuevo == codigo:
            registro(f"      IDAC{etapa} ya esta en su tope, {codigo:+d}")
            return codigo, valor, False
        codigo = nuevo
        poner_idac(lab, etapa, codigo, None)
        registro(f"      IDAC{etapa} -> {codigo:+d}")
        time.sleep(espera)
    valor, vivo = leer_promedio(lab, canal, n=3)
    return codigo, valor, vivo and abs(valor - objetivo) <= tolerancia


def estado(lab: Lab, registro, sangria: str = "      ") -> list[bool]:
    ok = []
    for canal in (0, 1, 2, 3, 4):
        valor, vivo = leer_promedio(lab, canal, n=3)
        ok.append(vivo)
        registro(f"{sangria}ch{canal} ({NOMBRES[canal]:<8}) {valor:+9.1f} mV"
                 + ("" if vivo else "   FUERA DE VENTANA"))
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM8")
    ap.add_argument("--pga", type=int, default=50)
    ap.add_argument("--punto", required=True, help="idac0,idac1,idac2,idac3")
    ap.add_argument("--ganancias", default="2,4,8")
    ap.add_argument("--vref-pgaout", type=float, default=VREF_PGAOUT_MV)
    ap.add_argument("--tolerancia", type=float, default=200.0)
    ap.add_argument("--tolerancia-ch2", type=float, default=25.0)
    ap.add_argument("--asentar", type=float, default=75.0)
    ap.add_argument("--sostener", type=float, default=0.0,
                    help="minutos que el PI queda corriendo en el ultimo "
                         "punto que calibro, SIN soltar la cadena")
    ap.add_argument("--periodo", type=float, default=30.0,
                    help="periodo del PI mientras sostiene")
    ap.add_argument("--espera", type=float, default=5.0)
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
        registro(f"Referencia supuesta de PGAout: {vref_o:+.0f} mV")
        registro(f"Asentando {args.asentar:.0f} s a PGAout x1...")
        time.sleep(args.asentar)
        esperar_quieto(lab, CANAL_OPA_SUM, registro, maximo_s=120.0)
        registro("\nEstado a x1:")
        estado(lab, registro)

        registro("\nMidiendo la autoridad de IDAC2 sobre ch2 aca:")
        p2 = medir_pendiente(lab, 2, codigos[2], CANAL_OPA_SUM, 20,
                             args.espera, registro)
        if p2 is None or abs(p2) < 0.5:
            p2 = PENDIENTE_IDAC2_INICIAL
            registro(f"      no medible; se usa {p2:+.2f} mV/codigo")
        else:
            registro(f"      IDAC2 -> ch2: {p2:+.2f} mV/codigo")

        for ganancia in ganancias:
            total = args.pga * ganancia
            objetivo = consigna_ch2(ganancia, vref_o)
            registro(f"\n=== PGAout x{ganancia}, ganancia conjunta {total} ===")
            registro(f"   1) con PGAout todavia en x1, llevar ch2 a "
                     f"{objetivo:+.0f} mV")
            lab.set_gain("pgaout", GAIN_CODES.index(1))
            time.sleep(2.0)
            codigos[2], valor2, bien2 = llevar_a(
                lab, 2, CANAL_OPA_SUM, objetivo, codigos[2], p2,
                args.tolerancia_ch2, args.espera, registro)
            if not bien2:
                registro(f"   ch2 quedo en {valor2:+.1f} mV y no en "
                         f"{objetivo:+.0f}: no se cambia la ganancia.")
                break
            registro(f"   ch2 en {valor2:+.1f} mV. Dejando asentar.")
            esperar_quieto(lab, CANAL_OPA_SUM, registro, maximo_s=90.0)

            registro(f"   2) ahora si, PGAout a x{ganancia}")
            lab.set_gain("pgaout", GAIN_CODES.index(ganancia))
            time.sleep(4.0)
            ch2, _ = leer_promedio(lab, CANAL_OPA_SUM, n=3)
            ch3, vivo3 = leer_promedio(lab, CANAL_SUMO, n=3)
            registro(f"      ch2 {ch2:+.1f} -> ch3 {ch3:+.1f} mV"
                     + ("" if vivo3 else "   FUERA DE VENTANA"))
            if vivo3:
                medido = (ganancia * ch2 - ch3) / (ganancia - 1)
                registro(f"      la referencia de PGAout medida aca da "
                         f"{medido:+.0f} mV (se suponia {vref_o:+.0f})")
                vref_o = medido

            # A partir de aca se centra LPo y NO ch3, y la razon es
            # cuantitativa. La etapa LP multiplica SUMo por unos -18, asi que
            # exigirle a ch3 la misma tolerancia que a LPo es aceptar
            # dieciocho veces el error permitido. La corrida de las 18:05
            # murio exactamente ahi: dio por bueno ch3 = -145 mV, que son
            # -2.600 mV en LPo.
            registro("   3) centrar LPo con IDAC2, que es el grueso")
            p2l = medir_pendiente(lab, 2, codigos[2], CANAL_LPO, 1,
                                  args.espera, registro)
            if p2l is None or abs(p2l) < 1.0:
                p2l = p2 * ganancia * -18.0
                registro(f"      no medible; se estima {p2l:+.0f} mV/codigo")
            else:
                registro(f"      IDAC2 -> LPo: {p2l:+.1f} mV/codigo")
            codigos[2], lpo, _ = llevar_a(
                lab, 2, CANAL_LPO, 0.0, codigos[2], p2l, args.tolerancia,
                args.espera, registro)

            registro("   4) afinar LPo con IDAC3, que es el vernier")
            p3 = medir_pendiente(lab, 3, codigos[3], CANAL_LPO, 20,
                                 args.espera, registro)
            if p3 is None or abs(p3) < 0.5:
                p3 = PENDIENTE_IDAC3_INICIAL
                registro(f"      no medible; se usa {p3:+.2f} mV/codigo")
            else:
                registro(f"      IDAC3 -> LPo: {p3:+.2f} mV/codigo")
            codigos[3], lpo, bien4 = llevar_a(
                lab, 3, CANAL_LPO, 0.0, codigos[3], p3, args.tolerancia,
                args.espera, registro)

            registro("   estado final:")
            oks = estado(lab, registro)
            gana = all(oks) and abs(lpo) <= args.tolerancia
            registro(f"   >>> ganancia {total}: "
                     + ("CALIBRA" if gana else "no llega")
                     + f", LPo {lpo:+.1f} mV con "
                     + " ".join(f"{c:+d}" for c in codigos))
            if not gana:
                registro("   no se sigue subiendo: las mayores exigen mas.")
                break
            logrado.append((total, list(codigos), lpo))

        if logrado and args.sostener > 0:
            total, cod, _ = logrado[-1]
            ganancia = total // args.pga
            registro("")
            registro(f"=== SOSTENIENDO LA GANANCIA {total} durante "
                     f"{args.sostener:.0f} MINUTOS CON EL PI ===")
            # No se suelta nada: se sigue en la misma sesion, con la ganancia
            # puesta y los codigos donde quedaron. Volver a aplicarlos desde
            # cero seria pasar otra vez por un transitorio, y ya sabemos que el
            # punto de trabajo no queda definido solo por los cuatro codigos.
            k3 = medir_pendiente(lab, 3, cod[3], CANAL_LPO, 20, args.espera,
                                 registro) or PENDIENTE_IDAC3_INICIAL
            k2 = medir_pendiente(lab, 2, cod[2], CANAL_LPO, 1, args.espera,
                                 registro) or (p2 * ganancia * -18.0)
            registro(f"   IDAC3 -> LPo {k3:+.2f} mV/codigo, "
                     f"IDAC2 -> LPo {k2:+.1f} mV/codigo")
            reg = ReguladorCascada(
                ganancia_fina=k3, ganancia_gruesa=k2, tau_s=34.0,
                periodo_s=args.periodo, referencia_real_mv=0.0,
                tolerancia_real_mv=args.tolerancia,
                codigo_fino=cod[3], codigo_grueso=cod[2],
                limite_fino=255, zona_comoda=200)
            r = tramo(lab, args.sostener, args.periodo, args.tolerancia, reg,
                      codigos, f"CON EL PI a ganancia {total}", registro)
            sostuvo = informar(r, args.tolerancia, registro)
            registro(f"   la ganancia {total} "
                     + ("SE SOSTIENE" if sostuvo else "NO se sostiene")
                     + f" {args.sostener:.0f} minutos con el PI corriendo")
            registro("   estado al terminar:")
            estado(lab, registro)
            dejar_puesto = True

        registro("\n=== RESUMEN ===")
        if not logrado:
            registro("Ninguna ganancia por encima de x1 quedo calibrada.")
        for total, cod, lpo in logrado:
            registro(f"   ganancia {total:>5}: LPo {lpo:+8.1f} mV con IDAC "
                     + " ".join(f"{c:+d}" for c in cod))
        if logrado:
            mejor = logrado[-1]
            registro(f"\nLa mayor que calibra es {mejor[0]}. Para sostenerla:")
            registro("   py -3 sostener.py --pga " + str(args.pga)
                     + " --pgaout " + str(mejor[0] // args.pga)
                     + " --punto " + ",".join(str(c) for c in mejor[1]))
        return 0 if logrado else 1
    finally:
        if dejar_puesto:
            # Se pidio dejarlo activado: la placa queda con la ganancia y los
            # ultimos codigos puestos. Bajarla aca seria deshacer lo que se
            # acaba de conseguir.
            print("", flush=True)
            print("Se deja la placa con la ganancia puesta y los ultimos "
                  "codigos aplicados.", flush=True)
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
