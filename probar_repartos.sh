#!/usr/bin/env bash
# El reparto de la ganancia importa, y el producto no es el limite.
#
# Medido el 2026-09-13 con las tres primeras formas de dar 64:
#   PGA x32 * PGAout x2  -> no llega, y congelada se va al atractor +1.665 mV
#   PGA x16 * PGAout x4  -> no llega
#   PGA x8  * PGAout x8  -> CALIBRA, LPo +52,8 mV, y congelada NO diverge:
#                           se corre 650 mV y se queda, con los cinco taps
#                           en ventana a los cinco minutos.
#
# O sea que el techo no es el producto -los tres valen 64- sino como se
# reparte. El reparto parejo es el que anda, que es lo que Elias venia
# proponiendo desde hacia dias.
#
# Esta tanda, en orden de importancia:
#   1. 8x8 con el PI encima veinte minutos: demostrar que el 64 SE SOSTIENE.
#   2. 2x32, la unica forma de dar 64 que faltaba probar.
#   3. 16x8 y 8x16, los dos repartes de 128, para ver hasta donde llega.
#   4. 4x16, el ultimo reparto de 64.
set -u
S="$1"
PUNTO="0,164,-95,255"
correr() {
    pga=$1; pgaout=$2; extra=${3:-}
    total=$((pga * pgaout))
    echo "=================== PGA x$pga por PGAout x$pgaout = $total"
    for _ in $(seq 1 30); do
        py -3 -c "import serial; serial.Serial('COM8',115200,timeout=0).close()" \
            2>/dev/null && break
        sleep 5
    done
    py -3 saltar_ganancia.py --pga "$pga" --punto "$PUNTO" \
        --ganancias "$pgaout" --congelado 5 --periodo 20 $extra \
        --salida "$S/r${total}_${pga}x${pgaout}.log" 2>&1 | \
        grep -E ">>>|SE SOSTIENE|NO se sostiene|LPo entre|dentro de|ch[0-9] \(|no tiene sentido"
}
correr 8 8 "--sostener 20"
correr 2 32
correr 16 8
correr 8 16
correr 4 16
echo "=================== listo"
