"""config.py — All application-wide constants for the geophone scope."""

# ── Serial / Protocol ────────────────────────────────────────────────────────
BAUD: int = 921_600
PKT_HEADER: int = 0x56       # Master → PC packet header
CMD_HEADER: int = 0xAB       # PC → Master command header
CMD_DIRECTED: int = 0xBD     # Sub-header for slave-directed commands
PKT_LEN: int = 6             # Every packet is exactly 6 bytes

# ── Packet types (byte 3 of every PKT_HEADER packet) ────────────────────────
PTYPE_DATA: int       = 0x00  # ADC sample
PTYPE_HEARTBEAT: int  = 0x01  # pga, vdac, master_state
PTYPE_ACK: int        = 0x07  # ACK: b2=cmd, b1=val
PTYPE_LATENCY: int    = 0xFC  # START latency: 3-byte µs
PTYPE_STATUS: int     = 0xFD  # Status/HELLO from master or slave
PTYPE_READY: int      = 0xFE  # READY: b2=n_slaves_ready

# ── Commands (PC → Master) ───────────────────────────────────────────────────
CMD_STREAM: int       = 0xA1  # Stream on(1)/off(0)
CMD_ARM: int          = 0xA2  # ARM, param=n_slaves
CMD_START: int        = 0xA3  # START, N=n_batches (16-bit)
CMD_STOP: int         = 0xA4  # STOP
CMD_STATUS: int       = 0xA5  # Request status
CMD_DEBUG: int        = 0xA7  # Debug broadcast on/off
CMD_SET_RECLEN: int   = 0xAE  # Set record length N (16-bit)
CMD_SCOPE_MULTI: int  = 0xB0  # Scope multi-start

# ── Sub-commands (directed to slave) ────────────────────────────────────────
SUBCMD_PGA: int        = 0xA6  # Set PGA gain (code 0-8)
SUBCMD_PGAVDAC: int    = 0xA9  # Set PGAvdac (code 0-8)
SUBCMD_VDAC: int       = 0xAA  # Set VDAC byte (0-255)
SUBCMD_DEBUG: int      = 0xA7  # Debug node on/off
SUBCMD_VER: int        = 0xB2  # Single capture (Ver)
SUBCMD_LATENCY: int    = 0xAF  # Start latency probe

# ── Acquisition parameters ───────────────────────────────────────────────────
# NOTE: intentionally NO nominal/default sample-rate constant. The PSoC's ADC
# rate depends on its analog front-end programming and can be reconfigured at
# any time, so Fs must ALWAYS come from the slave's HELLO (see
# MainWindow._effective_fs / NodeData.fs_known) — never a guessed fallback.
SAMPLES_PER_BATCH: int = 30        # Samples per ESP-NOW batch
PSOC_CAPTURE_MAX_BATCHES: int = 512  # PSoC store-and-forward RAM limit
TEST_DEFAULT_SECONDS: float = 0.2    # Short directed debug burst
TEST_MIN_SECONDS: float = 0.1
TEST_MAX_SECONDS: float = 1.0
DEFAULT_SAMPLE_RATE_HZ: int = 2929 # Measured default before exact HELLO arrives
DISP_SAMP: int    = DEFAULT_SAMPLE_RATE_HZ * 3   # Startup placeholder; ~3 s
MAX_BUF_S: int    = 10             # Default circular buffer length, seconds
MAX_BUF: int      = DEFAULT_SAMPLE_RATE_HZ * MAX_BUF_S  # Startup placeholder; ~10 s

# ── Nodes ────────────────────────────────────────────────────────────────────
MAX_NODES: int = 4                 # 1 master gateway + 3 slaves
NODE_NAMES: list[str] = ["Maestro", "Esclavo 1", "Esclavo 2", "Esclavo 3"]
MASTER_NODE_ID: int = 0xFF         # node_id used by master in status/ready packets

# ── PGA ─────────────────────────────────────────────────────────────────────
GAIN_CODES: list[int]  = [1, 2, 4, 8, 16, 24, 32, 48, 50]
GAIN_NAMES: list[str]  = ["1x", "2x", "4x", "8x", "16x", "24x", "32x", "48x", "50x"]

# ── VDAC ────────────────────────────────────────────────────────────────────
VDAC_STEP: float = 0.004           # V per LSB
VDAC_MIN: int    = 0
VDAC_MAX: int    = 255
VDAC_FULL_SCALE_V: float = VDAC_MAX * VDAC_STEP

# ── ADC scaling ──────────────────────────────────────────────────────────────
# PSoC ADC config: 18-bit DelSig, +/-2.5 V input range, left alignment fixed
# in firmware before transmission. ADC_CFG1_COUNTS_PER_VOLT from ADC.h = 52429.
ADC_COUNTS_PER_VOLT: float = 52_429.0

# ── Master states (b0 of heartbeat) ─────────────────────────────────────────
MASTER_STATE_NAMES: dict[int, str] = {
    0: "IDLE",
    1: "ARMING",
    2: "ARMED",
    3: "RUNNING",
    4: "STOPPING",
    5: "DUMPING",
    6: "PRESTART",
    7: "SCOPE_MULTI",
}
MASTER_STATE_IDLE: int       = 0
MASTER_STATE_ARMED: int      = 2
MASTER_STATE_RUNNING: int    = 3
MASTER_STATE_DUMPING: int    = 5
MASTER_STATE_SCOPE_MULTI: int = 7

# ── TX modes ─────────────────────────────────────────────────────────────────
TX_RAW: int      = 0
TX_FILTERED: int = 1
TX_DEBUG: int    = 2
TX_MODE_NAMES: list[str] = ["Raw ADC", "Filtered ADC", "Debug"]

# ── GUI / Rendering ──────────────────────────────────────────────────────────
RENDER_PERIOD_MS: int  = 150       # Render timer period
SERIAL_POLL_MS: int    = 50        # Serial polling interval (used as thread sleep)
LEFT_PANEL_W: int      = 340       # Tab panel width in pixels

# ── Logging ──────────────────────────────────────────────────────────────────
N_LOG_SESSIONS: int = 10           # How many log sessions to keep on disk

# ── Retry / latency probing ──────────────────────────────────────────────────
MAX_RETRIES: int       = 3
RETRY_SEC: float       = 1.5
START_LATENCY_PROBES: int    = 12
START_LATENCY_PROBE_GAP_S: float = 0.04

# ── Notch defaults ───────────────────────────────────────────────────────────
NOTCH_F0: float         = 50.0     # Hz
NOTCH_DEFAULT_HARM: int = 3

# ── Save ─────────────────────────────────────────────────────────────────────
DEFAULT_SAVE_NAME: str = "muestra"
