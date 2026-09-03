"""Punto de entrada: ``python -m testbench``.

Sin argumentos abre el modo gráfico, que es para el banco. Con un subcomando
va al modo terminal, que es el que se invoca desde un script. ``gui`` fuerza el
gráfico de forma explícita.
"""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    if not argv or argv[0] == "gui":
        from .gui import main as gui_main

        return gui_main(argv[1:])

    from .cli import main as cli_main

    return cli_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
