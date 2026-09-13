"""Centra la cadena por biseccion de signo, etapa por etapa.

POR QUE BISECCION Y NO UN PI

Los lazos PI que se probaron el 12 y el 13 de septiembre fallaron siempre de la
misma forma: estiman una ganancia de planta, integran, y cuando la ganancia real
no es la estimada se van al riel. El 13 a la madrugada el lazo sobre el sumador
llego a +34 mV de su consigna y treinta segundos despues estaba en -5.170.

La biseccion no usa ninguna ganancia. Solo pregunta de que lado del centro esta
el tap, y ese signo sobrevive a la saturacion: una etapa contra el riel sigue
diciendo bien "estas de mas", aunque no diga cuanto. Como no hay integrador, no
hay windup ni escapada: el intervalo solo se achica. Y el numero de sondeos esta
acotado de antemano, que es lo que permite calcular el presupuesto de campo en
vez de estimarlo.

CADA ETAPA SE CENTRA CONTRA SU PROPIO TAP, Y EN ESTE ORDEN

  1. IDAC1 contra ch2 (OPA_SUMo), con un BARRIDO MONOTONO de una sola pasada.
     Es el unico con autoridad de continua sobre ese nodo: el capacitor de
     680 uF del pasabanda le bloquea la suya a IDAC0. Cuesta una constante y
     media por sondeo, y no puede volver atras: la cadena no regresa al mismo
     estado.
  2. Esperar A QUE EL TAP DEJE DE MOVERSE, no un tiempo fijo. La cola de esta
     cadena es mucho mas larga que el tau que da el estimador.
  3. IDAC2 contra ch3 (SUMo), por biseccion de signo. ch3 es lo que ve el
     pasabajos: esta DESPUES de PGAout, mientras ch2 esta antes. Centrar ch2 y
     darlo por bueno deja ch3 contra su riel.
  4. IDAC3 contra ch4 (LPo), por biseccion de signo. Entra despues de PGAout,
     asi que su paso no escala con la ganancia: 6,68 mV por codigo y +-1,70 V,
     medidos con LPo dentro de la ventana, que es la condicion que ninguna
     medicion anterior habia cumplido.

ch2 no deja de importar despues del paso 1: es la guarda de saturacion, y se
verifica al final. El criterio duro de Elias es que NINGUN tap toque un riel, no
solo que el ultimo quede centrado.

DONDE ESTA EL TECHO DE GANANCIA

Lo que el pasabajos amplifica desde el sumador crece con PGAout, asi que el
mismo codigo de IDAC2 mueve a LPo cada vez mas. Cuando medio paso de IDAC2
supere el recorrido de IDAC3, o cuando el ch3 que centra a LPo caiga fuera de la
ventana del sumador, la ganancia deja de ser calibrable. Este script mide las
dos cosas en vez de suponerlas.
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from autocalibracion_dos_fases import TwoPhaseCalibrator
from cerrar_lazo import (CANAL_LPO, CANAL_SUM, SALIDA, TAU_REFERENCIA_S,
                         real_mv)
from escala_banco import BANCO_VREF_MV, FACTOR, clavado, lectura_valida
from perfil_placa import PerfilPlaca, resumen
from testbench.core.console import Console
from testbench.core.lab import GAIN_CODES, Lab
from testbench.core.session import Session

#: SUMo, la salida DESPUES de PGAout. Es de donde cuelga el pasabajos y por lo
#: tanto el nodo que hay que centrar. CANAL_SUM, que vale 2, es OPA_SUMo, el
#: sumador ANTES de PGAout: sirve de guarda de saturacion, no de objetivo.
CANAL_SUMO = 3
#: Limite de IDAC2. El tope real del actuador son +-255.
#:
#: Estuvo en 180 por una medicion del 2026-09-12: pasados unos 180 codigos el
#: sumador deriva 7 mV/min, contra 0,1 en la zona buena. Ese limite resulto ser
#: el que impedia calibrar. Medido el 2026-09-13 con PGA x8 y PGAout x4: con
#: IDAC2 en -180, su tope, ch2 quedaba en +772 mV, y para que ch3 entre en
#: ventana hace falta |ch2| < 520. Faltaban unos sesenta codigos.
#:
#: Se elige el compromiso explicito: 7 mV/min de deriva es un problema conocido
#: y acotado, y no calibrar no es un problema, es no tener instrumento. La
#: verificacion final -un minuto de reposo mirando LPo- mide exactamente esa
#: deriva, asi que si el punto elegido cae en la zona mala se va a ver.
IDAC2_LIMITE = 255
#: Cuanto se deja mover a IDAC1 alrededor del punto de partida. Su paso medido
#: sobre ch2 son 41 mV, asi que 60 codigos son 2,5 V: mas que la ventana entera
#: del sumador. Mas ancho no ayuda y cuesta sondeos de 43 s cada uno.
IDAC1_BRACKET = 60
#: Paso del barrido de IDAC1. Ocho codigos son 330 mV sobre ch2, chicos
#: frente a los 4,8 V de la ventana: el transitorio que deja cada escalon
#: no alcanza a tapar la lectura del siguiente.
IDAC1_PASO_ESCANEO = 10
#: Cuanto se deja saltar de una a un actuador lento. Acota el dano de una
#: pendiente mal medida sin impedir que la iteracion converja.
SALTO_MAXIMO = 80
#: Y cuanto se lo deja mover cuando el punto viene de una corrida que YA cerro.
#: IDAC1 actua antes de PGAout, asi que su optimo no depende de la ganancia de
#: salida: en la escalera se reusa el de la anterior y se verifica con cuatro
#: sondeos en vez de diez.
IDAC1_BRACKET_CONOCIDO = 24
#: Limite de IDAC3.
IDAC3_LIMITE = 255
#: Espera despues de mover IDAC2 o IDAC3. Ninguno de los dos toca el pasabanda:
#: el sumador y el pasabajos asientan en milisegundos. Lo que se espera aca es
#: la deriva lenta de la cadena, no la respuesta del actuador.
ESPERA_RAPIDA_S = 6.0
#: Pasos de biseccion. log2(360) = 8,5, asi que con 9 el intervalo queda en 1.
PASOS_BISECCION = 9
#: Segundos que se mira el punto sin tocar nada antes de declararlo bueno.
REPOSO_S = 60.0
#: Con cuanto alcanza en cada etapa intermedia. No se busca el cero exacto: en
#: esta cadena un punto bueno no sobrevive a los sondeos siguientes, asi que
#: apenas se entra en la banda hay que dejar de tocar y pasar al nivel de abajo,
#: que tiene resolucion de sobra para el resto.
#:   ch2 es la guarda de saturacion, y aca hay que ser GENEROSO. Con sondeos
#:   asentados ch2 resulto tomar solo dos valores: dentro de ventana, o clavado
#:   en -5.163, saltando entre uno y otro con UN SOLO codigo de IDAC1. Eso no
#:   puede ser una ganancia -serian 1.800 desde Vref_BP, y la topologia da 4-:
#:   es un ENGANCHE. El pasabanda se satura, el capacitor de 680 uF se carga
#:   hacia el riel y lo sostiene ahi, y salir de esa condicion lleva minutos.
#:
#:   Con IDAC1 incapaz de posicionar ch2 con resolucion menor que su ventana, lo
#:   unico sensato es aceptar cualquier punto comodamente adentro y dejar que
#:   IDAC2 -que tiene 12,8 mV por codigo y 4,6 V de recorrido- haga el centrado.
TOLERANCIA_CH2_MV = 1500.0
#:   ch3 es lo que ve el pasabajos, asi que se pide mas: 300 mV sobre 2,4 V de
#:   ventana, y lo que quede lo tapa IDAC3 con sus +-1,7 V sobre LPo.
TOLERANCIA_CH3_MV = 300.0


#: Cuantas escrituras de IDAC fallaron en la corrida. Si esto no es cero, nada
#: de lo medido despues significa lo que parece.
ESCRITURAS_FALLIDAS: list[str] = []


def poner_idac(lab: Lab, etapa: int, codigo: int, registro=None) -> bool:
    """Escribe un IDAC y VERIFICA que el firmware lo haya aceptado.

    `Lab.set_idac` devuelve si el eco del firmware confirmo el codigo, y hasta
    ahora ese valor se descartaba en todos los llamados. No es un detalle: el
    propio firmware avisa en su comentario que "el primer comando despues de un
    cambio de configuracion se ignora en silencio", y por eso `set_idac`
    reintenta una vez. Si aun asi falla, el actuador se queda donde estaba y
    todo lo que se mida despues se atribuye a un codigo que nunca se aplico.

    Es una explicacion candidata para los taps que quedan clavados en el mismo
    valor a lo largo de un barrido entero, asi que conviene descartarla con un
    dato en vez de con una suposicion.
    """
    ok = lab.set_idac(etapa, codigo)
    if not ok:
        aviso = f"IDAC{etapa}={codigo:+d} NO fue aceptado por el firmware"
        ESCRITURAS_FALLIDAS.append(aviso)
        if registro is not None:
            registro(f"      {aviso}")
    return ok


def leer_promedio(lab: Lab, canal: int, n: int = 3) -> tuple[float, bool]:
    """Promedia n lecturas. Devuelve tambien si el tap esta VIVO.

    Vivo quiere decir dos cosas a la vez, y hacen falta las dos:

      - la lectura cae dentro de la ventana donde el ADC tiene sentido, y
      - el tap tiene ruido propio.

    Lo segundo es lo que ninguna version anterior miraba, y por eso el
    2026-09-13 el algoritmo decidio que ch2 estaba "dentro de ventana" en
    +1.884 mV cuando ese numero es el riel del sumador: cae adentro de los
    +-2,4 V del ADC y sin embargo la etapa no responde a nada. Una etapa contra
    su riel no tiene ruido: el pico a pico se le va a decenas de microvolts
    mientras los taps sanos andan por el milivolt.

    El valor se devuelve igual aunque el tap este clavado: su SIGNO sigue
    siendo correcto, y la biseccion no usa mas que eso.
    """
    valores, pps, todas_validas = [], [], True
    for _ in range(n):
        punto = lab.measure_dc(canal, settle_sel=2)
        if punto is None or not punto.ok:
            continue
        valores.append(real_mv(punto.mean_uv))
        pps.append(punto.pp_uv)
        todas_validas = todas_validas and lectura_valida(punto.mean_uv / 1000.0)
    if not valores:
        return 0.0, False
    medio = sum(valores) / len(valores)
    banco_mv = sum(v for v in valores) / len(valores) / FACTOR + BANCO_VREF_MV
    pp_medio = sum(pps) / len(pps)
    vivo = todas_validas and not clavado(pp_medio, banco_mv)
    return medio, vivo


def sondar(lab: Lab, etapa: int, codigo: int, canal: int,
           espera: float) -> tuple[float, bool]:
    poner_idac(lab, etapa, codigo)
    time.sleep(espera)
    return leer_promedio(lab, canal)


def esperar_quieto(lab: Lab, canal: int, registro,
                   tolerancia_mv: float = 20.0, periodo: float = 10.0,
                   maximo_s: float = 420.0) -> bool:
    """Mira un tap hasta que deja de moverse. Devuelve si lo logro.

    Una espera fija no sirve porque la constante de tiempo efectiva de esta
    cadena no es la que da el estimador. Medido el 2026-09-13: despues del
    ultimo escalon del barrido de IDAC1, y de esperar 102 s -tres veces el tau
    de 34 s-, ch3 seguia corriendose 30 mV por segundo, y en los noventa
    segundos que duro la biseccion siguiente se fue 2.680 mV, perdiendo un punto
    que ya estaba centrado. Con el acople de 680 uF y su absorcion dielectrica,
    la cola es mucho mas larga que el tau nominal.

    Mirar hasta que se quede quieto no necesita saber cuanto vale esa cola.
    """
    anterior = None
    limite = time.time() + maximo_s
    quietas = 0
    while time.time() < limite:
        valor, _ = leer_promedio(lab, canal, n=2)
        if anterior is not None and abs(valor - anterior) <= tolerancia_mv:
            quietas += 1
            if quietas >= 2:
                registro(f"   ch{canal} quieto en {valor:+.1f} mV")
                return True
        else:
            quietas = 0
        anterior = valor
        time.sleep(periodo)
    registro(f"   ch{canal} sigue moviendose tras {maximo_s:.0f} s; se sigue "
             "igual, pero el resultado va a ser peor")
    return False


def sondar_asentado(lab: Lab, etapa: int, codigo: int, canal: int,
                    registro, espera_minima: float = 10.0,
                    maximo_s: float = 180.0):
    """Pone el codigo y espera a que el tap DEJE DE MOVERSE antes de leer.

    Es obligatorio para el actuador lento, y la razon es cuantitativa. Medido el
    2026-09-13 a las 06:22: el ultimo escalon del barrido, de IDAC1=-6 a +4,
    movio ch2 de +245 a -475 al instante -o sea 72 mV por codigo- y despues
    siguio bajando hasta -5.164, donde se quedo. La respuesta ASENTADA es unas
    setenta veces la instantanea.

    Con esa relacion, un barrido que lee a los 43 s no esta midiendo la
    respuesta al codigo: esta midiendo un transitorio que todavia no empezo. Y
    cualquier busqueda apoyada en esas lecturas aterriza en el lugar equivocado
    en cuanto la cadena termina de moverse.

    Esperar a que se quede quieto no necesita saber cuanto vale la cola ni cuan
    grande es la relacion entre lo asentado y lo instantaneo.
    """
    poner_idac(lab, etapa, codigo, registro)
    time.sleep(espera_minima)
    # El tope de tres minutos por sondeo acota el peor caso de la busqueda a
    # media hora. Si un punto no se aquieta en ese tiempo, se lee igual y se
    # sigue: el aviso queda en el registro y la biseccion tolera un punto malo.
    esperar_quieto(lab, canal, registro, maximo_s=maximo_s)
    return leer_promedio(lab, canal)


def buscar_par_idac1_idac2(lab: Lab, idac1: int, ancho: int, paso: int,
                           tolerancia_mv: float, espera_rapida: float,
                           registro):
    """Busca el PAR (IDAC1, IDAC2) que mete a SUMo en su ventana.

    POR QUE ANIDADO Y NO UNO DESPUES DEL OTRO

    Todas las corridas de la madrugada fijaban IDAC2 en una posicion y buscaban
    con IDAC1, o al reves. Y ninguna podia funcionar, porque lo que determina
    donde queda el sumador es el PAR, no cada uno por separado:

      - Con IDAC2 en -180, IDAC1 dejaba ch2 en +772 mV. Faltaban sesenta codigos
        de IDAC2 para meter ch3 en ventana, y -180 era su tope de entonces.
      - Con el tope liberado a -255, la misma busqueda dejaba ch2 clavado por
        DEBAJO en los dos extremos de IDAC1. El pre-posicionamiento se pasaba.

    No hay un valor de IDAC2 que sirva para pre-posicionar, porque el correcto
    depende de donde quede IDAC1, y viceversa.

    La estructura que corresponde es anidada, y sale gratis porque los dos
    actuadores cuestan cosas muy distintas: IDAC1 pasa por el pasabanda y cada
    sondeo suyo cuesta minutos, mientras que IDAC2 es inmediato y barrerlo
    entero cuesta un minuto y medio. Asi que por cada codigo de IDAC1 -pocos, y
    caros- se recorre IDAC2 completo -muchos, y baratos-.
    """
    for i in range(-(ancho // paso), ancho // paso + 1):
        codigo1 = max(-255, min(255, idac1 + i * paso))
        registro(f"   IDAC1={codigo1:+d}")
        poner_idac(lab, 1, codigo1, registro)
        poner_idac(lab, 2, 0, registro)
        time.sleep(10.0)
        esperar_quieto(lab, CANAL_SUM, registro, maximo_s=150.0)

        for codigo2 in range(-IDAC2_LIMITE, IDAC2_LIMITE + 1, 32):
            valor, vivo = sondar(lab, 2, codigo2, CANAL_SUMO, espera_rapida)
            # NO ALCANZA CON QUE LA LECTURA SEA "VALIDA": TIENE QUE ESTAR CERCA
            # DEL CENTRO.
            #
            # El riel de ch3 esta en +1.855 mV, y eso cae comodo adentro de la
            # ventana de +-2,4 V del ADC, asi que `lectura_valida` lo da por
            # bueno. El 2026-09-13 a las 07:46 la busqueda acepto exactamente
            # ese valor como "DENTRO" y siguio adelante sobre un tap clavado.
            #
            # La tolerancia ya venia como parametro; lo que faltaba era usarla.
            if not vivo or abs(valor) > tolerancia_mv:
                continue
            registro(f"      IDAC2={codigo2:+4d} -> ch3 {valor:+9.1f} mV"
                     "   candidato")
            # SE CONFIRMA CON LA CADENA QUIETA ANTES DE DARLO POR BUENO.
            #
            # El sondeo de seis segundos se toma en pleno transitorio: llegar a
            # IDAC2=-255 desde cero es un salto de 3,3 V sobre ch2. El
            # 2026-09-13 a las 07:31 la busqueda encontro asi ch3 en -377 mV,
            # dentro de ventana, y para cuando IDAC3 fue a trabajar -doce
            # segundos despues- ch3 ya estaba debajo de su riel.
            #
            # Confirmar cuesta un asentamiento por candidato, y los candidatos
            # son pocos: solo se confirma lo que ya parece bueno.
            esperar_quieto(lab, CANAL_SUMO, registro, maximo_s=150.0)
            valor, vivo = leer_promedio(lab, CANAL_SUMO)
            if vivo and abs(valor) <= tolerancia_mv:
                registro(f"      confirmado: ch3 {valor:+9.1f} mV   DENTRO")
                return codigo1, codigo2, valor, True, ""
            registro(f"      no sobrevivio al asentamiento: {valor:+9.1f} mV")
        registro("      IDAC2 recorrido entero sin meter ch3 en ventana")
    return idac1, 0, 0.0, False, "ningun par de IDAC1 e IDAC2 mete SUMo adentro"


def escanear(lab: Lab, etapa: int, canal: int, desde: int, hasta: int,
             paso: int, espera: float, registro):
    """Recorre el rango en pasos chicos y devuelve donde el tap cruza el cero.

    POR QUE UN BARRIDO Y NO UNA BISECCION, para el actuador lento.

    La biseccion salta: su primer movimiento es de medio rango. Sobre un camino
    con una constante de tiempo de treinta segundos y una ganancia de veintidos
    veces aguas abajo, ese salto deja un transitorio que tapa la lectura por
    minutos, y el resultado termina dependiendo de por donde se venia. Medido el
    2026-09-13: IDAC1=-96 leyo +676 mV la primera vez y -5.165 al volver, con el
    mismo codigo y los mismos 43 s de espera.

    Un barrido monotono en pasos chicos no tiene ese problema. Cada escalon es
    pequeno, el transitorio que deja es pequeno, y todos van en la misma
    direccion, asi que el sesgo que quede es parecido en todos los puntos y no
    cambia de signo. Es lo que hizo funcionar al barrido manual que encontro la
    zona despejada.

    Cuesta mas sondeos que una biseccion, pero son sondeos que sirven.
    """
    anterior = None
    sentido = 1 if hasta >= desde else -1
    codigo = desde
    while (codigo - hasta) * sentido <= 0:
        valor, valido = sondar(lab, etapa, codigo, canal, espera)
        registro(f"    IDAC{etapa}={codigo:+4d} -> ch{canal} {valor:+9.1f} mV"
                 + ("" if valido else "   fuera de ventana"))
        if anterior is not None and (anterior[1] > 0.0) != (valor > 0.0):
            # SE QUEDA DONDE ESTA. Nunca vuelve al punto anterior, aunque ese
            # tuviera menor |valor|.
            #
            # Volver no devuelve la cadena a donde estaba. Medido el 2026-09-13
            # a las 05:00: el barrido subio de -96 a +4 dando una recta limpia,
            # con ch2 en +320 al pasar por -16; al retroceder a ese mismo -16,
            # ch2 leyo riel, y siguio en el riel los once puntos siguientes,
            # ocho minutos seguidos. El acople de 680 uF tiene absorcion
            # dielectrica y la vuelta no es simetrica con la ida.
            #
            # No importa perder algo de precision: quien sigue es IDAC2, con
            # 12,8 mV por codigo y +-2,3 V de recorrido sobre ch2, y le sobra
            # para absorber un paso entero de este barrido.
            return codigo, valor, valido, ""
        anterior = (codigo, valor, valido)
        codigo += paso * sentido
    if anterior is None:
        return desde, 0.0, False, "el barrido no dio ninguna lectura"
    lab.set_idac(etapa, anterior[0])
    return anterior[0], anterior[1], anterior[2], "el tap no cruzo el cero"


def apuntar(lab: Lab, etapa: int, canal: int, base: int, limite: int,
            paso_sonda: int, espera: float, registro, intentos: int = 3,
            tolerancia_mv: float = 150.0):
    """Mide la pendiente local, salta al cero y verifica. Repite si hace falta.

    Es para el actuador lento, donde recorrer el rango en pasos chicos costaria
    veinte minutos y saltar a ciegas deja transitorios que tapan la lectura.

    La medicion del 2026-09-13 a las 04:29 mostro que, con la cadena despejada,
    IDAC1 sobre ch2 es notablemente lineal: dieciseis puntos entre -156 y -36
    dieron una recta de 6,9 mV por codigo sin un solo salto. Sobre una planta
    asi, medir la pendiente y saltar es mucho mas barato que barrer, y como cada
    salto se VERIFICA con una lectura nueva, un error de modelo se corrige en la
    iteracion siguiente en vez de arruinar el resultado.

    Lo que no se hace nunca es saltar sin verificar. Eso es lo que rompio a los
    PI: integrar sobre una ganancia mal estimada.
    """
    for intento in range(intentos):
        valor, valido = leer_promedio(lab, canal)
        registro(f"    intento {intento + 1}: IDAC{etapa}={base:+4d} -> "
                 f"ch{canal} {valor:+9.1f} mV"
                 + ("" if valido else "   fuera de ventana"))
        if valido and abs(valor) <= tolerancia_mv:
            return base, valor, True, ""

        # EL ESCALON DE SONDA TIENE QUE SER GRANDE.
        #
        # La cadena deriva unos 0,3 mV por segundo, asi que en los 43 s que
        # tarda un sondeo se corre una decena de milivolts. Con un escalon de
        # ocho codigos la senal son 55 mV y la deriva se come una parte
        # apreciable: el 2026-09-13 esa medicion dio -1,47 mV/codigo cuando la
        # real, sacada de un barrido de dieciseis puntos, son -6,5. El salto
        # calculado con esa pendiente se fue hasta el tope del actuador.
        #
        # Con treinta codigos la senal son 195 mV contra los mismos 13 mV de
        # deriva, y la relacion pasa de cuatro a quince.
        sonda = base + paso_sonda
        if not -limite <= sonda <= limite:
            sonda = base - paso_sonda
        if not -limite <= sonda <= limite:
            return base, valor, valido, "no queda margen para sondear"
        v_sonda, _ = sondar(lab, etapa, sonda, canal, espera)
        pendiente = (v_sonda - valor) / (sonda - base)
        registro(f"      pendiente local {pendiente:+.2f} mV/codigo")
        if abs(pendiente) < 0.5:
            lab.set_idac(etapa, base)
            return base, valor, valido, "el tap no responde al actuador"

        # Y EL SALTO SE ACOTA. Aunque la pendiente salga mal, un paso limitado
        # deja al actuador en algun lado desde donde la iteracion siguiente
        # puede corregir. Sin el limite, una pendiente subestimada manda el
        # codigo al tope, y desde el tope ya no se ve nada.
        destino = int(round(sonda - v_sonda / pendiente))
        destino = max(sonda - SALTO_MAXIMO, min(sonda + SALTO_MAXIMO, destino))
        destino = max(-limite, min(limite, destino))
        if destino == base and destino == sonda:
            return base, valor, valido, ""
        registro(f"      salta a IDAC{etapa}={destino:+d}")
        lab.set_idac(etapa, destino)
        time.sleep(espera)
        base = destino

    valor, valido = leer_promedio(lab, canal)
    registro(f"    quedo en IDAC{etapa}={base:+4d} -> ch{canal} "
             f"{valor:+9.1f} mV" + ("" if valido else "   fuera de ventana"))
    return base, valor, valido, ("" if valido and abs(valor) <= tolerancia_mv
                                 else "no llego a la tolerancia pedida")


def escanear_en_dos_etapas(lab: Lab, etapa: int, canal: int, base: int,
                           ancho: int, espera: float, registro):
    """Barrido grueso para ubicar el cruce y fino para afinarlo.

    Es el metodo para el actuador lento, y se llego a el descartando los otros
    dos con datos:

      - La BISECCION no sirve porque su primer salto es de medio rango, y sobre
        un camino con tau de treinta segundos eso deja un transitorio que tapa
        las lecturas siguientes. El mismo codigo dio +676 mV y -5.165 mV segun
        de donde se viniera.
      - El APUNTADO POR PENDIENTE tampoco, porque la respuesta no es una recta:
        lejos del cruce son 6,5 mV por codigo y cerca se empina a mas de 40. Una
        pendiente medida lejos manda el salto muy lejos, y una medida cerca
        necesita ya estar cerca.

    El barrido monotono no depende de ninguna de las dos cosas. Cada escalon es
    chico, asi que el transitorio que deja tambien, y todos van en la misma
    direccion, asi que el sesgo no cambia de signo. Lo unico que cuesta es
    tiempo, y de eso se ocupa la segunda etapa: el grueso ubica el cruce con
    pocos puntos y el fino lo afina solo en el entorno que quedo.
    """
    grueso = max(4, ancho // 6)
    registro(f"   barrido grueso, paso {grueso}")
    codigo, valor, valido, motivo = escanear(
        lab, etapa, canal, base - ancho, base + ancho, grueso, espera,
        registro)
    if motivo:
        return codigo, valor, valido, motivo

    fino = max(1, grueso // 4)
    if fino >= grueso:
        return codigo, valor, valido, ""
    registro(f"   barrido fino alrededor de {codigo:+d}, paso {fino}")
    return escanear(lab, etapa, canal, codigo - grueso, codigo + grueso,
                    fino, espera, registro)


def bisectar(lab: Lab, etapa: int, canal: int, bajo: int, alto: int,
             espera: float, registro, pasos: int = PASOS_BISECCION,
             sonda=None, tolerancia_mv: float | None = None):
    """Busca el codigo que anula el canal usando solo el signo de la lectura.

    Devuelve (codigo, valor, valido, motivo). Si los dos extremos dan el mismo
    signo el cero no esta encerrado y devuelve el extremo que menos se aleja,
    para que quien llama decida si ampliar el alcance con otro actuador.
    """
    if sonda is None:
        def sonda(etapa_, codigo_, canal_):
            return sondar(lab, etapa_, codigo_, canal_, espera)

    v_bajo, ok_bajo = sonda(etapa, bajo, canal)
    registro(f"    IDAC{etapa}={bajo:+4d} -> ch{canal} {v_bajo:+9.1f} mV"
             + ("" if ok_bajo else "   fuera de ventana"))
    if tolerancia_mv is not None and ok_bajo and abs(v_bajo) <= tolerancia_mv:
        registro(f"    alcanza: {abs(v_bajo):.0f} mV esta dentro de los "
                 f"{tolerancia_mv:.0f} pedidos; no se sondea mas")
        return bajo, v_bajo, ok_bajo, ""
    v_alto, ok_alto = sonda(etapa, alto, canal)
    registro(f"    IDAC{etapa}={alto:+4d} -> ch{canal} {v_alto:+9.1f} mV"
             + ("" if ok_alto else "   fuera de ventana"))
    if tolerancia_mv is not None and ok_alto and abs(v_alto) <= tolerancia_mv:
        registro(f"    alcanza: {abs(v_alto):.0f} mV esta dentro de los "
                 f"{tolerancia_mv:.0f} pedidos; no se sondea mas")
        return alto, v_alto, ok_alto, ""

    if (v_bajo > 0.0) == (v_alto > 0.0):
        # Se queda el extremo que menos se aleja, y se informa SU validez, no
        # un False fijo. Con el False fijo, el 2026-09-13 la corrida aborto
        # diciendo que el sumador estaba fuera de ventana cuando el extremo
        # elegido lo dejaba en +256 mV, comodo adentro. No haber encerrado el
        # cero y estar fuera de ventana son dos cosas distintas, y quien llama
        # necesita distinguirlas para saber si puede seguir.
        gana_bajo = abs(v_bajo) < abs(v_alto)
        mejor = bajo if gana_bajo else alto
        mejor_v = v_bajo if gana_bajo else v_alto
        mejor_ok = ok_bajo if gana_bajo else ok_alto
        lab.set_idac(etapa, mejor)
        return mejor, mejor_v, mejor_ok, "los dos extremos del mismo signo"

    # Con signos opuestos el cero esta adentro y ya no se puede escapar.
    subiendo = v_alto > v_bajo
    if abs(v_bajo) <= abs(v_alto):
        mejor_codigo, mejor_valor, mejor_ok = bajo, v_bajo, ok_bajo
    else:
        mejor_codigo, mejor_valor, mejor_ok = alto, v_alto, ok_alto
    for paso in range(pasos):
        if alto - bajo <= 1:
            break
        medio = (bajo + alto) // 2
        valor, valido = sonda(etapa, medio, canal)
        registro(f"    paso {paso + 1}: IDAC{etapa}={medio:+4d} -> "
                 f"{valor:+9.1f} mV" + ("" if valido else "   fuera"))
        if abs(valor) < abs(mejor_valor):
            mejor_codigo, mejor_valor, mejor_ok = medio, valor, valido
        # PARAR APENAS ALCANZA, NO BUSCAR EL CERO EXACTO.
        #
        # En esta cadena un punto bueno no sobrevive a los sondeos siguientes.
        # El 2026-09-13 la biseccion encontro IDAC2=+45 con ch3 en -183 mV
        # -centrado- y siguio sondeando cinco veces mas; al volver a +45 el
        # punto ya no estaba. Seguir afinando no mejora nada y arruina lo
        # conseguido.
        if tolerancia_mv is not None and valido and abs(valor) <= tolerancia_mv:
            registro(f"    alcanza: {abs(valor):.0f} mV esta dentro de los "
                     f"{tolerancia_mv:.0f} pedidos; no se sondea mas")
            return medio, valor, valido, ""
        if (valor > 0.0) == subiendo:
            alto = medio
        else:
            bajo = medio

    # SE QUEDA CON EL MEJOR PUNTO YA VISTO, SIN VOLVER A SONDEAR.
    #
    # La version anterior remedia los dos extremos del intervalo final para
    # desempatar, y esos dos sondeos de mas son dos oportunidades mas de que la
    # deriva arruine el resultado. El 2026-09-13 paso exactamente eso: IDAC2=+90
    # habia dado ch3 en -826 mV, dentro de ventana, y el desempate volvio a
    # medir ese mismo codigo noventa segundos despues y leyo +1.855.
    #
    # Todos los puntos ya estan medidos. Quedarse con el mejor no cuesta nada.
    lab.set_idac(etapa, mejor_codigo)
    return mejor_codigo, mejor_valor, mejor_ok, ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM8")
    ap.add_argument("--pga", type=int, default=50)
    ap.add_argument("--pgaout", type=int, default=4)
    # 50 mV, no 25. La corrida del 2026-09-13 a las 08:53 dejo LPo en
    # +35 mV con los cinco taps adentro, y en el minuto de reposo la
    # planta se corrio 15 mV sola. Pedir 25 es pedir mas fineza que la
    # que el circuito sostiene: sobre una ventana de +-2 V, 50 mV son el
    # 2,5 %, y lo que sobra de margen no se usa para nada.
    ap.add_argument("--tolerancia", type=float, default=50.0)
    ap.add_argument("--espera", type=float, default=ESPERA_RAPIDA_S)
    ap.add_argument("--csv", default=None)
    ap.add_argument("--perfil", default=None,
                    help="identificador de placa: si hay perfil guardado, se "
                         "saltea la caracterizacion de la fase 1")
    ap.add_argument("--guardar-perfil", action="store_true",
                    help="escribe el punto encontrado en el perfil")
    ap.add_argument("--sin-reposo", action="store_true",
                    help="no espera el minuto de verificacion; solo para "
                         "depurar, un PASS asi no vale")
    args = ap.parse_args()

    destino = Path(args.csv) if args.csv else (
        SALIDA / f"centrar_lpo_x{args.pgaout}.csv")
    destino.parent.mkdir(parents=True, exist_ok=True)

    lineas: list[str] = []

    def registro(texto: str) -> None:
        print(texto, flush=True)
        lineas.append(texto)

    console = Console(args.port)
    console.open(wait_ready=True, timeout=25.0)
    lab = Lab(Session(console))
    codigo2 = codigo3 = 0
    try:
        lab.set_adc_config(1)
        lab.set_gain("pga", GAIN_CODES.index(args.pga))
        lab.set_gain("pgaout", GAIN_CODES.index(args.pgaout))

        perfil = PerfilPlaca.cargar(args.perfil) if args.perfil else None
        punto = perfil.punto_de_entrada(args.pgaout) if perfil else None
        tau = perfil.tau_bp_s if perfil and perfil.tau_bp_s else TAU_REFERENCIA_S
        punto_probado = bool(perfil) and perfil.probado(args.pgaout)

        if punto and perfil.vigente():
            # EL ADELANTO. La caracterizacion de la fase 1, tau y las cuatro
            # pendientes, son constantes de la placa y ya estan medidas. Volver
            # a medirlas en el campo cuesta diez minutos y no cambia nada.
            registro(f"1) Perfil {args.perfil} del {perfil.fecha}: se saltea "
                     "la caracterizacion")
            registro(resumen(perfil))
            idac0, idac1 = punto[0], punto[1]
            for etapa, codigo in zip(range(4), punto):
                lab.set_idac(etapa, codigo)
            # El trim SIEMPRE arranca en cero, venga lo que venga del perfil.
            # Una corrida que no cerro lo deja en su tope, y arrancar la
            # busqueda con 1,7 V de sesgo sobre LPo desplaza el punto que
            # encuentra IDAC2 en el paso 4.
            lab.set_idac(3, 0)
            punto = list(punto[:3]) + [0]
            registro(f"   IDAC {' '.join(f'{c:+d}' for c in punto)} "
                     "desde el perfil")
        else:
            registro(f"1) Adelanto a lazo abierto, PGA x{args.pga} "
                     f"PGAout x{args.pgaout}")
            cal = TwoPhaseCalibrator(lab, SALIDA / "centrar_lpo_fase1.json")
            cal.fase1_margen()
            idac0, idac1 = cal.codes[0], cal.codes[1]
            lab.set_gain("pgaout", GAIN_CODES.index(args.pgaout))
            lab.set_idac(2, 0)
            lab.set_idac(3, 0)
            registro(f"   IDAC0={idac0:+d} IDAC1={idac1:+d}")

        registro(f"   esperando {3.0 * tau:.0f} s el transitorio del pasabanda")
        time.sleep(3.0 * tau)
        for canal in (0, 1, 2, 4):
            valor, valido = leer_promedio(lab, canal)
            registro(f"   ch{canal} = {valor:+9.1f} mV"
                     + ("" if valido else "   fuera de ventana"))

        # --- 2, 3 y 4) una etapa por nivel, cada una contra su propio tap --
        #
        # El barrido del 2026-09-13 a las 03:57 mostro el punto de trabajo y
        # explica por que ninguna busqueda anterior lo encontraba. Con PGA x50,
        # PGAout x4, IDAC0=+11 e IDAC2=-180, barriendo IDAC1:
        #
        #     IDAC1   ch2        ch3
        #      -60    +338      +1856 riel
        #      -52    +272      +1856 riel
        #      -44    +218      +1856 riel
        #      -36    -108      +240  DENTRO
        #      -28    fuera     fuera
        #
        # La zona donde la cadena entera esta despejada mide unos OCHO codigos
        # de IDAC1. Fuera de ella todo esta contra un riel, y ahi ningun
        # actuador se ve: eso es lo que hizo que las bisecciones contra ch3 o
        # contra LPo se fueran para cualquier lado.
        #
        # De ahi sale el orden, y cada nivel se mide contra el tap que todavia
        # tiene rango:
        #
        #   IDAC1 contra ch2. ch2 esta dentro de ventana en un tramo mucho mas
        #     ancho de IDAC1 que ch3, asi que es el unico que da senal util
        #     mientras se busca. Paso medido: 41 mV por codigo.
        #   IDAC2 contra ch3. Con la cadena despejada, IDAC2 mueve ch2 12,8 mV
        #     por codigo -el nominal- y eso son unos 51 mV sobre ch3.
        #   IDAC3 contra LPo. 6,68 mV por codigo, +-1,70 V, y no escala con la
        #     ganancia.
        #
        # Los rangos encajan: el paso de IDAC1 sobre ch2 son 41 mV contra los
        # +-2,3 V de IDAC2, y el paso de IDAC2 sobre LPo son unos 820 mV contra
        # los +-1.700 de IDAC3.
        espera_lenta = 1.5 * tau
        registro("\n2) Se busca el PAR (IDAC1, IDAC2) que mete SUMo adentro")
        codigo1, codigo2, ch3, ch3_ok, motivo = buscar_par_idac1_idac2(
            lab, idac1, IDAC1_BRACKET, IDAC1_PASO_ESCANEO,
            TOLERANCIA_CH3_MV, args.espera, registro)
        if motivo:
            registro(f"   {motivo}. A esta ganancia la cadena no llega.")
            return 1
        idac1 = codigo1
        registro(f"   IDAC1={codigo1:+d} con IDAC2={codigo2:+d} deja ch3 en "
                 f"{ch3:+.1f} mV")

        registro("\n4) IDAC3 hace los ultimos milivolts sobre LPo")
        codigo3, lpo, lpo_ok, motivo3 = bisectar(
            lab, 3, CANAL_LPO, -IDAC3_LIMITE, IDAC3_LIMITE, args.espera,
            registro)
        if motivo3:
            registro(f"   {motivo3}: los +-1,7 V de IDAC3 no alcanzan para "
                     "tapar lo que dejo el paso de IDAC2.")
        registro(f"   IDAC3={codigo3:+d} deja LPo en {lpo:+.1f} mV")

        corrimiento = float("nan")
        if not args.sin_reposo:
            # UN MINUTO SIN TOCAR NADA ANTES DE DECLARAR EXITO. La historia de
            # este proyecto son PASS falsos: 166 cambios declarados buenos de
            # los que no sobrevivio ninguno, siempre por medir mientras la
            # cadena todavia se movia.
            registro(f"\n   se congela todo y se espera {REPOSO_S:.0f} s")
            antes, _ = leer_promedio(lab, CANAL_LPO)
            time.sleep(REPOSO_S)
            despues, _ = leer_promedio(lab, CANAL_LPO)
            corrimiento = despues - antes
            registro(f"   LPo {antes:+.1f} -> {despues:+.1f} mV: se corrio "
                     f"{corrimiento:+.1f} mV")

        registro("\n6) Estado final de los cinco taps")
        final = {}
        for canal in (0, 1, 2, 3, 4):
            valor, ok = leer_promedio(lab, canal, n=5)
            final[canal] = (valor, ok)
            registro(f"   ch{canal} = {valor:+9.1f} mV"
                     + ("" if ok else "   FUERA DE VENTANA"))

        lpo_final, lpo_ok = final[4]
        fuera = [c for c in (0, 1, 2) if not final[c][1]]
        exito = lpo_ok and not fuera and abs(lpo_final) <= args.tolerancia

        registro("\n=== RESULTADO ===")
        registro(f"PGAout x{args.pgaout}: "
                 + ("CALIBRA" if exito else "no llega"))
        if fuera:
            registro("   taps aguas arriba fuera de ventana: "
                     + ", ".join(f"ch{c}" for c in fuera))
        registro(f"LPo final {lpo_final:+.1f} mV contra una tolerancia de "
                 f"{args.tolerancia:.0f}")
        registro(f"Corrimiento en el minuto de reposo: {corrimiento:+.1f} mV")
        if ESCRITURAS_FALLIDAS:
            registro(f"ATENCION: {len(ESCRITURAS_FALLIDAS)} escrituras de IDAC "
                     "no fueron aceptadas por el firmware. Nada de lo medido "
                     "en esta corrida significa lo que parece.")
            for aviso in ESCRITURAS_FALLIDAS[:5]:
                registro(f"   {aviso}")
        else:
            registro("Todas las escrituras de IDAC fueron aceptadas.")
        registro(f"IDAC0={idac0:+d} IDAC1={idac1:+d} IDAC2={codigo2:+d} "
                 f"IDAC3={codigo3:+d}")

        if args.guardar_perfil and args.perfil:
            actualizado = perfil or PerfilPlaca(
                id_placa=args.perfil, fecha=date.today().isoformat())
            actualizado.fecha = date.today().isoformat()
            actualizado.tau_bp_s = tau
            actualizado.puntos[f"x{args.pgaout}"] = [idac0, idac1, codigo2,
                                                     codigo3]
            clave = f"x{args.pgaout}"
            if exito and clave not in actualizado.validados:
                actualizado.validados.append(clave)
            registro(f"Perfil guardado en {actualizado.guardar()}")

        with destino.open("w", encoding="utf-8", newline="") as fh:
            escritor = csv.writer(fh)
            escritor.writerow(["linea"])
            for linea in lineas:
                escritor.writerow([linea])
        print(f"Archivo: {destino}", flush=True)
        return 0 if exito else 1
    finally:
        try:
            lab.set_gain("pgaout", 0)
            for etapa in range(4):
                lab.set_idac(etapa, 0)
        except Exception:
            pass
        console.close()


if __name__ == "__main__":
    raise SystemExit(main())
