#!/usr/bin/env bash
# Hasta que ganancia de PGAout llega la cadena. Es la pregunta de Elias.
#
# Se corre con PGA en x1 porque es donde los cinco taps se leen; a x50 el punto
# de trabajo se corre mas que la ventana del sumador en decenas de minutos y no
# se puede medir un techo sobre un blanco movil.
set -u
DESTINO="${1:?falta carpeta}"; PGA="${2:-1}"; shift 2
GANANCIAS=("$@"); [ ${#GANANCIAS[@]} -eq 0 ] && GANANCIAS=(1 4 16 24)
mkdir -p "$DESTINO"; RES="$DESTINO/resumen.txt"; : > "$RES"
for g in "${GANANCIAS[@]}"; do
    log="$DESTINO/x${g}.log"
    echo "=== PGA x$PGA, PGAout x${g} ===" | tee -a "$RES"
    taskkill //F //IM python.exe > /dev/null 2>&1
    sleep 4
    timeout 1500 py -3 centrar_lpo.py --port COM8 --pga "$PGA" --pgaout "$g" \
        > "$log" 2>&1
    v=$(grep -E "PGAout x[0-9]+:|LPo final|Corrimiento|IDAC0=|fuera de ventana:" "$log" | tail -5)
    [ -z "$v" ] && v="sin veredicto; ultima linea: $(tail -1 "$log")"
    echo "$v" | tee -a "$RES"; echo | tee -a "$RES"
done
echo "Resumen en $RES"
