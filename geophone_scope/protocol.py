"""
protocol.py — PSoC binary packet protocol parser and command builders.

Packet types (PSoC -> PC):
  0xAA — 12-byte data sample
  0xCC — 10-byte state/config confirmation
  0xDD — N-byte debug event  (see debug_protocol.py)

Command frame (PC -> PSoC): [0xBB][CMD][LEN][payload...]
  VDAC_LOAD exception:       [0xBB][0x06][len_hi][len_lo][data...]
"""

import struct

# ── Packet / frame constants ──────────────────────────────────────────────────

PKT_DATA_HEADER  = 0xAA
PKT_STATE_HEADER = 0xCC
CMD_MARKER       = 0xBB
PKT_DATA_SIZE    = 12

# ── Command codes ─────────────────────────────────────────────────────────────

CMD_START       = 0x01
CMD_STOP        = 0x02
CMD_SET_MUX_IN  = 0x03
CMD_SET_MUX_OUT = 0x04
CMD_SET_ADC     = 0x05   # replaces old CMD_SET_GAIN; payload: [cfg][buf_gain]
CMD_VDAC_LOAD   = 0x06
CMD_SET_DEBUG   = 0x07
CMD_SET_DBG_CH  = 0x08
CMD_GET_STATE   = 0x09
CMD_LOAD_FIR    = 0x0A   # 2-byte length + Q1.15 int16 coefficients (2 bytes each)
CMD_FIR_CLEAR   = 0x0B   # disable FIR, pass-through mode

# ── Debug channel constants ───────────────────────────────────────────────────

DBG_CH_USB  = 0
DBG_CH_UART = 1
DBG_CH_BOTH = 2

# ── ADC config tables ─────────────────────────────────────────────────────────

# Base input range at PSoC Creator-configured buffer gain (8 for CFG2-4, 1 for CFG1)
CONFIG_BASE_RANGE = {1: 0.512, 2: 1.024, 3: 2.048, 4: 6.144}
CONFIG_BASE_GAIN  = {1: 1,     2: 8,     3: 8,     4: 8    }

CONFIG_LABEL = {
    1: "CFG1  ±0.512 V  (18-bit)",
    2: "CFG2  ±1.024 V  (17-bit)",
    3: "CFG3  ±2.048 V  (17-bit)",
    4: "CFG4  ±6.144 V  (17-bit)",
}

ADC_BITS      = 17   # 17-bit for CFG2-4 (use same for CFG1 which is 18-bit — conservative)
ADC_FULLSCALE = 2 ** (ADC_BITS - 1)

SAR_VREF      = 1.024
SAR_BITS      = 12
SAR_FULLSCALE = 2 ** (SAR_BITS - 1)

# Flags
FLAG_MUX_IN  = 0x01
FLAG_MUX_OUT = 0x02


def effective_fullscale(cfg: int, buf_gain: int) -> float:
    """Effective ±V range at the physical input pins."""
    base  = CONFIG_BASE_RANGE.get(cfg, 6.144)
    bgain = CONFIG_BASE_GAIN.get(cfg, 8)
    return base * bgain / max(buf_gain, 1)


def parse_gain_byte(b: int) -> tuple[int, int]:
    """Decode byte 10 of data packet → (cfg:1-4, buf_gain:1/2/4/8)."""
    cfg      = b & 0x0F
    buf_gain = 1 << ((b >> 4) & 0x03)
    return cfg, buf_gain


# ── Data packet parser ────────────────────────────────────────────────────────

def _sign_extend_24(val: int) -> int:
    return val - 0x1000000 if (val & 0x800000) else val


def _calc_crc(data: bytes) -> int:
    crc = 0
    for b in data:
        crc ^= b
    return crc


def parse_packet(raw: bytes) -> dict | None:
    """
    Parse a 12-byte PSoC data packet (header 0xAA).
    Returns a dict with converted values, or None on error.
    """
    if len(raw) < PKT_DATA_SIZE or raw[0] != PKT_DATA_HEADER:
        return None
    if _calc_crc(raw[:11]) != raw[11]:
        return None

    flags     = raw[1]
    raw_input = struct.unpack_from('<h', raw, 2)[0]

    analog_val   = raw[4] | (raw[5] << 8) | (raw[6] << 16)
    digital_val  = raw[7] | (raw[8] << 8) | (raw[9] << 16)
    analog_s     = _sign_extend_24(analog_val)
    digital_s    = _sign_extend_24(digital_val)

    cfg, buf_gain = parse_gain_byte(raw[10])
    vfs = effective_fullscale(cfg, buf_gain)

    mux_in = flags & FLAG_MUX_IN
    raw_input_v = (raw_input / SAR_FULLSCALE * SAR_VREF
                   if mux_in == 0
                   else raw_input / 128.0 * vfs)

    return {
        "raw_input":    raw_input_v,
        "post_analog":  analog_s  / ADC_FULLSCALE * vfs,
        "post_digital": digital_s / ADC_FULLSCALE * vfs,
        "raw_analog":   analog_s,
        "raw_digital":  digital_s,
        "adc_cfg":      cfg,
        "buf_gain":     buf_gain,
        "vfs":          vfs,
        "mux_in":       mux_in,
        "mux_out":      (flags & FLAG_MUX_OUT) >> 1,
    }


# ── Command builders ──────────────────────────────────────────────────────────

def cmd_start() -> bytes:
    return bytes([CMD_MARKER, CMD_START, 0])

def cmd_stop() -> bytes:
    return bytes([CMD_MARKER, CMD_STOP, 0])

def cmd_set_mux_in(source: int) -> bytes:
    return bytes([CMD_MARKER, CMD_SET_MUX_IN, 1, source & 0xFF])

def cmd_set_mux_out(output: int) -> bytes:
    return bytes([CMD_MARKER, CMD_SET_MUX_OUT, 1, output & 0xFF])

def cmd_set_adc(cfg: int, buf_gain: int) -> bytes:
    """
    cfg: 1-4 (ADC_DelSig config)
    buf_gain: 1, 2, 4, or 8  (forced to 1 by firmware if cfg==1)
    """
    return bytes([CMD_MARKER, CMD_SET_ADC, 2, cfg & 0xFF, buf_gain & 0xFF])

def cmd_vdac_load(sequence: list[int]) -> bytes:
    """
    sequence: list of int (0-255), up to 3000 samples.
    Frame: [BB][06][len_hi][len_lo][data...]  — no embedded length in payload.
    """
    n   = min(len(sequence), 3000)
    seq = bytes(sequence[:n])
    return bytes([CMD_MARKER, CMD_VDAC_LOAD, (n >> 8) & 0xFF, n & 0xFF]) + seq

def cmd_set_debug(enable: bool) -> bytes:
    return bytes([CMD_MARKER, CMD_SET_DEBUG, 1, 1 if enable else 0])

def cmd_set_debug_ch(ch: int) -> bytes:
    """ch: DBG_CH_USB=0, DBG_CH_UART=1, DBG_CH_BOTH=2"""
    return bytes([CMD_MARKER, CMD_SET_DBG_CH, 1, ch & 0xFF])

def cmd_get_state() -> bytes:
    return bytes([CMD_MARKER, CMD_GET_STATE, 0])


def cmd_load_fir(coeffs_float) -> bytes:
    """
    Build CMD_LOAD_FIR frame from float coefficients.

    coeffs_float : sequence of floats in [-1.0, 1.0].  Max 255 taps.
    Converts to Q1.15 int16 (multiply by 32767 and round), then packs as
    little-endian pairs.  Frame: [BB][0A][len_hi][len_lo][c0_lo][c0_hi]...
    """
    import struct as _struct
    taps = list(coeffs_float)[:255]
    n    = len(taps)
    if n == 0:
        return cmd_fir_clear()
    payload = bytearray()
    for c in taps:
        q = max(-32768, min(32767, int(round(c * 32768.0))))
        payload += _struct.pack('<h', q)
    plen = len(payload)
    return bytes([CMD_MARKER, CMD_LOAD_FIR, (plen >> 8) & 0xFF, plen & 0xFF]) + bytes(payload)


def cmd_fir_clear() -> bytes:
    """Disable software FIR on PSoC — post_digital reverts to raw DFB output."""
    return bytes([CMD_MARKER, CMD_FIR_CLEAR, 0])
