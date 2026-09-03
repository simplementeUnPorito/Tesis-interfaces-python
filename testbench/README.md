# Banco de pruebas de placas por serie

Prueba una placa del nodo esclavo desde la PC, hablando por USB con el firmware
`slaveTest` del ESP32. No pasa por la página web ni por el maestro: es un cable
y esta herramienta.

Dos modos sobre el mismo núcleo:

- **Automático**, para invocar sin mirar y para que lo use un script o un agente.
  Corre el autotest, aplica los invariantes, deja un JSON de evidencia y las
  figuras, y devuelve un código de salida.
- **Manual**, para experimentos finos: mover los IDAC, mirar cualquier tap del
  AMux, barrer una etapa, y un osciloscopio lento en vivo. Existe en terminal y
  en la interfaz gráfica.

## Intérprete

Usar el Python 3.14 del sistema, **no** el de PlatformIO:

```powershell
$py = "C:\Users\elias\AppData\Local\Python\pythoncore-3.14-64\python.exe"
```

El de PlatformIO (`~/.platformio/penv/Scripts/python.exe`) no tiene PyQt6 y el
modo gráfico no arranca. Dependencias: `pyserial`, `matplotlib`, `numpy` para el
modo terminal; además `PyQt6` y `pyqtgraph` para el gráfico. Están todas
instaladas en ese intérprete.

Se corre desde `src/interfaces/python`, que es donde está el paquete:

```powershell
Set-Location C:\Github\Tesis\src\interfaces\python
& $py -m testbench <subcomando>
```

## Modo automático

```powershell
& $py -m testbench run --json artifacts\corrida.json --figs artifacts\figs
```

Códigos de salida, los mismos que `autotest_runner.py` del repo del firmware:

| Código | Significado |
|---:|---|
| 0 | Placa apta y cobertura completa. |
| 1 | Hay `FAIL`, o la corrida quedó trunca. |
| 2 | Error de puerto o excepción. |
| 3 | Sin `FAIL` pero cobertura parcial a propósito, por PSoC ausente. |

Otros subcomandos automáticos:

```powershell
& $py -m testbench puertos                 # qué COM es el ESP y cuál el KitProg
& $py -m testbench probe                   # enlace con el PSoC y perfil de hardware
& $py -m testbench run --group d           # un solo grupo
& $py -m testbench tap --repeat 8          # D7, ocho intentos
& $py -m testbench hw geo 1                # cambiar el perfil, queda en NVS
& $py -m testbench replay corrida.log      # evaluar una captura, sin placa
& $py -m testbench self-test               # pruebas del parser, sin placa
```

`replay` y `self-test` no tocan hardware: sirven para revisar todo sin la placa
enchufada.

## Modo manual

```powershell
& $py -m testbench taps                    # reposo DC de los cuatro taps
& $py -m testbench dc 3 --settle 3         # una medida de un canal
& $py -m testbench ac 0 --n 2              # media, RMS, pp y 50 Hz
& $py -m testbench idac 3 128 --medir      # mover un IDAC y ver qué pasó
& $py -m testbench gain pgaout 2           # ganancias
& $py -m testbench mon 3 --periodo 200     # osciloscopio lento, Ctrl+C corta
& $py -m testbench sweep 3 --paso 16 --figs figs   # barrido con ajuste
```

`sweep` es la matriz D2 pero con la curva entera en vez de dos puntos: muestra
si la etapa es lineal, dónde tiene zona muerta y dónde se comprime. En la
primera corrida sobre la placa real, la etapa `Vref_LP` mostró zona muerta entre
los códigos 0 y 40 y compresión arriba de 200, con un tramo lineal de
~1375 µV/código entre medio. D2, que mide con dos puntos separados 40 códigos,
promedia todo eso.

## Consola interactiva

```powershell
& $py -m testbench consola
```

Acepta los comandos del banco y, lo que no reconoce, se lo manda al firmware tal
cual. `ayuda` los lista. Muestra el diálogo completo con dirección y hora, así
que se ve tanto lo que se manda como lo que contesta, incluidos los eventos que
el PSoC le manda al ESP cuando está `diag on`.

## Modo gráfico

```powershell
& $py -m testbench          # sin argumentos abre la ventana
& $py -m testbench gui
```

## Trampas que la herramienta ya evita

Están codificadas en `core/session.py` para que ni la terminal ni la interfaz
puedan pedir una secuencia que dé un diagnóstico falso:

1. **Toda captura exige el SYNC armado.** `stCapture()` del firmware devuelve
   false de entrada si `g_syncOk` es falso, y eso sólo se pone en el grupo B.
   Correr `c`, `d` o `tap` sin `b` antes, en el mismo arranque del ESP, da
   `SKIP` en C4/C5 y `no se pudo capturar el fondo` en D7. No es la placa: es el
   orden. Por eso los grupos que capturan arman el SYNC solos.
2. **Abrir el puerto resetea el ESP**, y eso desarma el SYNC. La herramienta
   espera el banner de arranque antes de mandar nada; si no, los primeros
   comandos se pierden.
3. **D7 no se puede sincronizar desde la PC.** El firmware mide el fondo, cuenta
   hasta tres y captura 1,47 s. Por eso `tap --repeat N` dispara N intentos: el
   operador golpea a ritmo parejo y varios caen bien.

Los comandos del modo manual (`dc`, `ac`, `idac`, `mon`, `sweep`) **no**
necesitan el SYNC: miden, no capturan.

## La escala del firmware está mal para esta placa

El firmware deriva la relación código de IDAC → tensión de la portadora JitX,
que no se fabricó: R de conversión de 30 kΩ y una `Vref` de 2,062 V generada por
un AMS1117 que en la placa no existe. La placa construida tiene R11–R14 de
15 kΩ contra `Vref`, y `Vref` es `Vdda/2` = 2,5 V bufferada por `OPAref`. Con los
IDAC8 en 0–31,875 µA (1/8 µA por bit), cada referencia vale `Vref ± R·Idac`, o
sea de 2,0 a 3,0 V, y **el escalón real es 1875 µV por código, la mitad** del que
asume el firmware.

Consecuencias, que la herramienta ya aplica:

- El umbral de D2 del firmware, 200 µV/código, está justificado en su propio
  comentario con esos 3,75 mV/LSB. El equivalente para esta placa es
  **100 µV/código**, y es el que usa `checklist.D2_MIN_SLOPE_UV_PLACA`.
- Lo que el autotest informa en µV es **desviación respecto de `Vref`**, no
  tensión absoluta: la cadena entra al ADC por un amplificador referido a
  `Vdda/2`.
- `sweep` divide la pendiente por 1875 µV para dar una ganancia con sentido
  físico.

Ver `slave/artifacts/registro_pruebas_analogicas_2026-09-02.md`.

## Estructura

```text
testbench/
├── _gs.py             reutiliza config/protocol/serial_worker de geophone_scope
├── cli.py             modo terminal: automático, manual y consola
├── gui.py             modo gráfico
├── __main__.py        sin argumentos abre el gráfico; con subcomando, la terminal
└── core/
    ├── console.py     enlace de texto a 115200 con el slaveTest
    ├── checklist.py   parseo incremental y veredicto
    ├── session.py     secuenciador; acá viven las trampas evitadas
    ├── lab.py         modo manual: IDAC, taps, barridos, monitor
    ├── evidence.py    JSON con el esquema del runner del firmware
    └── figures.py     figuras matplotlib
```

`geophone_scope/` queda intacto: de ahí sólo se toman `config`, `protocol` y
`serial_worker`, que son el protocolo binario del maestro, por si más adelante
se agrega el enlace con el maestro además del esclavo. Se accede por `_gs.py`,
con el mismo criterio que `server/_gs.py`.

## Comandos de laboratorio del firmware

Los agregó esta herramienta a `main_selftest.cpp`. Contestan líneas prefijadas
con `#` y campos separados por espacios, para parsearlas sin adivinar:

| Comando | Respuesta |
|---|---|
| `idac E C` | `#IDAC etapa codigo ok` |
| `dc N [S]` | `#DC ch settle media_uv pp_uv ok` |
| `ac N [S]` | `#AC ch nsel media_uv rms_uv pp_uv hz50_uv ok` |
| `mon N [ms] [n]` | `#MONSTART`, `#MON i t_ms ch media_uv pp_uv ok`…, `#MONEND n` |
| `sweep E lo hi paso [N]` | `#SWEEPSTART`, `#SWEEP etapa code ch media_uv pp_uv ok`…, `#SWEEPEND` |
| `pga C` / `pgaout C` | `#GAIN cual codigo` |

`mon` y `sweep` cortan si llega cualquier byte por el USB.
