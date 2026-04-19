#!/usr/bin/env python3
from pathlib import Path
import json
import shutil
import sys

ROOT = Path(".").resolve()
CONCEPTS = ROOT / "Obsidian Vault" / "Notes" / "Surface Wave Methods" / "Concepts"
MANIFEST = ROOT / ".concept_reorder_manifest.json"

MODE = "apply"
if len(sys.argv) > 1:
    MODE = sys.argv[1].strip().lower()

if not CONCEPTS.exists():
    print(f"[ERROR] No existe: {CONCEPTS}")
    sys.exit(1)

# ----------------------------
# Mapa explícito: archivo -> subcarpeta destino
# ----------------------------
CATEGORY_MAP = {
    # 00 Foundations
    "Wave.md": "00 Foundations",
    "Linear Waves.md": "00 Foundations",
    "Attenuation.md": "00 Foundations",

    # 01 Elasticity and Continuum Mechanics
    "Elasticity.md": "01 Elasticity and Continuum Mechanics",
    "Stress Tensor.md": "01 Elasticity and Continuum Mechanics",
    "Strain Tensor.md": "01 Elasticity and Continuum Mechanics",
    "Elastic Half Space.md": "01 Elasticity and Continuum Mechanics",

    # 02 Wave Mathematics
    "1D Wave Equation.md": "02 Wave Mathematics",
    "d’Alembert Solution.md": "02 Wave Mathematics",
    "Hyperbolic Partial Differential Equations.md": "02 Wave Mathematics",
    "Hyperbolic Waves.md": "02 Wave Mathematics",
    "Fourier Integral.md": "02 Wave Mathematics",
    "Wave Superposition.md": "02 Wave Mathematics",
    "Angular Frequency.md": "02 Wave Mathematics",
    "Wavenumber.md": "02 Wave Mathematics",

    # 03 Wave Propagation
    "Dispersion Relation.md": "03 Wave Propagation",
    "Phase Velocity.md": "03 Wave Propagation",
    "Group Velocity.md": "03 Wave Propagation",
    "Dispersive Waves.md": "03 Wave Propagation",
    "Wave Dispersion.md": "03 Wave Propagation",
    "Mode Superposition.md": "03 Wave Propagation",
    "Mode Conversion.md": "03 Wave Propagation",
    "Elastic Wave Potentials.md": "03 Wave Propagation",
    "Lamb’s Problem.md": "03 Wave Propagation",

    # 04 Body and Surface Waves
    "Body Waves.md": "04 Body and Surface Waves",
    "P-waves.md": "04 Body and Surface Waves",
    "S-Waves.md": "04 Body and Surface Waves",
    "Surface Waves.md": "04 Body and Surface Waves",
    "Rayleigh Waves.md": "04 Body and Surface Waves",
    "Love Waves.md": "04 Body and Surface Waves",
    "Scholte Waves.md": "04 Body and Surface Waves",
    "Surface Water Waves.md": "04 Body and Surface Waves",
    "Water Waves.md": "04 Body and Surface Waves",
    "Surface Wave Modes.md": "04 Body and Surface Waves",
    "Rayleigh Eigenproblem.md": "04 Body and Surface Waves",

    # 05 Dispersion
    "Material Dispersion.md": "05 Dispersion",
    "Geometric Dispersion.md": "05 Dispersion",

    # 06 Media and Stratification
    "Layered Media.md": "06 Media and Stratification",
    "Vertically Inhomogeneous Media.md": "06 Media and Stratification",
    "Viscoelastic Media.md": "06 Media and Stratification",
    "Porous Media.md": "06 Media and Stratification",

    # 07 Acquisition and Processing
    "Adquisición de Datos.md": "07 Acquisition and Processing",
    "Procesamiento de Señales.md": "07 Acquisition and Processing",
    "Métodos Sísmicos Invasivos.md": "07 Acquisition and Processing",
    "Métodos Sísmicos No Invasivos.md": "07 Acquisition and Processing",

    # 08 Inversion and Interpretation
    "Inversión.md": "08 Inversion and Interpretation",
}

ALL_MD = sorted([p for p in CONCEPTS.glob("*.md") if p.is_file()])
SUBDIRS = [p for p in CONCEPTS.iterdir() if p.is_dir()]

def print_plan(plan):
    if not plan:
        print("[INFO] No hay movimientos para realizar.")
        return
    for src, dst in plan:
        print(f"[MOVE] {src.relative_to(CONCEPTS)} -> {dst.relative_to(CONCEPTS)}")

def build_plan():
    plan = []
    unclassified = []

    for src in ALL_MD:
        category = CATEGORY_MAP.get(src.name, "99 Review Needed")
        dst_dir = CONCEPTS / category
        dst = dst_dir / src.name
        if src == dst:
            continue
        plan.append((src, dst))
        if src.name not in CATEGORY_MAP:
            unclassified.append(src.name)

    return plan, unclassified

def apply_plan(plan):
    manifest = []
    for src, dst in plan:
        dst.parent.mkdir(parents=True, exist_ok=True)

        if dst.exists():
            print(f"[SKIP EXISTS] {dst.relative_to(CONCEPTS)}")
            continue

        shutil.move(str(src), str(dst))
        manifest.append({
            "from": str(src),
            "to": str(dst),
        })
        print(f"[MOVED] {src.relative_to(CONCEPTS)} -> {dst.relative_to(CONCEPTS)}")

    MANIFEST.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[MANIFEST] {MANIFEST}")

def undo():
    if not MANIFEST.exists():
        print(f"[ERROR] No existe manifiesto: {MANIFEST}")
        sys.exit(1)

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

    for item in reversed(manifest):
        src = Path(item["to"])
        dst = Path(item["from"])

        if not src.exists():
            print(f"[SKIP MISSING] {src}")
            continue

        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        print(f"[UNDO] {src.relative_to(ROOT)} -> {dst.relative_to(ROOT)}")

    # eliminar carpetas vacías dentro de Concepts
    for p in sorted(CONCEPTS.rglob("*"), reverse=True):
        if p.is_dir():
            try:
                p.rmdir()
                print(f"[RMDIR EMPTY] {p.relative_to(CONCEPTS)}")
            except OSError:
                pass

    print("[DONE] Undo completado.")

if MODE not in {"plan", "apply", "undo"}:
    print("Uso:")
    print("  python3 reorder_concepts.py plan")
    print("  python3 reorder_concepts.py apply")
    print("  python3 reorder_concepts.py undo")
    sys.exit(1)

if MODE == "undo":
    undo()
    sys.exit(0)

plan, unclassified = build_plan()

print_plan(plan)

if unclassified:
    print("\n[UNCLASSIFIED -> 99 Review Needed]")
    for name in unclassified:
        print(f"  - {name}")

if MODE == "plan":
    sys.exit(0)

apply_plan(plan)
