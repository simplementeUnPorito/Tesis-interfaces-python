"""
main.py — Geophone Filter Testbench Oscilloscope

Entry point. Launch this script to start the GUI.

Usage:
    python main.py

Requirements (install with pip):
    pip install -r requirements.txt
"""

import sys
from PyQt5.QtWidgets import QApplication
from main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setStyle('Fusion')

    window = MainWindow()
    window.show()

    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
