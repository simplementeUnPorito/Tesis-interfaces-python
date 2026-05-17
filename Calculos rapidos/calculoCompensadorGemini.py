import numpy as np
import matplotlib.pyplot as plt
from scipy import signal

# ==============================================================================
# 1. PARÁMETROS DEL FILTRO OBJETIVO (IDEAL)
# ==============================================================================
f0 = 10.0                          # Frecuencia natural (Hz)
w0 = 2 * np.pi * f0                # Frecuencia angular (rad/s)
gamma1 = 1000.0                     # Damping objetivo (ζ)
gamma0 = 0.25                      # Parámetro de ganancia del numerador

# Numerador y denominador ideal de la imagen:
# H_ideal = -2*(gamma1*w0 - gamma0*w0)*s / (s^2 + 2*gamma1*w0*s + w0^2)
num_ideal_coef = -2 * (gamma1 * w0 - gamma0 * w0)
num_ideal = [num_ideal_coef, 0]    # [coef_s, coef_cero]
den_ideal = [1, 2 * gamma1 * w0, w0**2]

sys_ideal = signal.TransferFunction(num_ideal, den_ideal)

# ==============================================================================
# 2. COMPONENTES REALES DEL CIRCUITO (PROPUESTOS)
# ==============================================================================
# Valores comerciales seleccionados dentro de los rangos seguros del Op-Amp
R1 = 680e3      # 680 kOhm (> 400 Ohm y < 1.75 MOhm)
R2 = 680e3      # 680 kOhm
C1 = 4.7e-6     # 4.7 uF
C2 = 120e-12    # 120 pF

# Fórmulas físicas del circuito real:
# H_real = -s * R2 * C1 / ((s*R1*C1 + 1) * (s*R2*C2 + 1))
# Expandido: -s * R2 * C1 / (R1*C1*R2*C2*s^2 + (R1*C1 + R2*C2)*s + 1)
num_real = [-R2 * C1, 0]
den_real = [R1 * C1 * R2 * C2, (R1 * C1) + (R2 * C2), 1]

sys_real = signal.TransferFunction(num_real, den_real)

# Calcular f0 y ζ reales para mostrar en consola
w0_real = 1 / np.sqrt(R1 * C1 * R2 * C2)
f0_real = w0_real / (2 * np.pi)
zeta_real = (R1 * C1 + R2 * C2) / (2 * np.sqrt(R1 * C1 * R2 * C2))

print("=" * 60)
print(" COMPARATIVA DE PARÁMETROS")
print("=" * 60)
print(f"Frecuencia f0: Ideal = {f0} Hz | Real = {f0_real:.3f} Hz (Error: {abs(f0-f0_real)/f0*100:.2f}%)")
print(f"Damping   ζ: Ideal = {gamma1}  | Real = {zeta_real:.3f} (Error: {abs(gamma1-zeta_real)/gamma1*100:.2f}%)")
print("=" * 60)

# ==============================================================================
# 3. EVALUACIÓN EN FRECUENCIA (DIAGRAMA DE BODE)
# ==============================================================================
# Generamos un vector de frecuencias desde 0.001 Hz hasta 100 kHz
w = np.logspace(-3, 6, 2000)

w_ideal, mag_ideal, phase_ideal = signal.bode(sys_ideal, w=w)
w_real, mag_real, phase_real = signal.bode(sys_real, w=w)

# Convertir rad/s a Hz para el gráfico
f_hz = w / (2 * np.pi)

# ==============================================================================
# 4. GRÁFICOS
# ==============================================================================
plt.figure(figsize=(10, 8))

# Magnitud
plt.subplot(2, 1, 1)
plt.semilogx(f_hz, mag_ideal, 'r--', linewidth=2, label='TF Ideal (Imagen)')
plt.semilogx(f_hz, mag_real, 'b-', linewidth=1.5, label='TF Circuito Real')
plt.title('Comparativa de Respuesta en Frecuencia (Bode)', fontsize=14)
plt.ylabel('Magnitud (dB)', fontsize=12)
plt.grid(True, which="both", ls="-", color='0.85')
plt.legend(fontsize=11)

# Fase
plt.subplot(2, 1, 2)
plt.semilogx(f_hz, phase_ideal, 'r--', linewidth=2, label='TF Ideal (Imagen)')
plt.semilogx(f_hz, phase_real, 'b-', linewidth=1.5, label='TF Circuito Real')
plt.xlabel('Frecuencia (Hz)', fontsize=12)
plt.ylabel('Fase (grados)', fontsize=12)
plt.grid(True, which="both", ls="-", color='0.85')

plt.tight_layout()
plt.show()