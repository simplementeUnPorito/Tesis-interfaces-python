"""main.py — Entry point for the geophone multi-node scope application."""

from __future__ import annotations

import argparse
import os
import sys


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Geophone ESP32 multi-node scope (Python/PyQt6 edition)"
    )
    parser.add_argument(
        "--port",
        default=None,
        metavar="COM_PORT",
        help="Serial port to connect on startup (e.g. COM3 or /dev/ttyUSB0)",
    )
    parser.add_argument(
        "--baud",
        type=int,
        default=None,
        metavar="BAUD",
        help=f"Baud rate (default: {921600})",
    )
    parser.add_argument(
        "--log-dir",
        default=None,
        metavar="DIR",
        help="Directory for log files (default: <app_dir>/logs)",
    )
    parser.add_argument(
        "--data-dir",
        default=None,
        metavar="DIR",
        help="Directory for saved .mat data files (default: <app_dir>/datos)",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    # Resolve application directories relative to this file's location
    app_dir  = os.path.dirname(os.path.abspath(__file__))
    log_dir  = args.log_dir  or os.path.join(app_dir, "logs")
    data_dir = args.data_dir or os.path.join(app_dir, "datos")

    # Import Qt after argument parsing so --help works without a display
    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtCore import Qt

    # Qt6 enables High-DPI pixmaps automatically. Older PyQt6 builds still
    # expose this attribute; newer builds removed it.
    high_dpi_pixmaps = getattr(
        Qt.ApplicationAttribute, "AA_UseHighDpiPixmaps", None
    )
    if high_dpi_pixmaps is not None:
        QApplication.setAttribute(high_dpi_pixmaps)

    app = QApplication(sys.argv)
    app.setApplicationName("Geophone Scope")
    app.setOrganizationName("Tesis")

    from gui.main_window import MainWindow

    win = MainWindow(log_dir=log_dir, data_dir=data_dir)
    win.show()

    # Auto-connect if port was specified on CLI
    if args.port:
        import config
        baud = args.baud or config.BAUD
        # Use a short delay so the window is fully shown before connecting
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(300, lambda: win._on_connect(args.port, baud))

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
