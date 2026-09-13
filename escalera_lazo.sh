#!/usr/bin/env bash
# Escalera de PGAout con el LAZO CERRADO PI, propagando la consigna.
#
# Es la tecnica que demostro funcionar: fase 1 como adelanto y ReguladorCascada
# -PI en cascada con mid-ranging- encima. Nada nuevo.
#
# La consigna de ch2 que deja LPo legible se BUSCA a la primera ganancia y
# despues se propaga: `cerrar_lazo.py` predice la de la ganancia siguiente con
# --consigna-semilla, lo que convierte un rastreo de cuatro voltios en una
# busqueda estrecha alrededor de la prediccion.
set -u
DESTINO="${1:?falta la carpeta de logs}"
shift
GANANCIAS=("$@")
if [ "${#GANANCIAS[@]}" -eq 0 ]; then GANANCIAS=(4 16 24); fi
mkdir -p "$DESTINO"
RESUMEN="$DESTINO/resumen.txt"
: > "$RESUMEN"

SEMILLA=""
SEMILLA_G=""
for g in "${GANANCIAS[@]}"; do
    log="$DESTINO/lazo_x${g}.log"
    echo "=== PGA x50, PGAout x${g} ===" | tee -a "$RESUMEN"
    taskkill //F //IM python.exe > /dev/null 2>&1
    sleep 4
    if [ -n "$SEMILLA" ]; then
        timeout 2700 py -3 cerrar_lazo.py --port COM8 --pga 50 --pgaout "$g" \
            --minutos 15 --consigna-semilla "$SEMILLA" \
            --ganancia-semilla "$SEMILLA_G" > "$log" 2>&1
    else
        timeout 2700 py -3 cerrar_lazo.py --port COM8 --pga 50 --pgaout "$g" \
            --minutos 15 > "$log" 2>&1
    fi
    codigo=$?
    v=$(grep -E "Consigna encontrada|CONVERG|LPo final|RESULTADO|no llega|IDAC" "$log" | tail -6)
    [ -z "$v" ] && v="sin veredicto (salida $codigo); ultima linea: $(tail -1 "$log")"
    echo "$v" | tee -a "$RESUMEN"
    echo | tee -a "$RESUMEN"
    # La consigna hallada alimenta la ganancia siguiente.
    nueva=$(grep -oE "ch2 = [-+][0-9]+ mV deja LPo" "$log" | head -1 |
            grep -oE "[-+][0-9]+")
    if [ -n "$nueva" ]; then SEMILLA="$nueva"; SEMILLA_G="$g"; fi
done
echo "Resumen en $RESUMEN"
