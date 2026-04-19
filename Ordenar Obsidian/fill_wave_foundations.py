#!/usr/bin/env python3
from pathlib import Path
import shutil

ROOT = Path("/home/elias-alvarez/GitHub/Tesis").resolve()
CONCEPTS = ROOT / "Obsidian Vault" / "Notes" / "Surface Wave Methods" / "Concepts"

FILES = {
    "linear_waves": CONCEPTS / "00 Foundations" / "Linear Waves.md",
    "wave_eq_1d": CONCEPTS / "02 Wave Mathematics" / "1D Wave Equation.md",
    "dalembert": CONCEPTS / "02 Wave Mathematics" / "d’Alembert Solution.md",
    "superposition": CONCEPTS / "02 Wave Mathematics" / "Wave Superposition.md",
}

def backup_if_needed(path: Path):
    if path.exists():
        txt = path.read_text(encoding="utf-8")
        if txt.strip():
            bak = path.with_suffix(path.suffix + ".bak")
            if not bak.exists():
                shutil.copy2(path, bak)
                print(f"[BACKUP] {bak}")

def write_if_empty(path: Path, content: str):
    if path.exists():
        txt = path.read_text(encoding="utf-8")
        if txt.strip():
            print(f"[SKIPPED NOT EMPTY] {path.relative_to(ROOT)}")
            return
    else:
        path.parent.mkdir(parents=True, exist_ok=True)

    backup_if_needed(path)
    path.write_text(content.strip() + "\n", encoding="utf-8")
    print(f"[WROTE] {path.relative_to(ROOT)}")

linear_waves_text = r"""
# Linear Waves

## 1. Concepto

Las **ondas lineales** son ondas cuya propagación puede describirse mediante ecuaciones lineales.

Esto implica que la respuesta del sistema preserva el principio de superposición: si dos soluciones son válidas por separado, su suma también lo es.

---

## 2. Fundamento físico

La linealidad aparece cuando las perturbaciones son suficientemente pequeñas como para despreciar términos no lineales.

En ese régimen:

- la amplitud no modifica la velocidad de propagación
- las ondas no se deforman por auto-interacción
- es válido descomponer la señal en componentes elementales

Esto no significa que toda onda lineal sea no dispersiva. Una onda puede ser lineal y aun así ser dispersiva, dependiendo de la relación de dispersión del medio.

---

## 3. Formulación matemática

Si $\phi_1(x,t)$ y $\phi_2(x,t)$ son soluciones de una ecuación lineal de ondas, entonces:

$$
a\,\phi_1(x,t) + b\,\phi_2(x,t)
$$

también es solución, para constantes $a$ y $b$.

La ecuación clásica de onda 1D es un ejemplo de ecuación lineal:

$$
\frac{\partial^2 \phi}{\partial x^2}
=
\frac{1}{c_0^2}
\frac{\partial^2 \phi}{\partial t^2}
$$

---

## 4. Aplicación a geófonos

En geofísica near-surface, gran parte de la teoría usada para interpretar registros de geófonos se construye bajo hipótesis de propagación lineal.

Esto permite:

- usar análisis espectral
- separar modos
- formular problemas directo e inverso
- describir la señal como suma de contribuciones de distintas ondas

---

## 5. Implicaciones para el diseño experimental

- La linealidad es una hipótesis de trabajo, no una verdad universal.
- Si la fuente induce deformaciones grandes, la aproximación lineal puede degradarse.
- La mayor parte de la instrumentación y procesamiento estándar asume régimen lineal.
- Conviene mantener fuentes y acoplamientos dentro de un rango reproducible y estable.

---

## 6. Fuente

- PDF: Sebastiano Foti Chapter 2
- capítulo o sección: 2.1.1 Two categories of wave motion
- página: 38–39
"""

wave_eq_1d_text = r"""
# 1D Wave Equation

## 1. Concepto

La **ecuación de onda unidimensional** es el modelo clásico más simple de propagación ondulatoria lineal.

Describe cómo una perturbación evoluciona en una sola dimensión espacial cuando se propaga con velocidad constante.

---

## 2. Fundamento físico

Este modelo representa el caso paradigmático de las **ondas hiperbólicas lineales**.

Su solución muestra que la perturbación puede propagarse sin distorsión, siempre que el medio sea ideal y la velocidad de propagación sea constante para todas las frecuencias.

---

## 3. Formulación matemática

La ecuación es:

$$
\frac{\partial^2 \phi}{\partial x^2}
=
\frac{1}{c_0^2}
\frac{\partial^2 \phi}{\partial t^2}
$$

donde:

- $\phi(x,t)$ es la perturbación
- $x$ es la coordenada espacial
- $t$ es el tiempo
- $c_0$ es la velocidad de propagación

Su solución general está dada por la [[d’Alembert Solution]].

---

## 4. Aplicación a geófonos

Aunque los registros sísmicos reales no son estrictamente unidimensionales, esta ecuación sirve como punto de partida conceptual para entender:

- propagación sin dispersión
- velocidad constante
- relación entre forma de onda y medio
- diferencia entre ondas hiperbólicas y dispersivas

---

## 5. Implicaciones para el diseño experimental

- Es un modelo base útil para interpretar fenómenos simples.
- No representa toda la complejidad de ondas superficiales en medios estratificados.
- Sirve como referencia para entender qué cambia cuando aparece dispersión.
- Es valiosa para construir intuición física antes de pasar a Rayleigh y Love waves.

---

## 6. Fuente

- PDF: Sebastiano Foti Chapter 2
- capítulo o sección: 2.1.1 Two categories of wave motion
- página: 38–39
"""

dalembert_text = r"""
# d’Alembert Solution

## 1. Concepto

La **solución de d’Alembert** es la solución general de la ecuación clásica de onda unidimensional.

Expresa la perturbación como la suma de dos ondas que viajan en sentidos opuestos con la misma velocidad.

---

## 2. Fundamento físico

La interpretación física es directa:

- una componente se propaga hacia la derecha
- otra componente se propaga hacia la izquierda

Ambas conservan su forma durante la propagación. Esto refleja el carácter no dispersivo de la ecuación de onda 1D en este caso ideal.

---

## 3. Formulación matemática

La solución general es:

$$
\phi(x,t)=f(x-c_0 t)+g(x+c_0 t)
$$

donde:

- $f(\cdot)$ representa una onda que viaja hacia la derecha
- $g(\cdot)$ representa una onda que viaja hacia la izquierda
- $c_0$ es la velocidad de propagación

Esta expresión muestra que la solución es una [[Wave Superposition]] de dos ondas viajeras.

---

## 4. Aplicación a geófonos

En adquisición sísmica real, el campo registrado puede contener contribuciones que llegan desde distintas direcciones y con distintos tipos de onda.

La solución de d’Alembert ayuda a entender, en el caso más simple, que una señal observada puede representarse como suma de contribuciones propagativas.

---

## 5. Implicaciones para el diseño experimental

- Sirve para construir intuición sobre propagación y retardo temporal.
- Es útil como modelo base antes de estudiar superposición modal y ondas superficiales dispersivas.
- No debe extrapolarse sin cuidado a medios estratificados reales.
- Es un caso idealizado, pero muy útil pedagógicamente.

---

## 6. Fuente

- PDF: Sebastiano Foti Chapter 2
- capítulo o sección: 2.1.1 Two categories of wave motion
- página: 38–39
"""

superposition_text = r"""
# Wave Superposition

## 1. Concepto

La **superposición de ondas** es el principio según el cual la respuesta total de un sistema lineal puede obtenerse sumando las contribuciones de varias ondas.

Es una propiedad central de la propagación lineal.

---

## 2. Fundamento físico

Cuando el medio está gobernado por ecuaciones lineales, las ondas no se destruyen ni se modifican mutuamente de forma no lineal al coexistir.

Por ello, es posible describir una señal compleja como la suma de componentes más simples.

En la solución de d’Alembert, por ejemplo, la perturbación total se expresa como superposición de dos ondas que viajan en sentidos opuestos.

---

## 3. Formulación matemática

Si $\phi_1(x,t)$ y $\phi_2(x,t)$ son soluciones, entonces:

$$
\phi(x,t)=\phi_1(x,t)+\phi_2(x,t)
$$

también es solución en un sistema lineal.

En particular, para la ecuación de onda 1D:

$$
\phi(x,t)=f(x-c_0 t)+g(x+c_0 t)
$$

representa la superposición de dos ondas viajeras.

---

## 4. Aplicación a geófonos

Los registros de geófonos suelen contener superposición de:

- ondas directas
- ondas reflejadas
- body waves
- surface waves
- distintos modos de propagación

Entender la superposición es esencial para interpretar interferencia, separación modal y análisis espectral.

---

## 5. Implicaciones para el diseño experimental

- Muchas dificultades de interpretación nacen de señales superpuestas.
- El procesamiento busca separar o explotar esa superposición.
- En ondas superficiales, la superposición modal puede alterar la velocidad aparente observada.
- La linealidad del sistema es la base que permite usar herramientas de Fourier y análisis multicomponente.

---

## 6. Fuente

- PDF: Sebastiano Foti Chapter 2
- capítulo o sección: 2.1.1 Two categories of wave motion
- página: 38–40

- PDF: Sebastiano Foti Chapter 1
- capítulo o sección: 1.5.1 Higher modes
- página: 22–23
"""

def main():
    write_if_empty(FILES["linear_waves"], linear_waves_text)
    write_if_empty(FILES["wave_eq_1d"], wave_eq_1d_text)
    write_if_empty(FILES["dalembert"], dalembert_text)
    write_if_empty(FILES["superposition"], superposition_text)
    print("[DONE] Empty foundation notes filled.")

if __name__ == "__main__":
    main()
