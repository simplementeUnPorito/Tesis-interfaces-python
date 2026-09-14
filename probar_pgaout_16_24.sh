#!/usr/bin/env bash
# PGAout x16 y x24 SOSTENIDOS, que es lo que se pidio desde el principio.
#
# El dato que estaba a la vista y no se uso: 4x16=64 y 2x32=64 CALIBRAN. Los
# que fallaron con PGAout x16 fueron los de ganancia conjunta 128, donde la
# planta deriva 467 mV/min y no hay lazo que la siga. A 64 la deriva es de
# 10 mV/min, asi que el recorrido de IDAC3 -unos 1.700 mV- alcanza para horas
# y el actuador grueso, que es el que mete los transitorios feos al relevar,
# no deberia tener que intervenir nunca.
#
# Orden: primero los de 64 con PGAout alto, que son los que deberian andar, y
# despues x24 que sube la ganancia a 96.
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
    log="$S/po${pgaout}_${pga}x${pgaout}.log"
    py -3 saltar_ganancia.py --pga "$pga" --punto "$PUNTO" \
        --ganancias "$pgaout" --sostener "$min" --periodo 20 \
        --salida "$log" 2>&1 | \
        grep -E ">>>|SE SOSTIENE|NO se sostiene|LPo entre|dentro de|descontando|pendientes en el punto|no tiene sentido"
}
correr 4 16 25
correr 2 32 20
correr 4 24 20
correr 2 24 20
echo "=================== listo"
