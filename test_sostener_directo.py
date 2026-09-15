from __future__ import annotations

import argparse
import unittest

from sostener_directo import punto_desde_texto


class PruebaPunto(unittest.TestCase):
    def test_acepta_cuatro_codigos(self) -> None:
        self.assertEqual(punto_desde_texto("0,164,75,-87"), [0, 164, 75, -87])

    def test_rechaza_cantidad_incorrecta(self) -> None:
        with self.assertRaises(argparse.ArgumentTypeError):
            punto_desde_texto("0,1,2")

    def test_rechaza_codigo_fuera_de_rango(self) -> None:
        with self.assertRaises(argparse.ArgumentTypeError):
            punto_desde_texto("0,164,256,-87")


if __name__ == "__main__":
    unittest.main()
