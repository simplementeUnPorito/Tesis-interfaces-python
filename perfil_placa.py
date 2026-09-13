"""Lo que una placa puede saber de si misma antes de salir al campo.

La calibracion completa mide dos clases de cosas muy distintas y hasta ahora las
media todas juntas, cada vez, gastando minutos que en el campo no hay:

  CONSTANTES DE LA PLACA. Cuanto mueve cada IDAC a cada tap, la constante de
  tiempo del pasabanda, la tension de alimentacion. Dependen del cobre y de los
  componentes, no del dia ni del terreno. Se miden una vez en el banco y se
  guardan. Solo hay que rehacerlas si se cambia un componente.

  EL PUNTO DE TRABAJO. Los cuatro codigos que dejan la cadena centrada.
  Dependen de la temperatura, del geofono conectado y de cuanto hace que la
  placa esta encendida. Hay que medirlos en el campo, pero partiendo de los
  ultimos conocidos en vez de partir de cero, que es lo que hace la diferencia
  entre diez minutos y treinta.

Este modulo es el archivo donde vive lo primero, con FECHA, para que se pueda
decidir si sigue vigente. La arquitectura de campo que lo usa esta descrita en
ARQUITECTURA_CALIBRACION_CAMPO.md: la placa arranca en estado "placa nueva",
pide sus parametros a la PC una vez, y desde ahi manda su identificador en los
metadatos de cada captura para que el servidor pueda avisar si quedaron viejos.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path

#: Donde viven los perfiles. Uno por placa.
CARPETA = Path(__file__).resolve().parent / "datos" / "perfiles"

#: Vencimiento sugerido. No es una regla fisica: es el plazo tras el cual
#: conviene volver a medir porque nadie sabe que le paso a la placa mientras
#: tanto. La fecha esta guardada, asi que quien quiera otro criterio lo aplica.
VIGENCIA_DIAS = 90


@dataclass
class PerfilPlaca:
    """Constantes de una placa, con la fecha en que se midieron."""

    id_placa: str
    fecha: str
    #: mV por codigo de cada actuador sobre cada tap, con la ganancia a la que
    #: se midio. Clave "IDAC<e>->ch<c>@x<g>".
    pendientes: dict[str, float] = field(default_factory=dict)
    #: Constante de tiempo del pasabanda, en segundos.
    tau_bp_s: float = 0.0
    #: Alimentacion analogica medida con tester, en mV.
    vdda_mv: float = 0.0
    #: Ultimo punto de trabajo conocido por ganancia de PGAout: clave "x4",
    #: valor [IDAC0, IDAC1, IDAC2, IDAC3].
    puntos: dict[str, list[int]] = field(default_factory=dict)
    #: Ganancias cuyo punto viene de una corrida que efectivamente cerro,
    #: no de una siembra a mano. Solo esos habilitan la busqueda estrecha de
    #: IDAC0: verificar en +-4 codigos un punto que nadie valido seria confiar
    #: en un numero inventado.
    validados: list[str] = field(default_factory=list)
    #: Texto libre: que se cambio, con que instrumento se midio.
    notas: str = ""

    # --- vigencia --------------------------------------------------------
    def dias(self, hoy: date | None = None) -> int:
        """Dias desde que se midio. Negativo si la fecha esta en el futuro."""
        medido = datetime.strptime(self.fecha, "%Y-%m-%d").date()
        return ((hoy or date.today()) - medido).days

    def vigente(self, hoy: date | None = None,
                vigencia_dias: int = VIGENCIA_DIAS) -> bool:
        return 0 <= self.dias(hoy) <= vigencia_dias

    # --- acceso a las pendientes ----------------------------------------
    @staticmethod
    def clave(etapa: int, canal: int, ganancia: int) -> str:
        return f"IDAC{etapa}->ch{canal}@x{ganancia}"

    def pendiente(self, etapa: int, canal: int, ganancia: int) -> float | None:
        """La pendiente medida a esa ganancia, o la escalada desde otra.

        IDAC3 entra despues de PGAout, asi que su pendiente sobre LPo NO se
        escala. Las demas actuan antes y su efecto sobre LPo es proporcional a
        la ganancia, asi que una medida en x1 sirve para todas.
        """
        exacta = self.pendientes.get(self.clave(etapa, canal, ganancia))
        if exacta is not None:
            return exacta
        for clave, valor in self.pendientes.items():
            if not clave.startswith(f"IDAC{etapa}->ch{canal}@x"):
                continue
            otra = int(clave.rsplit("@x", 1)[1])
            if etapa == 3 or canal != 4:
                # No depende de la ganancia de salida.
                return valor
            return valor * ganancia / otra
        return None

    def probado(self, ganancia: int) -> bool:
        """Si HAY un punto validado del que partir, aunque sea de otra ganancia.

        IDAC0 e IDAC1 actuan antes de PGAout, asi que un punto validado a
        cualquier ganancia sirve para acotar su busqueda a cualquier otra.
        """
        return bool(self.validados)

    def punto_de_entrada(self, ganancia: int) -> list[int] | None:
        """El mejor punto de partida disponible para esa ganancia.

        Si hay uno medido a esa misma ganancia se usa entero. Si no, se toma el
        de cualquier otra y se conservan SOLO IDAC0 e IDAC1: esos dos actuan
        antes de PGAout, asi que su punto no depende de la ganancia de salida.
        IDAC2 e IDAC3 si dependen y arrancan de cero.

        Esto es lo que permite correr la escalera de ganancias sin repetir la
        caracterizacion de la fase 1 en cada escalon, que son diez minutos cada
        vez y siempre da lo mismo.
        """
        exacto = self.puntos.get(f"x{ganancia}")
        if exacto:
            return list(exacto)
        for clave in sorted(self.puntos, key=_orden_de_clave):
            otro = self.puntos[clave]
            return [otro[0], otro[1], 0, 0]
        return None

    # --- persistencia ----------------------------------------------------
    def ruta(self, carpeta: Path | None = None) -> Path:
        return (carpeta or CARPETA) / f"{self.id_placa}.json"

    def guardar(self, carpeta: Path | None = None) -> Path:
        destino = self.ruta(carpeta)
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(
            json.dumps(asdict(self), indent=2, ensure_ascii=False),
            encoding="utf-8")
        return destino

    @classmethod
    def cargar(cls, id_placa: str,
               carpeta: Path | None = None) -> "PerfilPlaca | None":
        origen = (carpeta or CARPETA) / f"{id_placa}.json"
        if not origen.exists():
            return None
        return cls(**json.loads(origen.read_text(encoding="utf-8")))


def _orden_de_clave(clave: str) -> tuple[int, str]:
    """Ordena las claves de punto por ganancia, tolerando las que no lo son.

    Las claves normales son "x4", "x16". Pero puede haber alguna anotada a mano
    con otro formato, y ordenar no es motivo para hacer fallar un informe: esas
    van al final, por nombre.
    """
    if clave.startswith("x") and clave[1:].isdigit():
        return (0, f"{int(clave[1:]):06d}")
    return (1, clave)


def resumen(perfil: PerfilPlaca, hoy: date | None = None) -> str:
    """Una linea por cosa, para imprimir al arrancar una calibracion."""
    lineas = [f"Perfil de {perfil.id_placa}, medido el {perfil.fecha} "
              f"({perfil.dias(hoy)} dias)"]
    if not perfil.vigente(hoy):
        lineas.append("  VENCIDO: conviene volver a medir las constantes")
    if perfil.tau_bp_s:
        lineas.append(f"  tau del pasabanda: {perfil.tau_bp_s:.1f} s")
    if perfil.vdda_mv:
        lineas.append(f"  Vdda: {perfil.vdda_mv:.0f} mV")
    for clave in sorted(perfil.pendientes):
        lineas.append(f"  {clave}: {perfil.pendientes[clave]:+.3f} mV/codigo")
    for ganancia in sorted(perfil.puntos, key=_orden_de_clave):
        codigos = perfil.puntos[ganancia]
        lineas.append(f"  punto {ganancia}: IDAC " +
                      " ".join(f"{c:+d}" for c in codigos))
    if perfil.notas:
        lineas.append(f"  notas: {perfil.notas}")
    return "\n".join(lineas)
