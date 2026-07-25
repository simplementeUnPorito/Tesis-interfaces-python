"""Decodificador del formato .geoq que sube el maestro en modo ENLACE.

El maestro no arma carpetas ni convierte nada: encola los lotes crudos tal como
llegan por ESP-NOW (3 bytes por muestra) mas una tabla de nodos con lo que sabe
de cada esclavo (rol GEO/HAMMER, fs real del PSoC, MAC). Todo el trabajo de
estructurar vive aca, del lado de Python, para no cargar mas el firmware.

El formato esta especificado en el encabezado de
``src/firmware/esp32/Nodo comunicación/master/src/link_mode.h`` — este modulo es
la contraparte y tiene que moverse junto con aquel.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

MAGIC = b"GEOQ"
VERSION = 1

HEADER_SIZE = 48
NODE_ENTRY_SIZE = 14
RECORD_SIZE = 102
SAMPLES_PER_RECORD = 30

# SLAVE_HW_* de sync_protocol.h
HW_UNKNOWN = 0
HW_GEO = 2
HW_HAMMER = 1

# Escala por defecto del ADC: ±2.5 V es la config 0 del PSoC y la que asume la
# SPA (ADC_CONFIGS[0] en data/js/config.js: 131072 / 2.5 cuentas por volt).
# El .geoq todavia no transporta la config de ADC por nodo, asi que el sink la
# toma de aca y se puede override por CLI. Ver README.
DEFAULT_COUNTS_PER_VOLT = 131072.0 / 2.5


class GeoqError(ValueError):
    """Archivo .geoq invalido o truncado."""


def _role_from_hw_class(hw_class: int) -> str:
    if hw_class == HW_HAMMER:
        return "hammer"
    if hw_class == HW_GEO:
        return "geo"
    return "unknown"


@dataclass
class GeoqNode:
    node_id: int
    hw_class: int
    sample_rate: int
    n_batches: int
    psoc_ok: bool
    sd_present: bool
    mac: str
    samples: list[int] = field(default_factory=list)  # cuentas crudas con signo

    @property
    def role(self) -> str:
        return _role_from_hw_class(self.hw_class)

    @property
    def pcb_id(self) -> str:
        """Identificador estable del PCB: los ultimos 3 bytes de la MAC.

        Es lo unico que distingue fisicamente a un esclavo sin que el navegador
        haya puesto un alias. Si no hay MAC (HELLO viejo), se cae al node_id.
        """
        clean = self.mac.replace(":", "")
        if clean and set(clean) != {"0"}:
            return clean[-6:].upper()
        return f"S{self.node_id}"

    def volts(self, counts_per_volt: float = DEFAULT_COUNTS_PER_VOLT) -> list[float]:
        return [c / counts_per_volt for c in self.samples]


@dataclass
class GeoqCapture:
    version: int
    session_id: int
    uptime_ms: int
    n_batches: int
    site: str
    distance_mm: int
    epoch_s: int
    nodes: list[GeoqNode]

    @property
    def distance_m(self) -> float:
        return self.distance_mm / 1000.0

    def node_by_role(self, role: str) -> GeoqNode | None:
        for n in self.nodes:
            if n.role == role and n.samples:
                return n
        return None

    @property
    def fs(self) -> float:
        """fs representativa de la captura: la del geofono, o la primera valida."""
        geo = self.node_by_role("geo")
        if geo and geo.sample_rate:
            return float(geo.sample_rate)
        for n in self.nodes:
            if n.sample_rate:
                return float(n.sample_rate)
        return 0.0


def _unpack_sample(b0: int, b1: int, b2: int) -> int:
    """3 bytes little-endian con signo de 24 bits -> int.

    Mismo empaquetado que usa el firmware al reenviar a MATLAB
    (digi0 | digi1<<8 | digi2<<16, con extension de signo en el bit 23).
    """
    val = b0 | (b1 << 8) | (b2 << 16)
    if val & 0x800000:
        val -= 0x1000000
    return val


def parse(data: bytes) -> GeoqCapture:
    """Decodifica un .geoq completo en memoria.

    Tolera un archivo truncado en el ultimo registro (el maestro puede haberse
    quedado sin FS a mitad de escritura): descarta el resto parcial en vez de
    fallar, porque una captura incompleta sigue siendo util.
    """
    if len(data) < HEADER_SIZE:
        raise GeoqError(f"archivo demasiado corto: {len(data)} B")
    if data[:4] != MAGIC:
        raise GeoqError(f"magic invalido: {data[:4]!r}")

    version = data[4]
    if version != VERSION:
        raise GeoqError(f"version no soportada: {version}")

    node_count = data[5]
    n_batches = struct.unpack_from("<H", data, 6)[0]
    session_id = struct.unpack_from("<I", data, 8)[0]
    uptime_ms = struct.unpack_from("<I", data, 12)[0]
    site = data[16:32].split(b"\x00", 1)[0].decode("utf-8", errors="replace")
    distance_mm = struct.unpack_from("<I", data, 32)[0]
    epoch_s = struct.unpack_from("<I", data, 36)[0]

    off = HEADER_SIZE
    need = off + node_count * NODE_ENTRY_SIZE
    if len(data) < need:
        raise GeoqError(f"tabla de nodos truncada: {len(data)} B < {need} B")

    nodes: dict[int, GeoqNode] = {}
    order: list[int] = []
    for _ in range(node_count):
        node_id = data[off]
        hw_class = data[off + 1]
        sample_rate = struct.unpack_from("<H", data, off + 2)[0]
        nb = struct.unpack_from("<H", data, off + 4)[0]
        flags = data[off + 6]
        mac = ":".join(f"{b:02X}" for b in data[off + 8:off + 14])
        nodes[node_id] = GeoqNode(
            node_id=node_id,
            hw_class=hw_class,
            sample_rate=sample_rate,
            n_batches=nb,
            psoc_ok=bool(flags & 0x01),
            sd_present=bool(flags & 0x02),
            mac=mac,
        )
        order.append(node_id)
        off += NODE_ENTRY_SIZE

    while off + RECORD_SIZE <= len(data):
        node_id = data[off]
        payload = data[off + 12:off + RECORD_SIZE]
        node = nodes.get(node_id)
        if node is not None:
            for i in range(SAMPLES_PER_RECORD):
                j = i * 3
                node.samples.append(_unpack_sample(payload[j], payload[j + 1], payload[j + 2]))
        off += RECORD_SIZE

    return GeoqCapture(
        version=version,
        session_id=session_id,
        uptime_ms=uptime_ms,
        n_batches=n_batches,
        site=site,
        distance_mm=distance_mm,
        epoch_s=epoch_s,
        nodes=[nodes[i] for i in order],
    )


def build(
    *,
    session_id: int,
    site: str,
    distance_mm: int,
    n_batches: int,
    nodes: list[tuple[GeoqNode, list[int]]],
    uptime_ms: int = 0,
    epoch_s: int = 0,
) -> bytes:
    """Serializa un .geoq. Existe para los tests: es el generador que imita al
    firmware, y mantiene el formato verificado de los dos lados."""
    out = bytearray()
    out += MAGIC
    out += bytes([VERSION, len(nodes)])
    out += struct.pack("<H", n_batches)
    out += struct.pack("<I", session_id)
    out += struct.pack("<I", uptime_ms)
    out += site.encode("utf-8")[:16].ljust(16, b"\x00")
    out += struct.pack("<I", distance_mm)
    out += struct.pack("<I", epoch_s)
    out += b"\x00" * 8

    for node, _samples in nodes:
        flags = (0x01 if node.psoc_ok else 0) | (0x02 if node.sd_present else 0)
        mac_bytes = bytes(int(p, 16) for p in node.mac.split(":")) if node.mac else b"\x00" * 6
        out += bytes([node.node_id, node.hw_class])
        out += struct.pack("<H", node.sample_rate)
        out += struct.pack("<H", node.n_batches)
        out += bytes([flags, 0])
        out += mac_bytes.ljust(6, b"\x00")

    for node, samples in nodes:
        for seq in range(0, len(samples), SAMPLES_PER_RECORD):
            chunk = samples[seq:seq + SAMPLES_PER_RECORD]
            if len(chunk) < SAMPLES_PER_RECORD:
                chunk = chunk + [0] * (SAMPLES_PER_RECORD - len(chunk))
            out += bytes([node.node_id])
            out += struct.pack("<H", seq // SAMPLES_PER_RECORD)
            out += bytes([0])
            out += struct.pack("<Q", 0)
            for s in chunk:
                v = s & 0xFFFFFF
                out += bytes([v & 0xFF, (v >> 8) & 0xFF, (v >> 16) & 0xFF])

    return bytes(out)
