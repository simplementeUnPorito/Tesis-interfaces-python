#!/usr/bin/env bash
# Sube la ganancia escalon por escalon y en cada uno EXIGE que el PI la
# sostenga. Se detiene en el primero que no aguanta, y ese de antes es el
# maximo que se puede dejar por defecto.
#
# POR QUE ASI Y NO COMO ANTES
#
# Las tandas anteriores median "CALIBRA", que es una foto del instante
# posterior al afinado. Con eso, 8x8, 2x32 y 16x8 daban por buenas ganancias de
# 64 y 128; pero congeladas cinco minutos las tres se corrian a -730, -838 y
# -2.950 mV. Llegar no es sostener, y el unico veredicto que vale es el del
# tramo con el lazo corriendo.
#
# El orden es de menor a mayor y se corta al primer fallo, porque si una
# ganancia no se sostiene las mayores exigen mas.
#
# Los repartos elegidos son los que dieron resultado: PGAout en x8 o mas. Los
# que fallaron -50x2, 32x2, 16x4, 24x4- tienen todos PGAout en x2 o x4, y eso
# todavia no tiene explicacion.
set -u
S="$1"
PUNTO="0,164,-95,255"
MINUTOS=${2:-15}
ultimo_bueno=""
for combo in "16 8" "8 24" "16 16" "16 24" "16 32" "24 32"; do
    set -- $combo
    pga=$1; pgaout=$2
    total=$((pga * pgaout))
    echo "=================== PGA x$pga por PGAout x$pgaout = $total"
    for _ in $(seq 1 30); do
        py -3 -c "import serial; serial.Serial('COM8',115200,timeout=0).close()" \
            2>/dev/null && break
        sleep 5
    done
    log="$S/pi${total}_${pga}x${pgaout}.log"
    py -3 saltar_ganancia.py --pga "$pga" --punto "$PUNTO" \
        --ganancias "$pgaout" --sostener "$MINUTOS" --periodo 20 \
        --salida "$log" 2>&1 | \
        grep -E ">>>|SE SOSTIENE|NO se sostiene|LPo entre|dentro de|correccion|no tiene sentido"
    if grep -q " SE SOSTIENE" "$log" 2>/dev/null; then
        ultimo_bueno="PGA x$pga por PGAout x$pgaout = $total"
        echo "   >>> $total SE SOSTIENE. Se sigue subiendo."
    else
        echo "   >>> $total no se sostiene. Se corta la escalera."
        break
    fi
done
echo "=================== MAXIMO QUE SE SOSTIENE: ${ultimo_bueno:-ninguno}"
