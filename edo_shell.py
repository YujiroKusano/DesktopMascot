from __future__ import annotations

# エイリアスモジュール：従来の mascot（UIシェル）をわかりやすい名前で公開
from mascot import DesktopMascot  # re-export

__all__ = ["DesktopMascot"]

if __name__ == "__main__":
    import sys
    from PySide6.QtWidgets import QApplication
    app = QApplication(sys.argv)
    m = DesktopMascot()
    m.show()
    sys.exit(app.exec())

