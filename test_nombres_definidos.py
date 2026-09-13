"""Verifica que los scripts de laboratorio no llamen a nombres inexistentes.

Un `import` no alcanza para detectarlo: Python resuelve los nombres al ejecutar,
asi que una funcion que no existe se descubre recien cuando el interprete llega a
esa linea. En un script que tarda veinte minutos en llegar ahi, eso son veinte
minutos de placa tirados.

Paso el 2026-09-13: un parche aborto antes de escribir la definicion de
`esperar_quieto` pero la llamada quedo puesta. Los tests pasaban, el modulo
importaba, y la corrida murio con NameError despues del barrido de IDAC1, que
son ocho minutos.
"""
from __future__ import annotations

import ast
import builtins
import io
import sys
import unittest
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI))

#: Los que se revisan. No es todo el directorio: los scripts viejos arrastran
#: deuda que no vale la pena tocar ahora.
VIGILADOS = ("centrar_lpo.py", "perfil_placa.py", "barrer_sumo.py",
             "medir_conflicto_sum_lp.py", "medir_acople.py",
             "registrar_punto.py", "regulador_pi.py")


def _nombres_definidos(arbol: ast.Module) -> set[str]:
    """Todo lo que el modulo define o importa en su nivel superior."""
    nombres = set(dir(builtins))
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.ClassDef):
            nombres.add(nodo.name)
        elif isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef)):
            nombres.add(nodo.name)
            nombres.update(a.arg for a in nodo.args.args)
            nombres.update(a.arg for a in nodo.args.kwonlyargs)
            if nodo.args.vararg:
                nombres.add(nodo.args.vararg.arg)
            if nodo.args.kwarg:
                nombres.add(nodo.args.kwarg.arg)
        elif isinstance(nodo, (ast.Import, ast.ImportFrom)):
            for alias in nodo.names:
                nombres.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(nodo, ast.Name) and isinstance(nodo.ctx, ast.Store):
            nombres.add(nodo.id)
        elif isinstance(nodo, (ast.comprehension,)):
            pass
        elif isinstance(nodo, ast.ExceptHandler) and nodo.name:
            nombres.add(nodo.name)
        elif isinstance(nodo, ast.Global):
            nombres.update(nodo.names)
    return nombres


def _nombres_llamados(arbol: ast.Module) -> set[str]:
    """Los nombres simples que aparecen invocados, como f(...)."""
    llamados = set()
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Call) and isinstance(nodo.func, ast.Name):
            llamados.add(nodo.func.id)
    return llamados


class PruebaNombres(unittest.TestCase):

    def test_no_hay_llamadas_a_nombres_inexistentes(self):
        for nombre in VIGILADOS:
            ruta = AQUI / nombre
            if not ruta.exists():
                continue
            with self.subTest(archivo=nombre):
                arbol = ast.parse(ruta.read_text(encoding="utf-8"))
                faltantes = _nombres_llamados(arbol) - _nombres_definidos(arbol)
                self.assertEqual(
                    faltantes, set(),
                    f"{nombre} llama a nombres que no existen: "
                    f"{sorted(faltantes)}")

    def test_no_hay_funciones_duplicadas(self):
        """Dos definiciones con el mismo nombre: gana la ultima, en silencio.

        Ya paso: un parche por indices duplico ochenta y cinco lineas y la
        version vieja quedo activa.
        """
        for nombre in VIGILADOS:
            ruta = AQUI / nombre
            if not ruta.exists():
                continue
            with self.subTest(archivo=nombre):
                arbol = ast.parse(ruta.read_text(encoding="utf-8"))
                vistos, repetidos = set(), set()
                for nodo in arbol.body:
                    if isinstance(nodo, ast.FunctionDef):
                        if nodo.name in vistos:
                            repetidos.add(nodo.name)
                        vistos.add(nodo.name)
                self.assertEqual(repetidos, set(),
                                 f"{nombre} define dos veces: "
                                 f"{sorted(repetidos)}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
