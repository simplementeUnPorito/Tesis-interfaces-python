#!/usr/bin/env bash
# Calibra a PGAout x1 y ENSEGUIDA sube la ganancia, sin soltar la cadena.
#
# El hueco entre las dos cosas tiene que ser de segundos, no de minutos: los
# mismos codigos dieron ch2 en +7,4 mV a las 16:43 y en -4.973 a las 17:06.
set -u
D="${1:?falta carpeta}"; PGA="${2:-50}"; SUBIR="${3:-2,4}"
mkdir -p "$D"
echo "=== 1) calibrar a PGA x$PGA / PGAout x1 ==="
timeout 1500 py -3 centrar_lpo.py --port COM8 --pga "$PGA" --pgaout 1 \
    --perfil geo-01 --guardar-perfil --tope-minutos 15 --sin-reposo \
    > "$D/cal_x1.log" 2>&1
grep -E "IDAC0=|LPo final|CALIBRA|no llega" "$D/cal_x1.log" | tail -3
P=$(grep -oE "IDAC0=[-+][0-9]+ IDAC1=[-+][0-9]+ IDAC2=[-+][0-9]+ IDAC3=[-+][0-9]+" \
    "$D/cal_x1.log" | tail -1 | grep -oE "[-+][0-9]+" | paste -sd, -)
if [ -z "$P" ]; then echo "sin punto; no se puede subir"; exit 1; fi
echo "=== 2) subir desde el punto $P ==="
timeout 1500 py -3 subir_ganancia.py --port COM8 --pga "$PGA" --desde 1 \
    --hasta "$SUBIR" --punto "$P" > "$D/subir.log" 2>&1
grep -E ">>>|LPo|CALIBRA|no llega" "$D/subir.log" | tail -12
