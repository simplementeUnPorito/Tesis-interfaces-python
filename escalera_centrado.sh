#!/usr/bin/env bash
# Corre el centrado de LPo en toda la escalera de ganancias de PGAout y deja
# un veredicto por ganancia.
#
# La pregunta que contesta es hasta donde llega la cadena, que es empirica: la
# resolucion ya no es el limite desde que IDAC3 tiene 1,7 V de autoridad, asi
# que lo que falle va a fallar por saturacion aguas arriba y hay que verlo.
#
# Uso: ./escalera_centrado.sh <carpeta de logs> [ganancias...]
set -u

DESTINO="${1:?falta la carpeta de logs}"
shift
PERFIL="${PERFIL:-geo-01}"
GANANCIAS=("$@")
if [ "${#GANANCIAS[@]}" -eq 0 ]; then
    GANANCIAS=(1 2 4 8 16 24 32 50)
fi

mkdir -p "$DESTINO"
RESUMEN="$DESTINO/resumen.txt"
: > "$RESUMEN"

for g in "${GANANCIAS[@]}"; do
    log="$DESTINO/centrar_x${g}.log"
    echo "=== PGAout x${g} ===" | tee -a "$RESUMEN"
    taskkill //F //IM python.exe > /dev/null 2>&1
    sleep 3
    # Con el perfil, cada escalon saltea los diez minutos de caracterizacion
    # de la fase 1, que no depende de PGAout y da siempre lo mismo.
    timeout 1800 py -3 centrar_lpo.py --port COM8 --pga 50 --pgaout "$g" \
        --perfil "$PERFIL" --guardar-perfil > "$log" 2>&1
    codigo=$?
    veredicto=$(grep -E "PGAout x[0-9]+:|LPo final|Corrimiento|IDAC0=|fuera de ventana:|se aparto" "$log" | tail -6)
    if [ -z "$veredicto" ]; then
        veredicto="sin veredicto (salida $codigo); ultima linea: $(tail -1 "$log")"
    fi
    echo "$veredicto" | tee -a "$RESUMEN"
    echo | tee -a "$RESUMEN"
done

echo "Resumen en $RESUMEN"
