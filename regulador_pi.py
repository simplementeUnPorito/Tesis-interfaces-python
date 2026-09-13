"""Regulador PI en cascada para la cadena analogica GEO.

Esto es lo que faltaba. Todo lo construido hasta ahora es calibracion a LAZO
ABIERTO: medir la planta, calcular los codigos, escribirlos y soltar. Eso exige
un modelo correcto, y esta planta no lo tiene. Las pendientes derivadas variaron
un factor ocho entre corridas, el punto de trabajo depende del camino recorrido
por la absorcion dielectrica del acople, y hay una deriva de varios mV por
minuto. A lazo abierto todo eso es fatal. A lazo cerrado es una perturbacion mas.

El lazo abierto no se tira: queda como ADELANTO, para llegar cerca rapido. Lo
que cierra la calibracion es esto.

## Que necesita saber, y que no

Un integrador **solo necesita el signo** de la ganancia. No le importa cuanto
vale, ni por que camino llego la planta, ni por que se movio. Esa es la razon de
fondo por la que sirve aca: es inmune a todo lo que hizo fracasar al lazo
abierto.

## La sintonia sale de la fisica, no del tanteo

La planta es un retardo de primer orden con la tau del pasabanda. Con un
integrador puro, el margen de fase razonable se consigue cuando la ganancia de
lazo cruza uno cerca de 1/tau, y eso equivale a corregir en cada paso una
fraccion ``alpha = T/tau`` de lo que haria falta, con T el periodo de muestreo.

De ahi salen dos propiedades que importan:

- El lazo cerrado converge con constante de tiempo del orden de tau, o sea unos
  cien segundos al 95 %.
- **Tolera un error de ganancia de aproximadamente 1/alpha**. Con T de cinco
  segundos y tau de treinta y cuatro, alpha vale 0,15 y el lazo aguanta que la
  ganancia estimada este errada unas siete veces. Ojo: eso es una cota teorica
  prudente, la mitad de la ingenua. Lo VERIFICADO por prueba contra la planta
  simulada son cuatro veces en los dos sentidos. Las pendientes medidas en placa
  variaron un factor ocho, asi que el margen existe pero no sobra.

## La cascada, que resuelve el problema de rango

Dos actuadores para el mismo nodo: uno con resolucion (IDAC2, unos 12 mV por
codigo) y otro con recorrido (IDAC1, unos 8 mV por codigo pero con todo su rango
libre). El lazo rapido usa el fino para seguir la referencia. Un lazo lento mueve
el grueso para llevar al fino **de vuelta al centro de su rango**.

Es la tecnica clasica de mid-ranging y aca resuelve dos cosas de un saque: el
fino nunca se acerca a los 180 codigos donde se vuelve inestable, y la
combinacion de los dos da una resolucion efectiva muy fina, que es lo que hace
falta para las ganancias altas.

El lazo externo tiene que ser mucho mas lento que el interno o pelean. Diez veces
es el valor habitual y es el que se usa.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Los dos actuadores del nodo. El fino sigue la referencia; el grueso existe
# para que el fino no se quede sin lugar.
ETAPA_FINA = 2
ETAPA_GRUESA = 1

# Fraccion del error que se corrige por paso. Se calcula como T/tau y se acota:
# por debajo el lazo tarda una eternidad, por encima pierde la tolerancia al
# error de ganancia que es toda su gracia.
ALPHA_MINIMA = 0.05
ALPHA_MAXIMA = 0.25

# Cuanto mas lento va el lazo externo que el interno.
LENTITUD_EXTERNO = 10.0

# Pasos seguidos con lectura invalida antes de replegarse al ultimo punto bueno.
# Tres son suficientes para distinguir un transitorio de un rincon del que no se
# sale solo, y son quince segundos con el periodo habitual.
INVALIDAS_PARA_REPLEGAR = 3

# Hasta donde puede llegar el actuador fino sin volverse inestable. Medido el
# 2026-09-12: el codo esta entre 180 y 210 codigos.
LIMITE_FINO = 160
# El externo empieza a tirar cuando el fino pasa de aca. Mas adentro seria pelear
# con el ruido; mas afuera es dejarlo llegar al borde.
ZONA_COMODA_FINA = 60


#: Peso del termino proporcional, en unidades de alpha.
#:
#: En forma de velocidad el proporcional actua sobre e[k]-e[k-1], asi que en el
#: primer paso tras un cambio de error suma kp_relativo*alpha a la correccion y
#: en regimen no aporta nada. Con 1,0 el primer paso vale 1,5 alpha.
#:
#: El techo lo pone la tolerancia al error de ganancia, que es lo que sostiene
#: todo este diseno: el lazo diverge si (Kp + alpha/2) * (real/estimada) pasa de
#: 2. Con alpha 0,147 y Kp = alpha queda 0,22, o sea que todavia aguanta que la
#: ganancia real sea NUEVE VECES la estimada. Subir Kp a 3 alpha bajaria esa
#: tolerancia a cuatro, que es el limite verificado en simulacion.
KP_RELATIVO = 1.0


def alpha_desde_tau(periodo_s: float, tau_s: float) -> float:
    """Fraccion a corregir por paso, derivada del retardo de la planta."""
    if tau_s <= 0.0:
        return ALPHA_MAXIMA
    return max(ALPHA_MINIMA, min(ALPHA_MAXIMA, periodo_s / tau_s))


def tolerancia_de_ganancia(alpha: float) -> float:
    """Cuantas veces puede estar errada la ganancia y el lazo seguir estable.

    Para una ganancia pura la cota es ``2/alpha``: el lazo diverge cuando
    ``alpha * (real/estimada) > 2``. Pero esa cuenta ignora el retardo de la
    planta, que se come parte del margen de fase, asi que informarla seria
    prometer de mas.

    Se devuelve ``1/alpha``, la mitad de la cota ingenua. Con alpha 0,15 son
    unas siete veces. Lo unico verificado por prueba contra la planta simulada
    son cuatro veces en ambos sentidos, asi que incluso este numero es una
    extrapolacion prudente y no un resultado medido.
    """
    return 1.0 / alpha if alpha > 0.0 else 0.0


def paso_vernier(delta_mv: float, ganancia_fina: float,
                 ganancia_gruesa: float,
                 max_codigos: int = 8) -> tuple[int, int]:
    """Combinacion de los dos actuadores cuyo efecto neto se acerca a delta_mv.

    A ganancias altas la cuantizacion de un solo actuador deja de entrar en la
    ventana: en x24 un codigo del fino mueve LPo 2.830 mV sobre una ventana de
    4.826, asi que con el fino solo no hay forma de centrar nada.

    Pero los dos pasos son incomensurables, 12,35 y 8,3 mV, y eso permite
    resoluciones efectivas de decimas de milivolt combinandolos en oposicion.
    Tres codigos de uno contra dos del otro dan 0,2 mV netos. Es un vernier, la
    misma idea que la del calibre.

    El precio: mover el grueso arrastra la cola del pasabanda, asi que un paso de
    vernier es lento. Por eso se usa SOLO cuando el paso del fino no alcanza la
    precision pedida, que es lo que pasa a ganancias altas.
    """
    mejor, mejor_error = (0, 0), abs(delta_mv)
    for n_fino in range(-max_codigos, max_codigos + 1):
        for n_grueso in range(-max_codigos, max_codigos + 1):
            if n_fino == 0 and n_grueso == 0:
                continue
            efecto = n_fino * ganancia_fina + n_grueso * ganancia_gruesa
            error = abs(efecto - delta_mv)
            costo = abs(n_fino) + abs(n_grueso)
            costo_mejor = abs(mejor[0]) + abs(mejor[1])
            if error < mejor_error - 1e-9 or (
                    abs(error - mejor_error) < 1e-9 and costo < costo_mejor):
                mejor, mejor_error = (n_fino, n_grueso), error
    return mejor


def paso_neutro(ganancia_fina: float, ganancia_gruesa: float,
                max_codigos: int = 8) -> tuple[int, int]:
    """Movimiento que descarga el actuador fino SIN mover el tap.

    En mid-ranging clasico el lazo externo mueve el grueso y el interno
    compensa. Aca no se puede: a x24 un codigo del grueso son 1.807 mV y uno del
    fino 2.688, asi que cualquiera de los dos solo es una patada enorme y el otro
    no tiene con que corregirla. El lazo se escapaba justo ahi.

    Lo que si se puede es moverlos JUNTOS de modo que sus efectos se cancelen.
    Con pasos de 2.688 y 1.807 la relacion es casi dos tercios, asi que dos
    codigos de uno contra tres del otro dejan un residuo de 45 mV sobre un
    movimiento de miles. El fino se acerca al centro y el tap casi no se entera.

    Es un desplazamiento en el nucleo de la transformacion. Devuelve el par con
    el fino POSITIVO; quien llama le pone el signo que necesite.
    """
    mejor, mejor_residuo = (0, 0), float("inf")
    for n_fino in range(1, max_codigos + 1):
        for n_grueso in range(-max_codigos, max_codigos + 1):
            if n_grueso == 0:
                continue
            residuo = abs(n_fino * ganancia_fina + n_grueso * ganancia_gruesa)
            # Se prefiere el residuo mas chico y, a igualdad, el movimiento mas
            # corto: cada codigo del grueso arrastra la cola del pasabanda.
            if residuo < mejor_residuo - 1e-9:
                mejor, mejor_residuo = (n_fino, n_grueso), residuo
    return mejor


@dataclass
class SalidaPI:
    """Lo que el regulador decide en un paso. Sin efectos: quien llama escribe."""

    codigo_fino: int
    codigo_grueso: int
    error_real_mv: float
    paso_fino: int = 0
    paso_grueso: int = 0
    saturado: bool = False
    motivo: str = ""


@dataclass
class ReguladorCascada:
    """PI en cascada con mid-ranging. Logica pura, sin hardware.

    ``ganancia_fina`` y ``ganancia_gruesa`` son mV del tap por codigo. Solo
    necesitan tener el SIGNO bien y el orden de magnitud aproximado; el lazo
    tolera que el modulo este errado varias veces.
    """

    ganancia_fina: float
    ganancia_gruesa: float
    tau_s: float
    periodo_s: float = 5.0
    referencia_real_mv: float = 0.0
    # Precision pedida sobre el tap. Si un codigo del fino es MAS grueso que
    # esto, el lazo pasa a mover los dos actuadores en vernier.
    tolerancia_real_mv: float = 25.0
    limite_fino: int = LIMITE_FINO
    zona_comoda: int = ZONA_COMODA_FINA
    limite_grueso: int = 255
    # El integrador vive en el codigo del actuador, que es la accion acumulada.
    codigo_fino: int = 0
    codigo_grueso: int = 0
    historial: list = field(default_factory=list)
    pasos_vernier: int = 0
    # Ultimo par de codigos con el que la lectura fue valida, y cuantos pasos
    # seguidos lleva sin serlo.
    ultimo_bueno: tuple[int, int] | None = None
    invalidas_seguidas: int = 0
    repliegues: int = 0
    # Error de la muestra anterior. Lo necesitan las dos cosas que se agregaron
    # el 2026-09-13: el trapecio de Tustin, que integra el promedio de e[k] y
    # e[k-1] en vez de solo el actual, y el termino proporcional, que en forma
    # de velocidad actua sobre la DIFERENCIA entre los dos.
    error_previo_mv: float | None = None
    # Peso del proporcional, en unidades de alpha. Con 1,0 la respuesta del
    # primer paso pasa de alpha a 1,5 alpha: 50 % mas rapida.
    kp_relativo: float = KP_RELATIVO

    def __post_init__(self) -> None:
        if not self.ganancia_fina:
            raise ValueError("la ganancia fina no puede ser nula")
        self.alpha = alpha_desde_tau(self.periodo_s, self.tau_s)
        self.alpha_externo = self.alpha / LENTITUD_EXTERNO

    def paso(self, medido_real_mv: float,
             lectura_valida: bool = True) -> SalidaPI:
        """Un paso del lazo. Devuelve los codigos nuevos; no escribe nada.

        ``lectura_valida`` es obligatorio de respetar. Un tap fuera de rango
        devuelve un valor comprimido contra el borde, o sea un error constante y
        falso, y un integrador alimentado con eso se escapa: en la simulacion se
        iba al riel opuesto y no volvia. Aca NO se integra y se avisa.

        Eso define la frontera entre las dos mitades del sistema. El lazo
        cerrado exige arrancar dentro de rango; ponerlo ahi es el trabajo del
        adelanto a lazo abierto. Cada uno hace lo suyo.
        """
        if not lectura_valida:
            # NO INTEGRAR ALCANZA PARA NO ROMPER, PERO NO PARA VOLVER.
            #
            # El 2026-09-12 el tap salio de rango durante un afinado y el lazo
            # se quedo congelado cincuenta minutos: hacia bien en no integrar
            # sobre una lectura sin sentido, pero no tenia ninguna forma de
            # regresar. Un regulador que se puede pintar solo en un rincon no
            # sirve desatendido.
            #
            # Por eso recuerda el ultimo par de codigos con el que la lectura
            # valia, y despues de unos pocos pasos ciegos se repliega ahi.
            self.invalidas_seguidas += 1
            # Y se olvida el error previo. Cuando la lectura vuelva, la muestra
            # anterior valida puede ser de hace minutos: promediarla con la
            # nueva en el trapecio, o restarla en el proporcional, seria tratar
            # como vecinas dos muestras que no lo son.
            self.error_previo_mv = None
            motivo = "lectura fuera de rango; no se integra"
            if (self.invalidas_seguidas >= INVALIDAS_PARA_REPLEGAR
                    and self.ultimo_bueno is not None):
                self.codigo_fino, self.codigo_grueso = self.ultimo_bueno
                self.invalidas_seguidas = 0
                self.repliegues += 1
                motivo = (f"fuera de rango {INVALIDAS_PARA_REPLEGAR} veces; se "
                          f"repliega a ({self.codigo_fino:+d}, "
                          f"{self.codigo_grueso:+d})")
            salida = SalidaPI(codigo_fino=self.codigo_fino,
                              codigo_grueso=self.codigo_grueso,
                              error_real_mv=float("nan"),
                              motivo=motivo)
            self.historial.append(salida)
            return salida

        self.invalidas_seguidas = 0
        self.ultimo_bueno = (self.codigo_fino, self.codigo_grueso)

        error = self.referencia_real_mv - medido_real_mv

        # LAZO INTERNO: el fino sigue la referencia.
        #
        # Se corrige una fraccion del camino, no el camino entero. Eso es lo que
        # da la inmunidad al error de ganancia: si la ganancia real es el doble
        # de la estimada, el paso resulta el doble de grande pero sigue siendo
        # menos que el error, asi que converge igual.
        # TUSTIN MAS PROPORCIONAL, EN FORMA DE VELOCIDAD.
        #
        # Antes era un rectangulo: la correccion valia alpha*e[k], o sea un
        # integrador puro discretizado con Euler y sin termino proporcional.
        #
        # Ahora:
        #     correccion = Kp*(e[k] - e[k-1])  +  (alpha/2)*(e[k] + e[k-1])
        #                   |__ proporcional __|    |__ integral trapezoidal __|
        #
        # El trapecio promedia la muestra actual con la anterior, que ademas de
        # ser mas fiel en frecuencia suaviza el ruido. El proporcional acelera
        # la respuesta al cambio sin tocar el regimen: cuando el error se queda
        # quieto, e[k]-e[k-1] es cero y solo queda la integral, asi que el error
        # de regimen sigue siendo nulo.
        #
        # En el primer paso, donde no hay muestra anterior, se usa la actual
        # para las dos. El trapecio degenera en rectangulo y el proporcional no
        # aporta, que es exactamente el comportamiento viejo: arrancar igual que
        # antes y acelerar recien cuando hay historia.
        previo = self.error_previo_mv
        if previo is None:
            previo = error
        kp = self.kp_relativo * self.alpha
        correccion_mv = (kp * (error - previo)
                         + 0.5 * self.alpha * (error + previo))
        self.error_previo_mv = error
        deseado = correccion_mv / self.ganancia_fina

        # VERNIER cuando un codigo del fino es mas grueso que la precision
        # pedida. Pasa a ganancias altas: en x24 un codigo mueve LPo 2.830 mV
        # sobre una ventana de 4.826, asi que con el fino solo no se centra
        # nada. Los dos actuadores en oposicion dan decimas de milivolt.
        usar_vernier = abs(self.ganancia_fina) > self.tolerancia_real_mv
        paso_vernier_grueso = 0
        if usar_vernier:
            paso_fino, paso_vernier_grueso = paso_vernier(
                correccion_mv, self.ganancia_fina, self.ganancia_gruesa)
            self.pasos_vernier += 1
        else:
            paso_fino = int(round(deseado))
            if paso_fino == 0 and abs(deseado) > 0.25:
                # Sin esto el lazo se queda quieto ante errores chicos porque el
                # redondeo se los come, y nunca llega. Un codigo es un codigo.
                paso_fino = 1 if deseado > 0 else -1

        nuevo_fino = self.codigo_fino + paso_fino
        saturado = abs(nuevo_fino) > self.limite_fino
        if saturado:
            # ANTI-WINDUP: el integrador no puede acumular mas alla del limite.
            # Si acumulara, al volver a la zona util tendria que descargar todo
            # lo acumulado y sobrepasaria feo.
            nuevo_fino = self.limite_fino if nuevo_fino > 0 else -self.limite_fino
        paso_fino = nuevo_fino - self.codigo_fino

        # LAZO EXTERNO: el grueso devuelve al fino hacia el centro.
        #
        # Su referencia no es el tap sino el CODIGO del fino. Solo actua cuando
        # el fino salio de su zona comoda, y diez veces mas lento, para no
        # pelearse con el lazo interno.
        paso_grueso = paso_vernier_grueso
        exceso = 0
        if abs(nuevo_fino) > self.zona_comoda:
            exceso = (abs(nuevo_fino) - self.zona_comoda) * (
                1 if nuevo_fino > 0 else -1)

            if usar_vernier:
                # DESCARGUE NEUTRO. Mover el grueso solo seria una patada de
                # miles de milivolts que el fino no puede compensar. Se mueven
                # los dos juntos con efecto neto casi nulo: el fino se acerca al
                # centro y el tap casi no se entera.
                m_fino, m_grueso = paso_neutro(self.ganancia_fina,
                                               self.ganancia_gruesa)
                signo = -1 if nuevo_fino > 0 else 1
                candidato_fino = nuevo_fino + signo * m_fino
                if abs(candidato_fino) <= self.limite_fino:
                    nuevo_fino = candidato_fino
                    paso_fino = nuevo_fino - self.codigo_fino
                    paso_grueso += signo * m_grueso
                nuevo_grueso = max(-self.limite_grueso,
                                   min(self.limite_grueso,
                                       self.codigo_grueso + paso_grueso))
                paso_grueso = nuevo_grueso - self.codigo_grueso
                self.codigo_fino = nuevo_fino
                self.codigo_grueso += paso_grueso
                salida = SalidaPI(codigo_fino=self.codigo_fino,
                                  codigo_grueso=self.codigo_grueso,
                                  error_real_mv=error, paso_fino=paso_fino,
                                  paso_grueso=paso_grueso, saturado=saturado,
                                  motivo="descargue neutro del fino")
                self.historial.append(salida)
                return salida
            # Cuanto tap hay que compensar para que el fino pueda volver.
            mv_a_transferir = exceso * self.ganancia_fina
            # OJO: aca iba multiplicado por LENTITUD_EXTERNO, y como
            # alpha_externo ya viene dividida por ella, se cancelaban. El lazo
            # externo corria a la misma velocidad que el interno y se peleaban:
            # el error oscilaba entre -1.230 y +173 mV sin converger nunca.
            # El externo TIENE que ser lento.
            deseado_grueso = (self.alpha_externo * mv_a_transferir
                              / self.ganancia_gruesa)
            paso_grueso += int(round(deseado_grueso))
            if paso_grueso == 0 and abs(deseado_grueso) > 0.4:
                # El externo necesita su propio paso minimo, igual que el
                # interno. Sin esto los dos lazos se quedan quietos por
                # redondeo y el fino se estanca fuera de su zona comoda: en la
                # simulacion quedaba en 82 con la zona terminando en 60. El
                # umbral es mas alto que el del interno a proposito, para que el
                # externo no se ponga a caminar por ruido.
                paso_grueso = 1 if deseado_grueso > 0 else -1
            nuevo_grueso = max(-self.limite_grueso,
                               min(self.limite_grueso,
                                   self.codigo_grueso + paso_grueso))
            paso_grueso = nuevo_grueso - self.codigo_grueso
        else:
            nuevo_grueso = max(-self.limite_grueso,
                               min(self.limite_grueso,
                                   self.codigo_grueso + paso_grueso))
            paso_grueso = nuevo_grueso - self.codigo_grueso

        self.codigo_fino = nuevo_fino
        self.codigo_grueso += paso_grueso
        motivo = ""
        if saturado:
            motivo = f"fino en su limite (±{self.limite_fino})"
        elif paso_grueso:
            motivo = f"el fino paso su zona comoda por {exceso:+d} códigos"
        salida = SalidaPI(codigo_fino=self.codigo_fino,
                          codigo_grueso=self.codigo_grueso,
                          error_real_mv=error, paso_fino=paso_fino,
                          paso_grueso=paso_grueso, saturado=saturado,
                          motivo=motivo)
        self.historial.append(salida)
        return salida

    def convergido(self, tolerancia_real_mv: float,
                   muestras: int = 3) -> bool:
        """True si los ultimos pasos quedaron dentro de tolerancia.

        Se piden varias seguidas y no una: la planta tiene mas de un polo y el
        error puede pasar por cero mientras el sistema sigue viajando.
        """
        if len(self.historial) < muestras:
            return False
        ultimas = self.historial[-muestras:]
        if any(s.error_real_mv != s.error_real_mv for s in ultimas):
            # Alguna lectura fue invalida (NaN). No se puede declarar
            # convergencia sobre datos que no existen.
            return False
        return all(abs(s.error_real_mv) <= tolerancia_real_mv
                   for s in ultimas)
