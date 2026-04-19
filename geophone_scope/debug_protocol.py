"""
debug_protocol.py — PSoC compact debug event parser.

Debug packet: [0xDD][EVENT][LEN][payload...][CRC]
  CRC = XOR of all bytes including 0xDD header.
"""

import datetime

DBG_HEADER = 0xDD

# Channel constants
DBG_CH_USB  = 0
DBG_CH_UART = 1
DBG_CH_BOTH = 2

# Event code → (level, label, payload_fmt)
# payload_fmt: list of field names matching payload bytes
_EVENTS = {
    0x10: ("INFO",  "Boot: ADC OK",         ["adc_cfg", "buf_gain"]),
    0x11: ("INFO",  "Boot: DMA ready",      []),
    0x12: ("INFO",  "Boot: USB enumerated", []),
    0x13: ("INFO",  "Boot: TestBench OK",   []),
    0x20: ("INFO",  "Streaming ON",         []),
    0x21: ("INFO",  "Streaming OFF",        []),
    0x30: ("INFO",  "Command received",     ["cmd_code"]),
    0x40: ("ERROR", "DMA error",            ["chain_id"]),
    0x41: ("ERROR", "ADC lost lock",        []),
    0x50: ("INFO",  "VDAC sequence loaded", ["len_hi", "len_lo"]),
    0x60: ("INFO",  "ADC config changed",   ["adc_cfg", "buf_gain"]),
}

_CFG_RANGE  = {1: "±0.512V", 2: "±1.024V", 3: "±2.048V", 4: "±6.144V"}
_CMD_NAMES  = {
    0x01: "START", 0x02: "STOP", 0x03: "SET_MUX_IN", 0x04: "SET_MUX_OUT",
    0x05: "SET_ADC", 0x06: "VDAC_LOAD", 0x07: "SET_DEBUG",
    0x08: "SET_DBG_CH", 0x09: "GET_STATE",
}
_CHAIN_NAMES = {0: "A (ADC→Filter)", 1: "B (ADC→RAM)", 2: "C (Filter→RAM)", 3: "D (SAR→RAM)"}


def _calc_crc(data: bytes) -> int:
    crc = 0
    for b in data:
        crc ^= b
    return crc


def parse_debug_packet(raw: bytes) -> dict | None:
    """
    Parse one debug packet from raw bytes.
    Returns dict or None on CRC/format error.
    """
    if len(raw) < 4:
        return None
    if raw[0] != DBG_HEADER:
        return None
    event = raw[1]
    plen  = raw[2]
    if len(raw) < 4 + plen:
        return None
    payload = raw[3:3 + plen]
    crc     = raw[3 + plen]
    if _calc_crc(raw[:3 + plen]) != crc:
        return None

    level, label, fields = _EVENTS.get(event, ("WARN", f"Unknown event 0x{event:02X}", []))
    detail = _format_payload(event, payload, fields)

    return {
        "event":   event,
        "level":   level,
        "label":   label,
        "detail":  detail,
        "payload": bytes(payload),
        "ts":      datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3],
    }


def _format_payload(event: int, payload: bytes, fields: list) -> str:
    if not payload:
        return ""

    if event in (0x10, 0x60) and len(payload) >= 2:
        cfg  = payload[0]
        gain = payload[1]
        rng  = _CFG_RANGE.get(cfg, f"CFG{cfg}")
        return f"cfg={rng}  buf_gain={gain}×"

    if event == 0x30 and len(payload) >= 1:
        name = _CMD_NAMES.get(payload[0], f"0x{payload[0]:02X}")
        return f"cmd={name}"

    if event == 0x40 and len(payload) >= 1:
        name = _CHAIN_NAMES.get(payload[0], f"chain {payload[0]}")
        return f"chain={name}"

    if event == 0x50 and len(payload) >= 2:
        n = (payload[0] << 8) | payload[1]
        return f"{n} samples"

    # Generic fallback
    return " ".join(f"{b:02X}" for b in payload)


def format_event(d: dict) -> str:
    """Return a single display line for a debug event dict."""
    detail = f"  {d['detail']}" if d["detail"] else ""
    return f"[{d['level'][0]}] {d['ts']}  {d['label']}{detail}"


# ── State packet parser ───────────────────────────────────────────────────────

STATE_HEADER  = 0xCC
STATE_PKT_LEN = 10   # fixed

def parse_state_packet(raw: bytes) -> dict | None:
    """
    Parse the 10-byte PSoC state packet [0xCC].
    Returns dict or None.
    """
    if len(raw) < STATE_PKT_LEN:
        return None
    if raw[0] != STATE_HEADER or raw[1] != 7:
        return None
    crc = 0
    for b in raw[:9]:
        crc ^= b
    if crc != raw[9]:
        return None

    return {
        "adc_cfg":    raw[2],
        "buf_gain":   raw[3],
        "streaming":  bool(raw[4]),
        "debug_en":   bool(raw[5]),
        "debug_ch":   raw[6],
        "vdac_mode":  raw[7],
        "filter_sel": raw[8],
    }
