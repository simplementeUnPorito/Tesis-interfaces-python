#!/usr/bin/env bash
# Con PGAout x24 sostenido a ganancia 96, hasta donde sube el PGA.
#
# El 2026-09-14 quedo probado que PGAout x16 y x24 se sostienen si el PGA es
# x4. Lo que fallaba antes no era la etapa de salida sino la ganancia conjunta
# alta con PGA alto: la planta deriva 467 mV/min a 128 con PGA x16 y agota el
# recorrido del fino en seis minutos.
#
# Asi que la pregunta que queda es cuanto PGA tolera con PGAout ya en x24, y de
# paso si x32 y x48 de PGAout tambien andan con PGA x4.
set -u
S="$1"
PUNTO="0,164,-95,255"
correr() {
    pga=$1; pgaout=$2; min=$3
    total=$((pga * pgaout))
    echo "=================== PGA x$pga por PGAout x$pgaout = $total"
    for _ in $(seq 1 30); do
        py -3 -c "import serial; serial.Serial('COM8',115200,timeout=0).close()" \
            2>/dev/null && break
        sleep 5
    done
    log="$S/t${total}_${pga}x${pgaout}.log"
    py -3 saltar_ganancia.py --pga "$pga" --punto "$PUNTO" \
        --ganancias "$pgaout" --sostener "$min" --periodo 20 \
        --salida "$log" 2>&1 | \
        grep -E ">>>|SE SOSTIENE|NO se sostiene|descontando|pendientes en el punto|no tiene sentido"
    grep -q " SE SOSTIENE" "$log" 2>/dev/null && echo "   ***** $total SOSTENIDO"
}
correr 4 32 20
correr 8 24 20
correr 4 48 20
correr 8 32 20
correr 16 24 20
echo "=================== listo"
