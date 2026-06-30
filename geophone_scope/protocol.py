"""protocol.py — Packet encoding and decoding for the geophone ESP32 protocol."""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Optional

import config


@dataclass
class Packet:
    """Parsed 6-byte packet from master (0x56 header)."""

    node_id: int
    ptype: int          # type byte (PTYPE_* constants)
    b2: int             # payload byte 2 (MSB)
    b1: int             # payload byte 1
    b0: int             # payload byte 0 (LSB)
    value24u: int = field(init=False)  # unsigned 24-bit from b2,b1,b0
    value24: int = field(init=False)  # signed 24-bit from b2,b1,b0

    def __post_init__(self) -> None:
        raw = (self.b2 << 16) | (self.b1 << 8) | self.b0
        self.value24u = raw
        # Sign-extend 24-bit to Python int
        if raw & 0x80_0000:
            raw -= 0x100_0000
        self.value24 = raw

    # ── Convenience properties ───────────────────────────────────────────────

    @property
    def is_data(self) -> bool:
        return self.ptype == config.PTYPE_DATA

    @property
    def is_heartbeat(self) -> bool:
        return self.ptype == config.PTYPE_HEARTBEAT

    @property
    def is_ack(self) -> bool:
        return self.ptype == config.PTYPE_ACK

    @property
    def is_ready(self) -> bool:
        return self.ptype == config.PTYPE_READY

    @property
    def is_latency(self) -> bool:
        return self.ptype == config.PTYPE_LATENCY

    @property
    def is_status(self) -> bool:
        return self.ptype == config.PTYPE_STATUS

    # Heartbeat fields
    @property
    def hb_pga(self) -> int:
        """PGA code from a heartbeat packet."""
        return self.b2

    @property
    def hb_vdac(self) -> int:
        """VDAC byte from a heartbeat packet."""
        return self.b1

    @property
    def hb_master_state(self) -> int:
        """Master state from a heartbeat packet."""
        return self.b0

    # ACK fields
    @property
    def ack_cmd(self) -> int:
        return self.b2

    @property
    def ack_val(self) -> int:
        return self.b1

    # READY
    @property
    def ready_n_slaves(self) -> int:
        """Number of slaves ready (from READY packet, node_id=0xFF)."""
        return self.b2

    # STATUS / HELLO
    @property
    def status_is_master(self) -> bool:
        return self.node_id == 0xFF

    @property
    def status_espnow_ok(self) -> int:
        return self.b2

    @property
    def status_ap_ch(self) -> int:
        return self.b1

    @property
    def hello_psoc_ok(self) -> bool:
        """PSoC status from a slave HELLO packet."""
        return bool(self.b1)

    @property
    def hello_fs_hz(self) -> int:
        """Legacy sample rate reported in a slave HELLO packet (b0 = fs/100 Hz, 0 = unknown)."""
        return self.b0 * 100

    @property
    def hello_fs_exact_hz(self) -> int:
        """Exact sample rate from HELLO sub-packet 0x05 (uint16 big-endian)."""
        return (self.b1 << 8) | self.b0

    @property
    def hello_mac_sub(self) -> int:
        """MAC sub-type (0x02/0x03/0x04) for MAC byte pairs."""
        return self.b2

    @property
    def hello_mac_hi(self) -> int:
        """High MAC byte of the pair."""
        return self.b1

    @property
    def hello_mac_lo(self) -> int:
        """Low MAC byte of the pair."""
        return self.b0

    def __repr__(self) -> str:
        return (
            f"Packet(node={self.node_id:#04x} type={self.ptype:#04x} "
            f"b=[{self.b2:#04x},{self.b1:#04x},{self.b0:#04x}] val={self.value24})"
        )


# ── Encoding (PC → Master) ───────────────────────────────────────────────────

def encode_std(cmd: int, param: int) -> bytes:
    """Encode a standard 4-byte command: [0xAB][cmd][param][cmd^param]."""
    cs = (cmd ^ param) & 0xFF
    return bytes([config.CMD_HEADER, cmd & 0xFF, param & 0xFF, cs])


def encode_std16(cmd: int, value16: int) -> bytes:
    """Encode a 5-byte set-N command: [0xAB][cmd][n_lo][n_hi][cmd^n_lo^n_hi]."""
    value16 = int(value16) & 0xFFFF
    n_lo = value16 & 0xFF
    n_hi = (value16 >> 8) & 0xFF
    cs = (cmd ^ n_lo ^ n_hi) & 0xFF
    return bytes([config.CMD_HEADER, cmd & 0xFF, n_lo, n_hi, cs])


def encode_directed(node_id: int, sub_cmd: int, param: int) -> bytes:
    """Encode a 6-byte directed slave command: [0xAB][0xBD][node_id][sub_cmd][param][cs]."""
    node_id = node_id & 0xFF
    sub_cmd = sub_cmd & 0xFF
    param   = param   & 0xFF
    cs = (node_id ^ sub_cmd ^ param) & 0xFF
    return bytes([config.CMD_HEADER, config.CMD_DIRECTED, node_id, sub_cmd, param, cs])


# ── Decoding (Master → PC) ───────────────────────────────────────────────────

def decode_packet(buf: bytes | bytearray) -> Optional[Packet]:
    """Parse exactly 6 bytes into a Packet. Returns None on bad header/length."""
    if len(buf) < config.PKT_LEN:
        return None
    if buf[0] != config.PKT_HEADER:
        return None
    return Packet(
        node_id=buf[1],
        ptype=buf[2],
        b2=buf[3],
        b1=buf[4],
        b0=buf[5],
    )


def find_and_decode(buf: bytearray) -> tuple[list[Packet], bytearray]:
    """
    Consume all complete 6-byte packets from a raw byte buffer.

    Scans for PKT_HEADER (0x56), extracts every aligned 6-byte frame,
    and returns (packets, remaining_buf). Discards framing garbage.
    """
    packets: list[Packet] = []
    i = 0
    while i < len(buf):
        # Find next header byte
        if buf[i] != config.PKT_HEADER:
            i += 1
            continue
        # Need at least 6 bytes from here
        if i + config.PKT_LEN > len(buf):
            break
        pkt = decode_packet(buf[i : i + config.PKT_LEN])
        if pkt is not None:
            packets.append(pkt)
        i += config.PKT_LEN
    return packets, buf[i:]
