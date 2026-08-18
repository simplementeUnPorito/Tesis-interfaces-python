# Amedida + Kalman de dispersion + inversion Vs

## Cadena ejecutada

```text
Amedida -> SOS existente -> MASW phase-shift
        -> Kalman + RTS solamente en frecuencia sobre la cresta cR(f)
        -> inversion Rayleigh Vs(z)
```

- No se uso el primer Kalman temporal.
- No se uso RTS temporal.
- La inversion recibe `cR`, no `cR/0.92`.
- La referencia externa no participo en tracking ni inversion; se uso despues para evaluar.

## Curva observada

- 25 puntos usados, 8.00-21.71 Hz.
- cR = 86.18-86.86 m/s.
- Longitudes de onda = 4.00-10.84 m.
- Profundidad aproximadamente resoluble: hasta 5.42 m.
- Contra referencia externa: RMSE 8.72 m/s, MAE 6.47 m/s, sesgo 2.99 m/s.

## Inversion recomendada: modelo monotono

| Capa | Profundidad | Vs [m/s] |
|---:|---:|---:|
| 1 | 0.00-0.71 m | 91.01 |
| 2 | 0.71-1.63 m | 91.38 |
| 3 | 1.63-3.56 m | 92.18 |
| 4 | 3.56-5.83 m | 93.19 |
| 5 | > 5.83 m | 95.31 |

- Misfit porcentual medio: **0.393 %**.
- RMSE de la curva teorica contra el pick Kalman: **0.463 m/s**.
- Mejor modelo con hasta dos inversiones: misfit **0.185 %**, RMSE **0.186 m/s**.
- Curva teorica recomendada contra referencia externa: RMSE **8.756 m/s**.

## Lectura

La banda se corto en 22 Hz porque, con dx=2 m, el limite espacial `cR >= 2*dx*f` empieza a obligar al tracker a velocidades mayores que el modo lento. Dentro de 8-22 Hz el modelo monotono ya ajusta por debajo de 1 m/s RMSE. Permitir inversiones reduce algo mas el error, pero agrega capas oscilatorias sensibles a la semilla; no se justifica esa complejidad con esta banda. La profundidad aproximadamente resoluble es 5.4 m. No interpretar las interfaces ni el semiespacio como un perfil geologico definitivo sin incertidumbre y comparacion con otros grupos/campanas.

## Artefactos

- `01_masw_acceleration_kalman_curve.png`
- `02_inversion_vs_profile.png`
- `dispersion_curve_kalman_acceleration.csv`
- `vs_profile_recommended_monotonic.csv`
- `vs_profile_two_reversal.csv`
- `metrics.json`
