#!/usr/bin/env python3
from pathlib import Path
import shutil
import re

ROOT = Path("/home/elias-alvarez/GitHub/Tesis").resolve()
CONCEPTS = ROOT / "Obsidian Vault" / "Notes" / "Surface Wave Methods" / "Concepts"

FILES = {
    "wave_dispersion": CONCEPTS / "03 Wave Propagation" / "Wave Dispersion.md",
    "dispersive_waves": CONCEPTS / "03 Wave Propagation" / "Dispersive Waves.md",
    "dispersion_relation": CONCEPTS / "03 Wave Propagation" / "Dispersion Relation.md",
    "material_dispersion": CONCEPTS / "05 Dispersion" / "Material Dispersion.md",
}

VAULT = ROOT / "Obsidian Vault"

def backup(path: Path):
    if path.exists():
        bak = path.with_suffix(path.suffix + ".bak")
        if not bak.exists():
            shutil.copy2(path, bak)
            print(f"[BACKUP] {bak}")

def write(path: Path, content: str):
    backup(path)
    path.write_text(content.strip() + "\n", encoding="utf-8")
    print(f"[WROTE] {path.relative_to(ROOT)}")

wave_dispersion_text = r"""
# Wave Dispersion

## 1. Concepto

La **dispersión de ondas** es el fenómeno por el cual distintas componentes espectrales de una señal se propagan con velocidades diferentes.

Como consecuencia, una señal no monocromática cambia de forma durante la propagación.

---

## 2. Fundamento físico

Una señal real puede representarse como superposición de componentes con diferentes números de onda y frecuencias.

Si la relación entre frecuencia angular y número de onda no es lineal, entonces cada componente puede propagarse con una velocidad de fase distinta. En ese caso, el paquete de ondas se deforma a medida que avanza.

Por tanto, la dispersión no es simplemente “que haya muchas frecuencias”, sino que el medio impone una relación $\omega(k)$ que hace que esas componentes no viajen todas igual.

---

## 3. Formulación matemática

Una onda dispersiva admite soluciones del tipo:

$$
\phi(x,t)=A e^{i[kx-\omega(k)t]}
$$

donde la frecuencia angular depende del número de onda.

La velocidad de fase es:

$$
c_p=\frac{\omega(k)}{k}
$$

y la velocidad de grupo es:

$$
c_g=\frac{d\omega}{dk}
$$

Si $c_p$ depende de $k$, la onda es dispersiva.

---

## 4. Aplicación a geófonos

En caracterización del subsuelo con geófonos, la dispersión es clave porque las [[Rayleigh Waves]] propagándose en medios estratificados presentan velocidades dependientes de la frecuencia.

Eso permite construir curvas de dispersión y luego inferir propiedades del subsuelo.

En near-surface geophysics, la práctica habitual se centra en la **velocidad de fase**; la velocidad de grupo existe y es físicamente relevante, pero se usa menos en inversión de sitio.

---

## 5. Implicaciones para el diseño experimental

- La dispersión observable depende del rango frecuencial excitado por la fuente.
- La geometría del arreglo condiciona el rango de longitudes de onda muestreado.
- No toda dispersión tiene el mismo origen: debe distinguirse entre [[Geometric Dispersion]] y [[Material Dispersion]].
- En métodos de ondas superficiales, la dispersión dominante suele ser la geométrica.

---

## 6. Fuente

- PDF: Sebastiano Foti Chapter 2
- capítulo o sección: 2.1.1 Two categories of wave motion
- página: 39–41

- PDF: Sebastiano Foti Chapter 2
- capítulo o sección: 2.1.2 Group velocity
- página: 41–42

- PDF: Sebastiano Foti Chapter 4
- capítulo o sección: 4.1 Phase and Group Velocity
- página: 205–206
"""

material_dispersion_text = r"""
# Material Dispersion

## 1. Concepto

La **dispersión material** ocurre cuando la velocidad de propagación depende de la frecuencia debido a las **propiedades constitutivas del material**.

No se debe a la estratificación geométrica del medio, sino al comportamiento interno del material.

---

## 2. Fundamento físico

En medios elásticos lineales ideales, muchas ondas pueden propagarse sin dispersión material.

Sin embargo, cuando el medio es:

- [[Viscoelastic Media]]
- [[Porous Media]]
- multicomponente
- disipativo

la respuesta mecánica puede depender de la frecuencia. Entonces, distintas componentes espectrales “ven” rigideces efectivas distintas y se propagan a velocidades diferentes.

---

## 3. Formulación matemática

La dispersión material no queda definida por una sola ecuación universal, pero se manifiesta cuando la relación de dispersión del medio incorpora parámetros materiales dependientes de la frecuencia.

En general:

$$
\omega = \omega(k)
$$

y por ello:

$$
c_p=\frac{\omega}{k}
$$

depende de la frecuencia o del número de onda por efecto de la constitución del material, no solo por la geometría del medio.

---

## 4. Aplicación a geófonos

En ensayos near-surface, la dispersión observada en ondas superficiales suele estar dominada por la [[Geometric Dispersion]] asociada a la estratificación vertical.

La dispersión material puede volverse importante cuando hay:

- medios altamente atenuantes
- saturación
- comportamiento viscoelástico marcado
- acoplamientos fluido-esqueleto en medios porosos

---

## 5. Implicaciones para el diseño experimental

- No conviene atribuir automáticamente toda dispersión observada a la estratificación.
- Si hay fuerte disipación o saturación, puede existir contribución de dispersión material.
- La separación entre dispersión geométrica y material requiere modelo físico, no solo inspección visual de la curva.
- En tesis experimental con geófonos, esta distinción es importante para no sobrerreclamar interpretación del perfil de suelo.

---

## 6. Fuente

- PDF: Sebastiano Foti Chapter 2
- capítulo o sección: 2.1.1 Two categories of wave motion
- página: 40–41
"""

dispersion_relation_text = r"""
# Dispersion Relation

## 1. Concepto

La **relación de dispersión** es la relación matemática que vincula la **frecuencia angular** $\omega$ con el **número de onda** $k$ para una onda que se propaga en un medio dado.

Es la descripción matemática fundamental de la propagación permitida por el sistema.

---

## 2. Fundamento físico

La física del medio —elasticidad, estratificación, disipación, condiciones de borde y geometría— impone restricciones sobre cómo pueden relacionarse la variación temporal y espacial de una onda.

Esa restricción se expresa mediante la relación de dispersión.

Si $\omega$ es proporcional a $k$, la velocidad de fase es constante y no hay dispersión. Si la dependencia no es lineal, diferentes componentes se propagan con velocidades distintas.

---

## 3. Formulación matemática

La forma general es:

$$
\omega=\omega(k)
$$

A partir de esta relación se definen:

### Velocidad de fase

$$
c_p=\frac{\omega}{k}
$$

### Velocidad de grupo

$$
c_g=\frac{d\omega}{dk}
$$

Cuando $c_p$ depende de $k$, el sistema es dispersivo.

---

## 4. Aplicación a geófonos

En ensayos con geófonos no se mide directamente la relación de dispersión teórica en forma cerrada. Lo que se estima experimentalmente son curvas de velocidad de fase o de grupo en función de frecuencia o longitud de onda, que constituyen manifestaciones observables de esa relación para las ondas presentes en el sitio.

---

## 5. Implicaciones para el diseño experimental

- Toda interpretación de dispersión parte de una relación $\omega(k)$.
- La identificación de modos depende de la forma de la relación de dispersión.
- Las curvas experimentales no deben confundirse con la relación teórica exacta: son una estimación observacional condicionada por fuente, arreglo, ruido y procesamiento.

---

## 6. Fuente

- PDF: Sebastiano Foti Chapter 2
- capítulo o sección: 2.1.1 Two categories of wave motion
- página: 39–40

- PDF: Sebastiano Foti Chapter 2
- capítulo o sección: 2.1.2 Group velocity
- página: 41–42
"""

def update_links():
    md_files = list(VAULT.rglob("*.md"))
    for f in md_files:
        txt = f.read_text(encoding="utf-8")
        original = txt

        txt = re.sub(r"\[\[Dispersive Waves\]\]", "[[Wave Dispersion]]", txt)
        txt = re.sub(r"\[\[Dispersive Waves\|", "[[Wave Dispersion|", txt)

        if txt != original:
            backup(f)
            f.write_text(txt, encoding="utf-8")
            print(f"[UPDATED LINKS] {f.relative_to(ROOT)}")

def delete_dispersive_waves():
    p = FILES["dispersive_waves"]
    if p.exists():
        backup(p)
        p.unlink()
        print(f"[DELETED] {p.relative_to(ROOT)}")

def main():
    write(FILES["wave_dispersion"], wave_dispersion_text)
    write(FILES["material_dispersion"], material_dispersion_text)
    write(FILES["dispersion_relation"], dispersion_relation_text)
    update_links()
    delete_dispersive_waves()
    print("[DONE] Dispersion layer cleaned.")

if __name__ == "__main__":
    main()
