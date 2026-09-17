# Scripts de laboratorio de la cadena analógica GEO

Los ~75 `.py` y 13 `.sh` sueltos en la raíz de este repositorio son el banco de
trabajo de la autocalibración (2026-09-04 → 2026-09-15). Hablan con la placa a
través del ESP32 `slaveTest` usando el paquete `testbench/` y trabajan en mV
**físicos del tap** vía `escala_banco.py`.

**Estado al 2026-09-16:** la calibración operativa (aprendizaje lento de
IDAC0/IDAC1 y PI permanente de IDAC2/IDAC3) ya corre **dentro del PSoC**
(`src/firmware/psoc/AcondicionamientoAnalogico.cydsn/CONTROL_UNIFICADO.md`) y se
maneja con comandos `ctl` desde el esclavo normal. Estos scripts quedan como
instrumentos de medición y como historia del método; la bitácora de resultados
está en `lab/README.md` del superproyecto. Ojo: el firmware de control usa otra
escala (`ctl_dc`, ~x20 más chica) y sus pendientes no son intercambiables con las
de acá.

## Bibliotecas compartidas (importadas por otros)

| módulo | rol |
|---|---|
| `testbench/` | enlace serie con `slaveTest`, secuenciador, modo manual (`idac`, `dc`, `sweep`), evidencia y figuras. Ver `testbench/README.md`. |
| `escala_banco.py` | **único lugar** de conversión cuentas del banco ↔ voltios reales y ventanas de validez. |
| `regulador_pi.py` | regulador PI en cascada con vernier y descargue neutro (α = T/τ). Tests: `test_regulador_pi.py`. |
| `cerrar_lazo.py` | conecta el regulador a la placa; lecturas y escrituras de IDAC reutilizadas por muchos scripts. |
| `asentamiento.py` | esperar a que la cadena deje de moverse en vez de un tiempo fijo. |
| `nonio.py` | combinación de dos actuadores gruesos cuya diferencia da resolución fina. Tests: `test_nonio.py`. |
| `perfil_placa.py` | lo que la placa sabe de sí misma (vigencia, escalado, persistencia). Tests: `test_perfil_placa.py`; datos en `datos/perfiles/`. |

## Calibrar y sostener (flujo final en Python, 13–15/09)

| script | qué hace |
|---|---|
| `calibrar_y_sostener.py` | ciclo completo en la ganancia nominal: fase lenta IDAC0/IDAC1 + PI rápido. Antecesor directo del firmware. |
| `sostener_directo.py` | aplica un punto conocido en la ganancia final y arranca el PI ya (`--punto`, `--periodo`, `--paso-rescate`). |
| `sostener.py` | mantiene el punto con el PI y mide si aguanta. |
| `saltar_ganancia.py` | salta a la ganancia final sin dejar a LPo en riel (mide el punto fijo de PGAout). |
| `subir_por_consigna.py`, `subir_ganancia.py` | suben PGAout preparando la entrada / sin soltar la cadena. |
| `calibrar.py` | adelanto a lazo abierto + lazo cerrado sobre LPo. |
| `cerrar_lazo.py` | ver bibliotecas. |
| `centrar_lpo.py` | centrado por bisección de signo etapa por etapa. Tests: `test_centrar_lpo.py`. |
| `registrar_punto.py` | guarda en el perfil de placa lo que dejó una corrida. |

## Autocalibración a lazo abierto (08–11/09, superada)

| script | qué hace |
|---|---|
| `autocalibracion_dos_fases.py` | autocalibración en dos fases guiada por margen y ganancia (3900 líneas). Tests: `test_autocalibracion_dos_fases.py`. |
| `autocalibrar_seguro.py` | versión adaptativa sin semillas. |
| `endurance_arranque_frio.py` | campaña desatendida de arranques en frío. |
| `analizar_endurance_dos_fases.py`, `repuntuar_endurance.py` | estadística y re-puntuación de campañas. |
| `calibrar_pc.py`, `calibrar_permisivo.py`, `calibracion_conservadora.py`, `calibrar_topologia_compartida.py` | variantes del 05–08/09 (conjunta desde PC, permisiva, conservadora, referencia BP/SUM compartida). |
| `registrar_calibracion.py` | graba la evolución de una calibración, no solo el veredicto. |

## Medir la planta

| script | pregunta que contesta |
|---|---|
| `medir_planta.py` → `analizar_planta.py` | caracterización general de la planta y sus tablas/figuras. |
| `medir_acople.py`, `medir_matriz_vs_ganancia.py` | matriz de acople 4 IDAC × 5 taps, y su dependencia de las ganancias. |
| `medir_escalado_pgaout.py`, `alcance_por_ganancia.py` | cómo escalan autoridad y resolución con PGAout; hasta qué ganancia se puede centrar. |
| `medir_ganancia_regimen_idac2.py` | pendiente asentada de IDAC2 (no la instantánea). |
| `medir_conflicto_sum_lp.py` | el número que decide si una ganancia es calibrable. |
| `medir_ruido.py` | alterna en la cadena según PGA. |
| `medir_signo_idac3.py`, `verificar_autoridad_idac3.py`, `probar_idac3_sobre_lpo.py`, `diagnostico_idac3.py`, `diagnosticar_idac3.py`, `diagnosticar_idac3_rango_actual.py` | ¿IDAC3 mueve LPo?, con qué signo y cuánto (varias generaciones; la vigente es `verificar_autoridad_idac3.py`, con LPo en rango). |
| `diagnosticar_idac2.py`, `diagnosticar_autoridad_actual.py`, `barrer_sumo.py` | IDAC2 sobre OPA_SUM/SUM/LP. |
| `mapear_alcance_sumador.py` → `analizar_mapa_sumador.py`, `mapear_umbral_idac1.py`, `buscar_salto_idac1.py` | mapa IDAC1/IDAC2 del sumador y la discontinuidad de IDAC1. |
| `probar_carga_pgaout.py` | ¿PGAout carga al sumador? |
| `barrer_pgaout.py`, `buscar_max_pgaout.py`, `mapa_estabilidad.py` | hasta dónde sube PGAout y dónde deja de ser estable. |
| `caracterizar_respuesta_local.py`, `caracterizar_pgaout_seguro.py`, `caracterizar_en_nodo.py` | escalones seguros; disparo de la caracterización que corre en el nodo. |

## Deriva, asentamiento y verificaciones de medida

| script | qué hace |
|---|---|
| `exp_deriva.py` → `analizar_deriva.py` (+ `descargar_temperatura_deriva.py`) | EXP4c/4d: cuánto se mueve solo el punto calibrado, ruido, temperatura exterior. |
| `ajustar_deriva_lenta.py`, `comparar_deriva_por_codigo.py`, `vigilar_punto.py`, `verificar_deriva_ch0.py` | forma de la deriva lenta, dependencia con el código, vigilancia sin tocar. |
| `verificar_origen.py` | origen de coordenadas de las medidas de continua (bloqueante). |
| `verificar_asentamiento_amux.py` | si el multiplexor no se asienta, todas las lecturas mienten. |
| `validar_estimador_tau.py`, `validar_extrapolacion.py` | estimador de τ de dos puntos y extrapolación del valor asentado. |
| `test_nombres_definidos.py` | que ningún script llame a nombres inexistentes. |

## Figuras

`generar_figuras.py` (05/09), `figura_sin_calibrar.py` (06/09),
`figuras_semana.py` (07/09), `analizar_campana.py` (81 combinaciones PGA×PGAout).

## Escaleras de ganancia (`.sh`, Git Bash)

`escalera_pgaout.sh`, `escalera_pga1.sh`, `escalera_ganancias.sh`,
`escalera_lazo.sh`, `escalera_centrado.sh`, `escalera_con_pi.sh`,
`cerrar_y_subir.sh`, `probar_pgaout_alto.sh`, `probar_pgaout_16_24.sh`,
`probar_mayores_a_50.sh`, `probar_repartos.sh`, `probar_mas_alto.sh`,
`probar_techo_pgaout24.sh`: encadenan los scripts de arriba sobre una escalera
de ganancias y dejan un veredicto por escalón. El resultado consolidado está en
`lab/RESULTADO_GANANCIA_2026-09-14.md` (máximo sostenido: PGA x4 · PGAout x24).

## Otras carpetas

- `lunes/`: batería del 07/09 (cambio de resistencias): `t0_linea_base`,
  `t1_autoridad`, `t2_sin_calibrar`, `t3_cal_firmware`, `t4_encadenado`,
  orquestadas por `correr_lunes.py`. Datos en `lab/lunes/`.
- `datos/perfiles/`: perfiles de placa de `perfil_placa.py`.
- `scripts/`: configuración e hidratación de Git LFS (Geopsy).

## Pruebas sin placa

```powershell
py -3 -m unittest test_regulador_pi test_nonio test_perfil_placa test_centrar_lpo test_autocalibracion_dos_fases test_nombres_definidos test_sostener_directo
```

Son `unittest` (pytest no está instalado). Verificado el 2026-09-16: los siete
pasan (`test_autocalibracion_dos_fases` con 3 omitidas). Usar el Python 3.14 del
sistema, no el de PlatformIO.
