"""Conversión entre las unidades del banco y voltios reales. UN SOLO LUGAR.

DE DÓNDE SALE
Elías midió con tester el 2026-09-05, sobre el mismo tap (LPo, pin P0[1]) y en
dos puntos separados a propósito para que la recta quede bien determinada:

    banco 1000,32 mV  ->  LPo = 2,388 V
    banco 1099,94 mV  ->  LPo = 4,372 V

y además, contra masa:  Vdda = 4,823–4,830 V,  Vref = 2,415 V,
PGAgain (P2[7]) = 2,419 V con el banco en 1000,90.

De los dos puntos sale directamente:

    LPo[mV] = 19,9157 * banco[mV] - 17533,5

o sea que **un milivoltio del banco son 19,92 mV reales**.

TRES VERIFICACIONES INDEPENDIENTES, que es lo que la vuelve confiable:

  * La recta predice Vdda en banco 1122,7. La saturación alta medida por barrido
    -otro experimento, otro día, sin conocer esta recta- dio **1123,01** en ch3
    y **1123,24** en ch2.
  * El punto medio entre 0 V y Vdda cae en banco **1001,55**, que es exactamente
    donde se sienta la cadena en reposo con los cuatro IDAC en cero. O sea que
    la cadena **está centrada en Vref**, como Elías venía diciendo.
  * Para ch0 en banco 1000,90 la recta predice 2,400 V y el tester dio 2,419.
    Difieren 19 mV, o sea **1 mV de banco**.

LO QUE ESTA RECTA CORRIGE, Y ES LO IMPORTANTE
Las lecturas por debajo de **banco 880,4** corresponden a tensiones NEGATIVAS
contra masa, que son imposibles. El "riel bajo" de ~746 que se usó todo el
2026-09-05 equivale a **−2,6 V**: no es un valor, es el ADC fuera de rango
devolviendo basura. La conclusión "está saturado abajo" era correcta; el número
no, y todo lo que se derivó de él —el centro en 936, la excursión de 372 mV, la
supuesta asimetría del objetivo— hay que descartarlo.

LO QUE FALTA ENTENDER, y no bloquea
Por qué el factor es 19,92. No es el 19,073 µV/cuenta del ADC ni el 32 del
DEC_DIV, así que hay una conversión mal en algún punto del camino del banco.
La recta está MEDIDA, así que se puede trabajar con ella; pero conviene
encontrar el mecanismo antes de que muerda en otro lado.
"""
from __future__ import annotations

#: Pendiente de la recta banco -> real, adimensional (mV reales por mV de banco).
FACTOR = 19.9157
#: Ordenada al origen, en mV reales.
OFFSET_MV = -17533.5

#: Referencias medidas con tester, en voltios.
VDDA_V = 4.826
VREF_V = 2.415

#: Por debajo de esto la lectura del banco NO significa nada: el ADC está fuera
#: de rango. Corresponde a 0 V contra masa.
BANCO_MIN_VALIDO_MV = 880.4
#: Y por arriba, Vdda.
BANCO_MAX_VALIDO_MV = 1122.7
#: Vref, que es el objetivo de la calibración en GEO.
BANCO_VREF_MV = 1001.5


def a_voltios(banco_mv: float | None) -> float | None:
    """Lectura del banco -> voltios reales contra masa."""
    if banco_mv is None:
        return None
    return (FACTOR * banco_mv + OFFSET_MV) / 1000.0


def a_banco(voltios: float) -> float:
    """Voltios reales contra masa -> lectura equivalente del banco."""
    return (voltios * 1000.0 - OFFSET_MV) / FACTOR


def delta_a_voltios(delta_banco_mv: float | None) -> float | None:
    """Una DIFERENCIA del banco -> diferencia real. Sin el offset."""
    if delta_banco_mv is None:
        return None
    return FACTOR * delta_banco_mv / 1000.0


def pendiente_a_real(uv_por_codigo_banco: float | None) -> float | None:
    """Pendiente en µV de banco por código -> µV reales por código."""
    if uv_por_codigo_banco is None:
        return None
    return FACTOR * uv_por_codigo_banco


def lectura_valida(banco_mv: float | None) -> bool:
    """False si la lectura cae fuera del rango donde el ADC tiene sentido."""
    if banco_mv is None:
        return False
    return BANCO_MIN_VALIDO_MV <= banco_mv <= BANCO_MAX_VALIDO_MV


def describir(banco_mv: float | None) -> str:
    """Texto corto para informes: valor real y si es válido."""
    if banco_mv is None:
        return "sin lectura"
    v = a_voltios(banco_mv)
    if banco_mv < BANCO_MIN_VALIDO_MV:
        return f"{banco_mv:.1f} banco -> FUERA DE RANGO POR ABAJO (daria {v:.3f} V)"
    if banco_mv > BANCO_MAX_VALIDO_MV:
        return f"{banco_mv:.1f} banco -> FUERA DE RANGO POR ARRIBA (daria {v:.3f} V)"
    return f"{banco_mv:.1f} banco = {v:.3f} V  ({(v - VREF_V) * 1000:+.0f} mV de Vref)"


if __name__ == "__main__":
    print(__doc__)
    print("\nPUNTOS DE REFERENCIA")
    for nombre, b in (("0 V (masa)", BANCO_MIN_VALIDO_MV),
                      ("Vref = objetivo", BANCO_VREF_MV),
                      ("Vdda", BANCO_MAX_VALIDO_MV)):
        print(f"  {nombre:<18} banco {b:8.1f}  ->  {a_voltios(b):6.3f} V")

    print("\nPENDIENTES MEDIDAS, RESCALADAS  (uV por codigo del IDAC)")
    print(f"{'actuador':<26} {'banco':>10} {'real':>12} {'recorrido real':>16}")
    for nombre, uv in (("PGA -> ch0 (a x1)", 61.2),
                       ("BP -> ch1", 90.1),
                       ("ADDER -> ch2", 741.0),
                       ("LP -> ch3", 525.0),
                       ("ADDER -> ch3", 3823.0),
                       ("BP -> ch3", 1002.0),
                       ("PGA -> ch3 (a x1)", 1026.0)):
        r = pendiente_a_real(uv)
        print(f"{nombre:<26} {uv:10.1f} {r:12.0f} {r * 255 / 1e6:14.2f} V")

    print("\nEl LSB del ADC, en las mismas unidades:")
    print(f"  19,1 uV de banco  ->  {pendiente_a_real(19.1):.0f} uV reales")
    print("  o sea que la resolucion efectiva de la medida es ~0,38 mV, no 19 uV:")
    print("  sobre los 4,83 V utiles entran unas 12.700 cuentas, ~13,6 bits, no 18.")
