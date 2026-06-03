# Geophone Scope — Python/PyQt6

Python replacement for `InterfaceESP.m`. Functionally equivalent to the MATLAB GUI.

## Requirements

- Python 3.11+
- The packages listed in `requirements.txt`

## Installation

```bash
cd src/python/geophone_scope
pip install -r requirements.txt
```

## Running

```bash
python main.py
```

With automatic connection on startup:

```bash
python main.py --port COM3
python main.py --port COM3 --baud 921600
```

Custom log / data directories:

```bash
python main.py --log-dir C:\MyLogs --data-dir C:\MyData
```

## File layout

| File | Purpose |
|------|---------|
| `config.py` | All constants (baud, FS, packet types, …) |
| `protocol.py` | Packet encode/decode |
| `serial_worker.py` | Background QThread for serial I/O |
| `debug_port.py` | Background QThread for slave debug UART |
| `signal_proc.py` | FIR filter, DC removal, LMS notch |
| `data_store.py` | Per-node circular buffers + stats |
| `logger.py` | Dual human/machine log files |
| `gui/main_window.py` | QMainWindow — wires everything together |
| `gui/stream_tab.py` | Connection, ARM, START/STOP, save |
| `gui/slave_tab.py` | Per-slave PGA/VDAC/FIR/test controls |
| `gui/plot_area.py` | pyqtgraph real-time multi-channel plots |
| `main.py` | Entry point |

## Protocol summary

### PC → Master (commands)

| Format | Bytes | Commands |
|--------|-------|---------|
| Standard | 4 | `0xAB cmd param (cmd^param)` |
| Set-N 16-bit | 5 | `0xAB cmd n_lo n_hi (cmd^n_lo^n_hi)` |
| Directed | 6 | `0xAB 0xBD node_id sub_cmd param (node_id^sub_cmd^param)` |

### Master → PC (packets)

All packets: `[0x56][node_id][type][b2][b1][b0]`

| Type | Meaning |
|------|---------|
| `0x00` | ADC sample (signed 24-bit) |
| `0x01` | Heartbeat (pga, vdac, master_state) |
| `0x07` | ACK |
| `0xFC` | START latency (µs, 24-bit unsigned) |
| `0xFD` | Status (master) / HELLO (slave) |
| `0xFE` | READY (n_slaves_ready) |

## Saved data format

Files are MATLAB `.mat` archives. Load with:

```python
from scipy.io import loadmat
d = loadmat("muestra_20240531_143022.mat")
raw_slave1 = d["node1_raw"].ravel()  # float32 array in volts
fs = float(d["fs"].squeeze())        # 1020.0
```

## FIR command examples

The per-slave FIR `Cmd` field accepts the short commands and SciPy-style
coefficient expressions. `FS` and `fs` are available as the acquisition rate.

```python
lp 200
hp 10
bp 10 400
bs 45 55
numtaps 201 lp 150
firls(73, (0, 1, 2, 3, 4, 5), (0, 0, 1, 1, 0, 0), fs=FS)
remez(73, [0, 40, 45, 55, 60, 510], [1, 0, 1], fs=FS)
firwin(101, [45, 55], pass_zero="bandstop", fs=FS)
firwin2(73, [0, 40, 45, 55, 60, 510], [1, 1, 0, 0, 1, 1], fs=FS)
b = [0.25, 0.5, 0.25]
```
