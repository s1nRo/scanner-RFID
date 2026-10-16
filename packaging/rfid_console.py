"""Точка входа rfid.exe: то же, что rfid.cmd — меню и все подкоманды."""

import sys

from rfid.cli import main

if __name__ == "__main__":
    sys.exit(main())
