#!/usr/bin/env bash
# Hasta que ganancia de PGAout llega la cadena.
#
# PGA VA FIJO EN x50. Es la decision de Elias y esta bien fundada: PGAgain
# alimenta al pasabanda, y mover cualquier cosa que llegue a ese nodo arranca la
# cola del acople de 680 uF. PGAout esta despues del sumador y no la excita.
#
# Con --perfil la fase 1 no mide nada: aplica el punto guardado, usa el tau fijo
# de 44 s y espera el transitorio. Son 2,2 min en vez de 21.
# Con --tope-minutos la busqueda se corta y lo reporta en vez de seguir media
# hora: el presupuesto de campo son 10 minutos.
set -u
DESTINO="${1:?falta carpeta}"; PGA="${2:-50}"; shift 2
GANANCIAS=("$@"); [ ${#GANANCIAS[@]} -eq 0 ] && GANANCIAS=(2 4 8 16)
mkdir -p "$DESTINO"; RES="$DESTINO/resumen.txt"; : > "$RES"
for g in "${GANANCIAS[@]}"; do
    log="$DESTINO/x${g}.log"
    echo "=== PGA x$PGA, PGAout x${g} ===" | tee -a "$RES"
    taskkill //F //IM python.exe > /dev/null 2>&1
    sleep 4
    timeout 900 py -3 centrar_lpo.py --port COM8 --pga "$PGA" --pgaout "$g" --perfil geo-01 --tope-minutos 10 > "$log" 2>&1
    v=$(grep -E "PGAout x[0-9]+:|LPo final|Corrimiento|IDAC0=|fuera de ventana:|presupuesto" "$log" | tail -5)
    [ -z "$v" ] && v="sin veredicto; ultima linea: $(tail -1 "$log")"
    echo "$v" | tee -a "$RES"; echo | tee -a "$RES"
    # CORTA EN LA PRIMERA QUE FALLE. El techo es monotono: la franja de ch2
    # donde LPo es legible se angosta como 1/G -132 mV en x4, 33 en x16- asi
    # que si una ganancia no entra, ninguna mayor va a entrar. Seguir probando
    # las de arriba es gastar diez minutos por cada una para confirmar lo que
    # ya se sabe.
    if ! grep -q "CALIBRA" "$log"; then
        echo "x${g} no llega; las mayores tampoco van a llegar, se corta"             | tee -a "$RES"
        break
    fi
done
echo "Resumen en $RES"
