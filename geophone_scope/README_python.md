# Geophone Scope — Python/PyQt6

GUI de escritorio (reemplaza `InterfaceESP.m`). Funcionalmente equivalente a la
interfaz MATLAB. Para trabajo de campo se prefiere la UI web del maestro ESP32;
esta app es útil para post-proceso y análisis en PC.

## Requisitos

- Python 3.11+
- Paquetes en `requirements.txt`

## Instalación

```bash
cd src/python/geophone_scope
pip install -r requirements.txt
```

## Ejecutar

```bash
python main.py
python main.py --port COM8
python main.py --port COM8 --baud 921600
python main.py --log-dir C:\Logs --data-dir C:\Data
```

## Convertir ZIP de la web a .mat

```bash
python zip_to_mat.py capture.zip
```

Convierte el ZIP exportado por la UI web del maestro en un `.mat` con las mismas
claves que la app de escritorio (`node1_raw`, `node1_filt`, `fs`, etc.).

## Layout de archivos

| Archivo | Función |
|---------|---------|
| `config.py` | Constantes: baud, tipos de paquete, comandos |
| `protocol.py` | Encode/decode de paquetes |
| `serial_worker.py` | QThread para I/O serie |
| `debug_port.py` | QThread para UART debug del esclavo |
| `signal_proc.py` | FIR (`firFilter`, `filtFilt`), `dcRemove`, notch armónico |
| `data_store.py` | Buffers circulares por nodo + stats |
| `logger.py` | Log dual humano/máquina a archivo |
| `zip_to_mat.py` | Conversor ZIP (web UI) → `.mat` |
| `gui/main_window.py` | QMainWindow — integra todos los componentes |
| `gui/stream_tab.py` | Conexión, ARM, START/STOP, guardar |
| `gui/slave_tab.py` | Controles PGA/VDAC/FIR por esclavo |
| `gui/plot_area.py` | Gráficas tiempo real (pyqtgraph) |
| `main.py` | Entry point |

## Protocolo

### PC → Maestro (comandos)

| Formato | Bytes | Comandos |
|---------|-------|---------|
| Estándar | 4 | `0xAB cmd param (cmd^param)` |
| Set-N 16-bit | 5 | `0xAB cmd n_lo n_hi (cmd^n_lo^n_hi)` |
| Dirigido | 6 | `0xAB 0xBD node_id sub_cmd param (node_id^sub_cmd^param)` |

### Maestro → PC (paquetes de 6 bytes)

`[0x56][node_id][type][b2][b1][b0]`

| type | Significado |
|------|-------------|
| `0x00` | Muestra ADC (int24 signed) |
| `0x01` | Heartbeat (PGA, VDAC, master_state) |
| `0x07` | ACK |
| `0xFC` | Latencia START (µs, 24-bit) |
| `0xFD` | Status / HELLO esclavo |
| `0xFE` | READY (n_slaves_ready) |

**Nota:** `Fs` no tiene constante nominal en `config.py` — siempre viene del
HELLO del esclavo (el PSoC reporta 2929 Hz en el firmware actual). La app
la lee de `PTYPE_STATUS` al arrancar.

## Formato de datos guardados (.mat)

```python
from scipy.io import loadmat
d = loadmat("muestra_20260701_143022.mat")
raw_slave1 = d["node1_raw"].ravel()   # float32 array en voltios
fs = float(d["fs"].squeeze())         # 2929.0 (valor real reportado por el PSoC)
fir_cmd = str(d["node1_fir_cmd"])
```

## Comandos FIR (campo `Cmd` de cada esclavo)

```python
lp 200
hp 10
bp 10 400
bs 45 55
numtaps 201 lp 150
firls(73, (0, 1, 2, 3, 4, 5), (0, 0, 1, 1, 0, 0), fs=FS)
remez(73, [0, 40, 45, 55, 60, 510], [1, 0, 1], fs=FS)
firwin(101, [45, 55], pass_zero="bandstop", fs=FS)
b = [0.25, 0.5, 0.25]
```

`FS` y `fs` están disponibles como la tasa de muestreo real del hardware.
