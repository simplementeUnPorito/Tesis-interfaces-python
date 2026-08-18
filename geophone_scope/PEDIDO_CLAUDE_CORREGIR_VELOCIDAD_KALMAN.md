# Pedido para Claude: corregir el primer Kalman de velocidad y rehacer MASW

## Objetivo

Corregir físicamente y verificar de extremo a extremo el primer Kalman temporal
que debe estimar **velocidad de partícula del suelo** `v_ground(t, x)` desde las
mediciones de aceleración de la campaña. Luego usar esa salida en la cadena:

```text
Amedida
  -> SOS previo
  -> Kalman 1 temporal causal: v_ground(t, x)
  -> SOS posterior
  -> MASW phase-shift
  -> Vs_app(f) = cR(f) / 0.92
  -> Kalman 2: solamente ventana/máscara válida en P(f, Vs)
```

No usar el suavizador RTS temporal en esta corrida. No producir todavía una
curva nueva de picking. La curva hidrogeológica externa debe mostrarse encima
de las máscaras únicamente como referencia visual; no puede entrar al ajuste,
a la inicialización ni al gating de ninguno de los dos Kalman.

## Lectura obligatoria antes de modificar código

Leer completos:

1. `src/interfaces/python/geophone_scope/HANDOFF_KALMAN.md`
2. `src/interfaces/python/geophone_scope/TAREAS_KALMAN.md`
3. `src/interfaces/python/geophone_scope/REFERENCIAS_KALMAN.md`
4. `src/interfaces/python/geophone_scope/kalman_deconv/models.py`
5. `src/interfaces/python/geophone_scope/kalman_deconv/plant.py`
6. `src/interfaces/python/geophone_scope/kalman_deconv/discretize.py`
7. `src/interfaces/python/geophone_scope/kalman_deconv/kf.py`
8. `src/interfaces/python/geophone_scope/kalman_deconv/report_vs_apparent_masks.py`

Hay cambios locales no commiteados. Preservarlos y no revertir trabajo ajeno.

## Diagnóstico ya reproducido

El error está en la conversión de magnitud de entrada de la planta.

La forma nativa del geófono para entrada de aceleración es:

```text
H_a(s) = -G*s / (s^2 + 2*zeta*w0*s + w0^2)
```

Si la entrada que se desea estimar es velocidad de partícula `v_ground`, como
`a_ground = s*v_ground`, la planta directa correcta es:

```text
H_v(s) = s * H_a(s)
       = -G*s^2 / (s^2 + 2*zeta*w0*s + w0^2)
```

Para desplazamiento debe cumplirse:

```text
H_d(s) = s^2 * H_a(s)
```

Sin embargo, `plant.py::_integrator_chain()` agrega polos en `s=0`, por lo que
actualmente construye `H_a/s` y `H_a/s^2`. Al invertir ese modelo, el estimador
realza frecuencias altas en vez de recuperar velocidad.

La corrida real defectuosa produjo, como mediana sobre 21 canales:

| Métrica | Amedida + SOS | Kalman "velocity" + SOS |
|---|---:|---:|
| Energía normalizada 1-10 Hz | 0.005357 | 0.0000545 |
| Energía normalizada 10-50 Hz | 0.978998 | 0.955090 |
| Energía normalizada 50-80 Hz | 0.001296 | 0.043688 |
| Centroide 1-200 Hz | 21.902 Hz | 29.716 Hz |
| SNR evento/prearribo 10-50 Hz | 28.226 dB | 31.464 dB |
| Energía MASW mediana sobre referencia | 0.419671 | 0.416268 |
| Contraste sobre referencia | 6.764 dB | 6.951 dB |

Las imágenes MASW tenían correlación 0.9755 en 8-30 Hz: no hubo una mejora
material. La máscara conjunta pasó de aproximadamente 2-24 Hz a 7.14-24 Hz.
Estos números son el baseline defectuoso que debe quedar en el informe final.

## Trabajo solicitado

### 1. Corregir la planta

- Reemplazar la lógica de `_integrator_chain()` por una conversión de magnitud
  físicamente correcta que agregue ceros en el origen:
  - `acceleration`: factor `1`
  - `velocity`: factor `s`
  - `displacement`: factor `s^2`
- Renombrar funciones, variables, comentarios y documentación para que no
  vuelva a afirmarse que estimar velocidad agrega un integrador a la planta.
- Revisar toda referencia textual a `PlantSpec.estimate` en el módulo y en los
  tres documentos de continuidad.
- Confirmar que las plantas resultantes siguen siendo propias para los
  acondicionadores usados. Si alguna combinación es impropia, fallar de forma
  explícita y documentar cuál.

### 2. Agregar regresiones físicas

Crear pruebas automáticas que como mínimo verifiquen, sobre una grilla
logarítmica de frecuencias y para los modelos usados en la campaña:

```text
H_velocity(jw) / H_acceleration(jw) = jw
H_displacement(jw) / H_acceleration(jw) = (jw)^2
```

Verificar también:

- número de ceros y polos;
- grado relativo esperado;
- respuesta de magnitud y fase con tolerancias numéricas explícitas;
- ausencia de NaN/Inf;
- que el cambio no rompa los 13 checks de S1 ni las pruebas del tracker MASW.

No marcar ninguna tarea como completada sin pegar la salida numérica del
comando correspondiente, según la regla de `TAREAS_KALMAN.md`.

### 3. Validar el primer Kalman

- Usar únicamente `kf_forward`; no llamar `rts_backward` en esta rama.
- Estimar `Q/R` de forma reproducible y reportar el método, límites de búsqueda,
  resultado del optimizador y sensibilidad al punto inicial/rango.
- Validar primero con un caso sintético donde la verdad de `v_ground` sea
  conocida. Incluir al menos RMSE alineado, razón de amplitud, error de fase y
  contenido espectral por bandas.
- Luego ejecutar las 21 trazas reales de Canchita grupo 1.
- Reportar `min eig(P)`, NIS por canal y agregado, finitud y cualquier problema
  de observabilidad o deriva de baja frecuencia.
- No afirmar amplitud metrológicamente calibrada en los datos reales: no existe
  una verdad sincronizada de velocidad de partícula en el repositorio.

### 4. Comparación espectral antes/después

Comparar al menos estos brazos:

1. `Amedida + SOS previo`
2. `v_ground Kalman 1`
3. `v_ground Kalman 1 + SOS posterior`

Para cada brazo, por canal y agregado robusto sobre los 21 canales, informar:

- fracción PSD normalizada en 1-10, 10-50, 50-80 y 80-200 Hz;
- centroide espectral 1-200 Hz;
- SNR evento/prearribo por banda;
- coherencia o preservación de fase entre canales;
- efecto del segundo SOS.

La transformación de aceleración a velocidad debería desplazar peso relativo
hacia frecuencias menores. Si vuelve a realzar 50-80 Hz, tratarlo como falla y
no como mejora.

### 5. Rehacer MASW y las máscaras

- El MASW debe recibir `v_ground Kalman 1 + SOS posterior` para los 21 offsets.
- Mantener la paleta solicitada: baja energía azul, alta energía rojo oscuro.
- Mostrar cuatro vistas: energía completa, máscara física, ventana Kalman 2 e
  intersección física + energía + Kalman 2.
- Superponer en las cuatro vistas la curva externa convertida a
  `Vs_ref = cR_ref/0.92`, con alto contraste y leyenda inequívoca.
- La referencia es solo overlay. Agregar una aserción o prueba que confirme que
  cambiar/retirar la referencia no modifica Q/R, la imagen MASW ni las máscaras.
- No mostrar ni exportar un picking nuevo; sí puede exportarse la envolvente
  inferior/superior de la región válida.
- Comparar contra `Amedida + SOS` usando:
  - correlación de imágenes MASW;
  - energía y percentil sobre la referencia;
  - contraste referencia/fondo;
  - error del máximo local dentro de +/-20 m/s de la referencia;
  - rango de frecuencias con máscara conjunta no vacía.

### 6. Regenerar y verificar el PDF

Regenerar:

```text
C:/Github/Tesis/output/pdf/MASW_VS_APARENTE_MASCARAS_KALMAN.pdf
```

El PDF debe explicar claramente:

- diferencia entre `v_ground(t,x)` y `Vs_app(f)`;
- los dos Kalman y sus funciones distintas;
- que no se usa RTS temporal;
- que no hay picking nuevo;
- que la referencia es externa y no participa en el cálculo;
- limitaciones estadísticas y metrológicas reales.

Renderizar todas las páginas a PNG, inspeccionarlas visualmente y no entregar
el PDF con texto cortado, solapamientos o gráficos ilegibles.

## Lugar obligatorio para documentar todo

El informe técnico completo y autocontenido debe escribirse exactamente en:

```text
C:/Github/Tesis/src/interfaces/python/geophone_scope/kalman_deconv/reports/velocity_model_fix_2026-08-17/INFORME_CORRECCION_VELOCIDAD_KALMAN.md
```

Ese archivo será la **fuente canónica** que Codex leerá en futuras sesiones.
Debe incluir:

1. resumen ejecutivo y conclusión;
2. causa raíz con ecuaciones;
3. archivos y líneas modificadas;
4. diseño del primer Kalman y unidades de cada variable;
5. comandos exactos ejecutados;
6. salida completa o suficiente de todas las pruebas;
7. tablas antes/después con los 21 canales;
8. métricas sintéticas y reales;
9. rutas de todos los artefactos generados;
10. limitaciones, fallas abiertas y decisiones pendientes;
11. estado del working tree y aclaración de que no se hizo commit, salvo orden
    explícita del usuario.

Además, al final de `HANDOFF_KALMAN.md`, agregar solamente un resumen corto y
un enlace relativo hacia ese informe canónico. No duplicar allí todo el
contenido.

## Criterio de aceptación

No declarar terminado hasta que se cumpla todo lo siguiente:

- las identidades `H_v/H_a=jw` y `H_d/H_a=(jw)^2` pasan numéricamente;
- todas las regresiones previas relevantes siguen verdes;
- el caso sintético demuestra recuperación de velocidad con métricas explícitas;
- la corrida real de 21 canales es finita y numéricamente estable;
- existe una comparación espectral y MASW antes/después honesta;
- el PDF fue regenerado e inspeccionado página por página;
- la curva externa aparece sobre todas las máscaras pero no altera resultados;
- el informe canónico existe en la ruta obligatoria y permite reproducir todo
  sin consultar esta conversación.

