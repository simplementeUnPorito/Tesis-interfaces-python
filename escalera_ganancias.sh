#!/usr/bin/env bash
# Sube la ganancia de PGAout en escalera, cerrando el lazo en cada escalon y
# usando la consigna hallada para predecir la siguiente.
#
# La escalera importa: buscar la consigna desde cero cuesta un rastreo de tres
# voltios, pero conociendo la de la ganancia anterior se predice y alcanza con
# mirar 400 mV alrededor. A x24 la franja mide 22 mV, asi que sin la prediccion
# el rastreo le pasaria por encima.
set -u
LOG_DIR="$1"; shift
GANANCIAS="$@"
SEMILLA=""
GSEMILLA=""
for G in $GANANCIAS; do
  echo "=================== PGAout x$G ==================="
  ARGS="--port COM8 --pgaout $G --minutos 14"
  if [ -n "$SEMILLA" ]; then
    ARGS="$ARGS --consigna-semilla $SEMILLA --ganancia-semilla $GSEMILLA"
  fi
  timeout 3000 py -3 cerrar_lazo.py $ARGS > "$LOG_DIR/escalon_x$G.log" 2>&1
  LINEA=$(grep -E "^SEMILLA " "$LOG_DIR/escalon_x$G.log" | tail -1)
  if [ -z "$LINEA" ]; then
    echo "x$G: no cerro; se corta la escalera"
    tail -4 "$LOG_DIR/escalon_x$G.log"
    break
  fi
  echo "x$G: $LINEA"
  SEMILLA=$(echo "$LINEA" | sed -n 's/.*consigna=\([-0-9.]*\).*/\1/p')
  GSEMILLA=$G
done
echo "=================== fin ==================="
