"""data_store.py — Per-node circular buffers and acquisition statistics."""

from __future__ import annotations

from collections import deque
from typing import Optional

import config


class NodeData:
    """Holds all runtime state for one node (master or slave)."""

    def __init__(self, node_index: int, max_buf: int = config.MAX_BUF) -> None:
        """
        Initialise buffers for node at index (0=master, 1-3=slaves).

        Args:
            node_index: 0-based index matching protocol node_id (slave_id = node_index).
            max_buf:    Maximum samples in the circular buffer.
        """
        self.node_index: int    = node_index
        self.max_buf: int       = max_buf

        # Voltage sample buffers — deque gives O(1) append and old-sample eviction
        self.raw_buf:  deque[float] = deque(maxlen=max_buf)
        self.filt_buf: deque[float] = deque(maxlen=max_buf)

        # FIR filter state
        self.filt_b:   Optional[object] = None   # numpy array of coefficients
        self.filt_zi:  Optional[object] = None   # numpy filter state
        self.filt_cmd: str               = ""

        # Notch (least-squares harmonic fit, recomputed over the full capture)
        self.notch_enabled:  bool  = False
        self.notch_harm:     int   = config.NOTCH_DEFAULT_HARM

        # DC removal
        self.dc_remove: bool = False

        # Per-node config (updated from heartbeat / ACK packets)
        self.pga_code:  int = 0
        self.vdac_byte: int = 128
        self.pgavdac:   int = 0
        self.psoc_ok:   Optional[bool] = None   # None until first HELLO
        self.mac:       str = ""

        # ADC sample rate — depends on the analog front-end / PSoC programming,
        # so it must come from the hardware (HELLO b0=fs/100), not a static constant.
        self.fs:        float = float(config.FS)  # nominal default until reported
        self.fs_known:  bool  = False              # True once a HELLO reported fs > 0

        # Batch / statistics
        self.batch_count:     int = 0
        self.total_samples:   int = 0
        self.drift_hist:      list[float] = []
        self.latency_hist:    list[float] = []

        # Acquisition timing helpers (used by GUI test/drift logic)
        self.got_first: bool  = False
        self.t_first:   float = 0.0

        # Health (0=unknown, 1=ok, 2=fail)
        self.health: int = 0

        # Visibility in plots
        self.visible: bool = (node_index > 0)  # master (0) not plotted by default

        # Slave ID label
        if node_index == 0:
            self.slave_id = "M"
        else:
            self.slave_id = f"S{node_index}"

        # Pending commands tracking {sub_cmd: (param, send_time, retries)}
        self.pending: dict[int, tuple[int, float, int]] = {}

    # ── Helpers ───────────────────────────────────────────────────────────────

    def resize_buf(self, new_max: int) -> None:
        """Resize circular buffers in-place (preserves most-recent samples)."""
        self.max_buf = new_max
        new_raw  = deque(self.raw_buf,  maxlen=new_max)
        new_filt = deque(self.filt_buf, maxlen=new_max)
        self.raw_buf  = new_raw
        self.filt_buf = new_filt

    def clear(self) -> None:
        """Clear sample buffers and reset filter state."""
        self.raw_buf.clear()
        self.filt_buf.clear()
        self.filt_zi    = None
        self.batch_count = 0
        self.total_samples = 0
        self.got_first   = False
        self.t_first     = 0.0

    def add_drift(self, value: float, max_hist: int = 50) -> None:
        self.drift_hist.append(value)
        if len(self.drift_hist) > max_hist:
            self.drift_hist = self.drift_hist[-max_hist:]

    def add_latency(self, value_us: float, max_hist: int = 50) -> None:
        self.latency_hist.append(value_us)
        if len(self.latency_hist) > max_hist:
            self.latency_hist = self.latency_hist[-max_hist:]

    def format_drift_stats(self) -> str:
        """Return a concise string with mean/std of drift history."""
        if not self.drift_hist:
            return "--"
        import numpy as np
        arr = np.array(self.drift_hist)
        return f"{arr.mean()*1e3:.1f} ± {arr.std()*1e3:.1f} ms"

    def format_latency_stats(self) -> str:
        """Return a concise string with mean/std of latency history (µs)."""
        if not self.latency_hist:
            return "--"
        import numpy as np
        arr = np.array(self.latency_hist)
        return f"{arr.mean():.0f} ± {arr.std():.0f} µs"


class DataStore:
    """Container for all per-node data objects."""

    def __init__(self, max_buf: int = config.MAX_BUF) -> None:
        """Create one NodeData per possible node (index 0-3)."""
        self.nodes: list[NodeData] = [
            NodeData(i, max_buf=max_buf) for i in range(config.MAX_NODES)
        ]

    def __getitem__(self, idx: int) -> NodeData:
        return self.nodes[idx]

    def clear_all(self) -> None:
        """Clear sample buffers on every node."""
        for nd in self.nodes:
            nd.clear()

    def resize_all(self, new_max: int) -> None:
        """Resize every node's buffer."""
        for nd in self.nodes:
            nd.resize_buf(new_max)
