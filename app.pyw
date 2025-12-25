import sys
import os
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from PySide6.QtWidgets import QApplication

# スクリプトディレクトリを import path に追加してローカルモジュール解決を安定化
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

# pythonw 対応: 作業ディレクトリを固定（相対パスの破綻防止）
try:
    os.chdir(BASE_DIR)
except Exception:
    # 失敗しても致命的ではないため継続
    pass


def _setup_logging() -> None:
    """
    バックグラウンド起動（pythonw.exe）でも診断可能なように
    ローテーション付きファイルロギングを構成する。
    """
    logs_dir = Path(BASE_DIR) / "logs"
    try:
        logs_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        # ディレクトリ作成に失敗しても続行（後続でハンドラ作成時に失敗し得る）
        pass

    log_file = logs_dir / "edo.log"

    root_logger = logging.getLogger()
    if root_logger.handlers:
        # すでに初期化済みなら二重初期化を避ける
        return

    root_logger.setLevel(logging.INFO)
    fmt = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # ファイルローテーション（最大5MB×3世代）
    try:
        file_handler = RotatingFileHandler(
            filename=str(log_file),
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setFormatter(fmt)
        file_handler.setLevel(logging.INFO)
        root_logger.addHandler(file_handler)
    except Exception:
        # ファイルハンドラが作れない環境でも最低限のログを維持
        pass

    # コンソール（python.exeでの起動時に有用。pythonw.exeでは出力されない）
    try:
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(fmt)
        stream_handler.setLevel(logging.INFO)
        root_logger.addHandler(stream_handler)
    except Exception:
        pass

    # 未処理例外をログへ
    def _excepthook(exc_type, exc_value, exc_traceback):
        logging.getLogger(__name__).exception(
            "Uncaught exception", exc_info=(exc_type, exc_value, exc_traceback)
        )

    sys.excepthook = _excepthook

from ui.shell import DesktopMascot


def main() -> None:
    _setup_logging()
    logging.getLogger(__name__).info("Application starting...")
    app = QApplication(sys.argv)
    # 起動時にローカル設定サーバを起動
    try:
        from ui.settings_server import get_or_start  # type: ignore
        srv = get_or_start(8766)
        logging.getLogger(__name__).info("Settings server: %s", srv.url())
    except Exception:
        logging.getLogger(__name__).exception("Failed to start local settings server")
    mascot = DesktopMascot()
    mascot.show()
    code = app.exec()
    logging.getLogger(__name__).info("Application exiting with code=%s", code)
    sys.exit(code)


if __name__ == "__main__":
    main()




