#!/usr/bin/env bash
# Escalera de PGAout con PGA en x1, que es donde la cadena tiene punto de
# trabajo. Contesta hasta que ganancia de salida se puede calibrar.
set -u
DESTINO="${1:?falta la carpeta de logs}"
shift
GANANCIAS=("$@")
if [ "${#GANANCIAS[@]}" -eq 0 ]; then GANANCIAS=(1 2 4 8 16 24); fi
mkdir -p "$DESTINO"
RESUMEN="$DESTINO/resumen.txt"
: > "$RESUMEN"
for g in "${GANANCIAS[@]}"; do
    log="$DESTINO/pga1_x${g}.log"
    echo "=== PGA x1, PGAout x${g} ===" | tee -a "$RESUMEN"
    taskkill //F //IM python.exe > /dev/null 2>&1
    sleep 3
    timeout 1800 py -3 centrar_lpo.py --port COM8 --pga 1 --pgaout "$g" \
        > "$log" 2>&1
    codigo=$?
    v=$(grep -E "PGAout x[0-9]+:|LPo final|Corrimiento|IDAC0=|fuera de ventana:" "$log" | tail -5)
    [ -z "$v" ] && v="sin veredicto (salida $codigo); ultima linea: $(tail -1 "$log")"
    echo "$v" | tee -a "$RESUMEN"
    echo | tee -a "$RESUMEN"
done
echo "Resumen en $RESUMEN"
