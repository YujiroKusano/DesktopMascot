from __future__ import annotations
import sys

# 互換レイヤ：実装は ui.shell へ移動済み
from ui.shell import DesktopMascot  # re-export

__all__ = ["DesktopMascot"]

if __name__ == "__main__":
    from PySide6.QtWidgets import QApplication
    app = QApplication(sys.argv)
    mascot = DesktopMascot()
    mascot.show()
    sys.exit(app.exec())


