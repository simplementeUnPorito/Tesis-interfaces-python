# RTS real y picking Kalman — Canchita grupo 1

## Datos y metodo

- Se reconstruyeron **21 trazas reales** entre 10 y 50 m, a **1020 Hz**, sobre la misma grilla y agrupacion.
- Todas las capturas son mediciones de **aceleracion** realizadas con la cadena de Ma et al.; el repositorio las almacena en voltios (`signal_units: V`), es decir, como salida electrica de esa cadena.
- Brazo de control: esa aceleracion medida pasa por Butterworth de orden 10 en SOS, `sosfiltfilt`, 0-80 Hz.
- Brazo RTS: planta SM-24 nominal + LP_PGA medido, entrada `leaky_rw`, `q` ajustado por maxima verosimilitud de innovaciones en la traza real de 30 m. En los demas canales se conserva `q/R`.
- Picking: estado `[p, dp/df]`, `p=1/c`, KF hacia frecuencias crecientes + RTS hacia atras y limites de longitud de onda. No se fuerza monotonia porque la imagen contiene cambios de curvatura/rama.
- La curva hidrogeologicamente guiada (132 puntos, 8.0-29.83 Hz) se uso **solo para evaluar**, nunca para inicializar ni ajustar.

## Resultado espectral real (traza de 30 m)

Las PSD estan normalizadas a area unitaria entre 1 y 200 Hz porque la aceleracion medida/SOS esta almacenada como salida en voltios de la cadena Ma, mientras RTS estima su entrada en m/s2; por eso la comparacion defendible es la **distribucion espectral**, no una amplitud absoluta entre unidades distintas.

| salida | 1-10 Hz | 10-50 Hz | 50-80 Hz | 80-200 Hz | centroide Hz |
|---|---:|---:|---:|---:|---:|
| Aceleracion medida (salida Ma, V) | 0.0041 | 0.9913 | 0.0013 | 0.0016 | 23.02 |
| SOS filtfilt | 0.0041 | 0.9929 | 0.0013 | 0.0000 | 22.83 |
| RTS | 0.0035 | 0.9860 | 0.0069 | 0.0029 | 26.38 |


## Picking contra la referencia externa

| brazo | RMSE m/s | MAE m/s | sesgo m/s | cobertura | puntos pick |
|---|---:|---:|---:|---:|---:|
| SOS / auto actual | 138.152 | 121.796 | 121.306 | 100.0% | 7 |
| SOS / Kalman+RTS | 16.245 | 12.902 | 10.775 | 99.2% | 77 |
| RTS / auto actual | 148.208 | 140.014 | 139.963 | 86.4% | 6 |
| RTS / Kalman+RTS | 16.447 | 13.081 | 10.827 | 99.2% | 77 |


## Conclusion

- Sobre la imagen SOS, el tracker Kalman+RTS cambia el RMSE de 138.15 a 16.25 m/s (88.2% de mejora relativa): el salto a un modo de alta velocidad del auto-pick actual desaparece.
- Aplicar la deconvolucion RTS a las trazas y luego el mismo tracker deja RMSE 16.45 m/s (-1.2% respecto del tracker sobre SOS). Esta cifra separa si la mejora viene del preprocesamiento RTS o del tracker.
- El NIS medio de la traza de referencia es 0.557; por tanto el `q` ML es reproducible pero su consistencia estadistica no debe sobreinterpretarse como calibracion metrologica.
- No se reporta RMSE temporal del RTS real: el repositorio no contiene una aceleracion de suelo verdadera sincronizada. La evidencia real valida aplicacion, estabilidad numerica y contenido espectral; la exactitud temporal absoluta queda limitada al benchmark sintetico.
