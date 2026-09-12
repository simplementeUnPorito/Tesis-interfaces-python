"""Hasta que ganancia se puede centrar LPo con la resolucion disponible.

No mide nada: combina las pendientes ya medidas en placa y contesta, para cada
ganancia, si el par IDAC2 mas IDAC3 alcanza a poner LPo dentro de la ventana
valida, y si no, cuanto falta.

El razonamiento es de cobertura de una grilla. IDAC2 mueve LPo en saltos de
`paso_idac2 * ganancia * ganancia_lp`: eso fija el espaciado de los puntos
alcanzables. IDAC3 mueve LPo en fino y su recorrido total dice cuanto se puede
rellenar alrededor de cada punto. Si el espaciado supera lo que IDAC3 rellena,
quedan huecos y hay ganancias donde ningun par de codigos centra LPo.
"""
from __future__ import annotations

from escala_banco import (BANCO_MAX_VALIDO_MV, BANCO_MIN_VALIDO_MV,
                          BANCO_VREF_MV, FACTOR)

PASO_IDAC1_CH2_MV = 8.231     # ABBA del 2026-09-11
PASO_IDAC2_CH2_MV = 13.0      # con la resistencia de 11,8 k
# Hay dos valores en juego y el techo depende de cual se crea. El nominal sale
# de escalar el 4,3 medido con 10 kohm a los 6,68 de hoy. El medido salio de
# excitar con 400 codigos, pero con LPo contra el riel, donde el amplificador
# esta saturado y la pendiente aparente queda comprimida. El caso pesimista es
# el que hay que reportar hasta repetir la medicion con LPo en rango.
PASO_IDAC3_NOMINAL_MV = 6.44
PASO_IDAC3_MEDIDO_MV = 2.95
PASO_IDAC3_CH4_MV = float(__import__("sys").argv[1]) if len(
    __import__("sys").argv) > 1 else PASO_IDAC3_NOMINAL_MV
GANANCIA_LP = 9.07            # modulo; el signo no cambia la cobertura
CODIGOS = 255

VENTANA_MV = (BANCO_MAX_VALIDO_MV - BANCO_MIN_VALIDO_MV) * FACTOR
AUTORIDAD_IDAC3_MV = 2.0 * CODIGOS * PASO_IDAC3_CH4_MV

print(f"Ventana valida de LPo: {VENTANA_MV:.0f} mV fisicos")
print(f"Recorrido total de IDAC3 sobre LPo: {AUTORIDAD_IDAC3_MV:.0f} mV")
print(f"(centro del banco {BANCO_VREF_MV} mV, factor {FACTOR})\n")

filas = []
for g in (1, 2, 4, 8, 16, 24, 32, 48, 50):
    salto2 = PASO_IDAC2_CH2_MV * g * GANANCIA_LP
    salto12 = PASO_IDAC1_CH2_MV * g * GANANCIA_LP
    # Vernier: con los dos actuadores gruesos el residuo mas chico alcanzable
    # es el menor |n1*p1 - n2*p2| distinto de cero en un rango razonable.
    mejor = min(abs(n1 * PASO_IDAC1_CH2_MV - n2 * PASO_IDAC2_CH2_MV)
                for n1 in range(0, 40) for n2 in range(0, 40)
                if (n1, n2) != (0, 0)) * g * GANANCIA_LP
    filas.append((g, salto2, salto12, mejor))

anchos = "| {:>3} | {:>12} | {:>12} | {:>12} | {:<22} |"
print(anchos.format("G", "salto IDAC2", "salto IDAC1", "vernier 1+2",
                    "veredicto"))
print("|" + "-" * 5 + "|" + "-" * 14 + "|" + "-" * 14 + "|" + "-" * 14
      + "|" + "-" * 24 + "|")
for g, s2, s1, ver in filas:
    if s2 <= AUTORIDAD_IDAC3_MV:
        v = "IDAC2 + IDAC3 alcanza"
    elif s1 <= AUTORIDAD_IDAC3_MV:
        v = "hace falta IDAC1"
    elif ver <= AUTORIDAD_IDAC3_MV:
        v = "hace falta el vernier"
    else:
        v = "NO alcanza"
    print(anchos.format(g, f"{s2:.0f} mV", f"{s1:.0f} mV", f"{ver:.1f} mV", v))

print("\nLectura: el salto de un actuador tiene que entrar dentro del recorrido")
print("de IDAC3, porque IDAC3 es el unico que rellena entre punto y punto.")
