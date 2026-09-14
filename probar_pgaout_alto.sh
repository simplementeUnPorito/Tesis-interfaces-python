#!/usr/bin/env bash
# Contesta si PGAout puede llegar a x16 o x24, que es lo que se pidio.
#
# El mapa sin calibrar del 2026-09-13 no sirvio: con los IDAC en cero LPo queda
# clavado en todos los puntos, y una cadena con una etapa en el riel deriva por
# eso y no por la ganancia. Hasta x50*x1=50, que se sabe estable, dio "diverge".
#
# La forma correcta es calibrar cada combinacion y despues congelarla. Cada
# corrida deja: si el salto aterriza, y si el punto se queda quieto cinco
# minutos con los cuatro codigos congelados. Ese segundo numero es el que
# decide, y es comparable contra la referencia medida a ganancia 50: 29 mV de
# excursion en diez minutos.
#
# Las tres primeras dan 64 con repartos distintos. Si las tres divergen igual,
# el limite es el PRODUCTO y repartir la ganancia no sirve. Si las de PGAout
# bajo aguantan, el limite es de esa etapa.
set -u
S="$1"
PUNTO="0,164,-95,255"
for combo in "24 2" "16 2" "1 16" "4 16"; do
    set -- $combo
    pga=$1; pgaout=$2
    total=$((pga * pgaout))
    echo "=================== PGA x$pga por PGAout x$pgaout = $total"
    # Esperar a que COM8 este realmente libre. Un proceso zombi de la corrida
    # anterior tomando el puerto hizo que las cuatro combinaciones "corrieran"
    # en segundos, fallando todas en console.open() sin dejar rastro util.
    for _ in $(seq 1 30); do
        py -3 -c "import serial,sys; serial.Serial('COM8',115200,timeout=0).close()"             2>/dev/null && break
        sleep 5
    done
    py -3 saltar_ganancia.py --pga "$pga" --punto "$PUNTO" \
        --ganancias "$pgaout" --congelado 5 --periodo 20 \
        --salida "$S/g${total}_${pga}x${pgaout}.log" 2>&1 | \
        grep -E ">>>|CONGELADO|LPo entre|dentro de|ch[0-9] \(|no arranca"
done
echo "=================== listo"
