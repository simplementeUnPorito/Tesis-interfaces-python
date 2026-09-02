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
PTYPE_ACK: int        = 0x07  # ACK: b2=cmd, b1=status/eco low8, b0 reservado (no uint16)
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
SUBCMD_ADC_CONFIG: int = 0xBA  # ADC range: 1=+/-2.5, 2=+/-0.512, 3=+/-1.024, 4=+/-0.625 V

# ── Acquisition parameters ───────────────────────────────────────────────────
# Hardware timing/export must use the slave HELLO (see MainWindow._effective_fs
# / NodeData.fs_known). DEFAULT_SAMPLE_RATE_HZ below is the canonical N=1
# startup fallback used for placeholder buffers before that report arrives.
SAMPLES_PER_BATCH: int = 30        # Samples per ESP-NOW batch
PSOC_CAPTURE_MAX_BATCHES: int = 512  # PSoC store-and-forward RAM limit
TEST_DEFAULT_SECONDS: float = 0.2    # Short directed debug burst
TEST_MIN_SECONDS: float = 0.1
TEST_MAX_SECONDS: float = 1.0
DEFAULT_SAMPLE_RATE_HZ: int = 2604 # Native N=1 default before exact HELLO arrives
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
# PSoC ADC configs exposed by the firmware/web UI. All run at 2604 Hz for N=1.
ADC_CONFIGS = [
    {"code": 1, "label": "+/-2.5 V", "range_v": 2.5, "fs_hz": 2604, "counts_per_volt": 131_072 / 2.5},
    {"code": 2, "label": "+/-0.512 V", "range_v": 0.512, "fs_hz": 2604, "counts_per_volt": 131_072 / 0.512},
    {"code": 3, "label": "+/-1.024 V", "range_v": 1.024, "fs_hz": 2604, "counts_per_volt": 131_072 / 1.024},
    {"code": 4, "label": "+/-0.625 V", "range_v": 0.625, "fs_hz": 2604, "counts_per_volt": 131_072 / 0.625},
]
ADC_COUNTS_PER_VOLT: float = ADC_CONFIGS[0]["counts_per_volt"]

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
NOTCH_SEARCH_HZ: float  = 2.0      # buscar pico real en NOTCH_F0±este margen

# ── Save ─────────────────────────────────────────────────────────────────────
DEFAULT_SAVE_NAME: str = "muestra"
