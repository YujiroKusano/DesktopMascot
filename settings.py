from __future__ import annotations

from typing import Dict, Any, Optional, List, Tuple
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QTabWidget, QWidget, QFormLayout, QLineEdit, QSpinBox,
    QDoubleSpinBox, QCheckBox, QPushButton, QTextEdit, QHBoxLayout, QMessageBox, QComboBox, QLabel
)
from PySide6.QtCore import Qt
from agent.config import load_config, save_config


class SettingsWindow(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("設定")
        self.setModal(True)
        self.resize(520, 560)
        self._cfg: Dict[str, Any] = load_config()

        layout = QVBoxLayout(self)
        self.tabs = QTabWidget(self)
        layout.addWidget(self.tabs, 1)

        # UI定義に基づきタブ/項目を動的構築（fallbackあり）
        self._field_widgets: List[Tuple[str, str, object]] = []  # (path, type, widget)
        if not self._build_tabs_from_ui():
            self._build_tabs_fallback()

        # ボタン
        btns = QHBoxLayout()
        btns.addStretch(1)
        self.btn_save = QPushButton("保存")
        self.btn_cancel = QPushButton("キャンセル")
        btns.addWidget(self.btn_save)
        btns.addWidget(self.btn_cancel)
        layout.addLayout(btns, 0)

        self.btn_cancel.clicked.connect(self.reject)
        self.btn_save.clicked.connect(self._on_save)

    # --- 動的UI構築（configの ui.tabs に従う） ---
    def _build_tabs_from_ui(self) -> bool:
        ui = self._cfg.get("ui", None)
        if not isinstance(ui, dict):
            return False
        tabs = ui.get("tabs", None)
        if not isinstance(tabs, list) or not tabs:
            return False
        for tab in tabs:
            title = str(tab.get("title", "設定"))
            fields = tab.get("fields", [])
            w = QWidget(self)
            form = QFormLayout(w)
            for f in fields:
                path = str(f.get("path", "")).strip()
                label = str(f.get("label", path or "項目")).strip()
                ftype = str(f.get("type", "string")).strip().lower()
                hint = str(f.get("hint", "") or "").strip()
                if not path:
                    continue
                widget = self._create_field_widget(ftype, f, path)
                if widget is None:
                    continue
                # ヒント（ツールチップ）対応
                if hint:
                    try:
                        widget.setToolTip(hint)
                    except Exception:
                        pass
                    lbl = QLabel(label, self)
                    try:
                        lbl.setToolTip(hint)
                    except Exception:
                        pass
                    form.addRow(lbl, widget)
                else:
                    form.addRow(label, widget)
                self._field_widgets.append((path, ftype, widget))
            self.tabs.addTab(w, title)
        return True

    def _create_field_widget(self, ftype: str, fdef: Dict[str, Any], path: str):
        # 優先: フィールド定義の value, 次に実値パス
        val = fdef.get("value", None)
        if val is None:
            val = self._get_by_path(self._cfg, path)
        if ftype == "bool":
            w = QCheckBox()
            w.setChecked(bool(val))
            return w
        if ftype == "int":
            w = QSpinBox()
            w.setRange(int(fdef.get("min", -10_000_000)), int(fdef.get("max", 10_000_000)))
            w.setSingleStep(int(fdef.get("step", 1)))
            w.setValue(int(val if val is not None else 0))
            return w
        if ftype == "float":
            w = QDoubleSpinBox()
            w.setDecimals(int(fdef.get("decimals", 2)))
            w.setRange(float(fdef.get("min", -1e9)), float(fdef.get("max", 1e9)))
            w.setSingleStep(float(fdef.get("step", 0.1)))
            w.setValue(float(val if val is not None else 0.0))
            return w
        if ftype in ("string", "password"):
            w = QLineEdit(str(val if val is not None else ""))
            if bool(fdef.get("multiline", False)):
                # multiline指定なら QTextEdit を使う
                te = QTextEdit()
                te.setPlainText(str(val if val is not None else ""))
                return te
            if ftype == "password":
                w.setEchoMode(QLineEdit.Password)
            return w
        if ftype == "textarea":
            te = QTextEdit()
            te.setPlainText(str(val if val is not None else ""))
            return te
        if ftype in ("select", "enum"):
            items = fdef.get("choices", [])
            cb = QComboBox()
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, dict):
                        cb.addItem(str(item.get("label", item.get("value", ""))), item.get("value", ""))
                    else:
                        cb.addItem(str(item), item)
            # 現在値を選択
            idx = cb.findData(val)
            if idx >= 0:
                cb.setCurrentIndex(idx)
            return cb
        if ftype in ("array.string", "list.string"):
            te = QTextEdit()
            arr = val if isinstance(val, list) else []
            te.setPlainText("\n".join(str(x) for x in arr))
            return te
        # 未対応はテキストとして表示
        w = QLineEdit(str(val if val is not None else ""))
        return w

    def _build_tabs_fallback(self) -> None:
        # 最低限のフォールバック（固定）
        # マスコット
        tab = QWidget(self); form = QFormLayout(tab); m = self._cfg.get("mascot", {})
        w1 = QSpinBox(); w1.setRange(32, 512); w1.setValue(int(m.get("icon_size_px", 160))); form.addRow("アイコンサイズ(px)", w1); self._field_widgets.append(("mascot.icon_size_px", "int", w1))
        w2 = QSpinBox(); w2.setRange(1, 1000); w2.setValue(int(m.get("timer_ms", 33))); form.addRow("更新間隔(ms)", w2); self._field_widgets.append(("mascot.timer_ms", "int", w2))
        w3 = QDoubleSpinBox(); w3.setRange(0.0, 10.0); w3.setSingleStep(0.1); w3.setValue(float(m.get("base_speed_px", 0.6))); form.addRow("基準速度(px/tick)", w3); self._field_widgets.append(("mascot.base_speed_px", "float", w3))
        self.tabs.addTab(tab, "マスコット")

    # --- ヘルパ ---
    def _get_by_path(self, root: Dict[str, Any], path: str) -> Any:
        import re
        cur: Any = root
        tokens = path.split(".")
        for tok in tokens:
            m = re.fullmatch(r"([^\[\]]+)(?:\[(\d+)\])?", tok)
            if not m:
                return None
            key, idx = m.group(1), m.group(2)
            if not isinstance(cur, dict) or key not in cur:
                return None
            cur = cur[key]
            if idx is not None:
                try:
                    i = int(idx)
                    if not (isinstance(cur, list) and 0 <= i < len(cur)):
                        return None
                    cur = cur[i]
                except Exception:
                    return None
        return cur

    def _set_by_path(self, root: Dict[str, Any], path: str, value: Any) -> None:
        import re
        cur: Any = root
        tokens = path.split(".")
        for tok in tokens[:-1]:
            m = re.fullmatch(r"([^\[\]]+)(?:\[(\d+)\])?", tok)
            if not m:
                return
            key, idx = m.group(1), m.group(2)
            if key not in cur or not isinstance(cur[key], (dict, list)):
                # create dict by default
                cur[key] = {} if idx is None else []
            cur = cur[key]
            if idx is not None:
                i = int(idx)
                # ensure list size
                while len(cur) <= i:
                    cur.append(None)
                if not isinstance(cur[i], dict) and tok != tokens[-2]:
                    # prepare container for deeper nesting if needed
                    cur[i] = {}
                cur = cur[i]
        # final token
        m = re.fullmatch(r"([^\[\]]+)(?:\[(\d+)\])?", tokens[-1])
        if not m:
            return
        key, idx = m.group(1), m.group(2)
        if idx is None:
            if not isinstance(cur, dict):
                return
            cur[key] = value
        else:
            if key not in cur or not isinstance(cur[key], list):
                cur[key] = []
            arr = cur[key]
            i = int(idx)
            while len(arr) <= i:
                arr.append(None)
            arr[i] = value

    def _set_ui_value(self, cfg: Dict[str, Any], path: str, value: Any) -> None:
        try:
            ui = cfg.get("ui", {})
            tabs = ui.get("tabs", [])
            for tab in tabs:
                fields = tab.get("fields", [])
                for f in fields:
                    if isinstance(f, dict) and f.get("path") == path:
                        f["value"] = value
        except Exception:
            pass

    # --- 保存 ---
    def _on_save(self) -> None:
        try:
            cfg = load_config()
            # 動的にフィールドを書き戻す
            for path, ftype, w in self._field_widgets:
                if ftype == "bool":
                    value = bool(w.isChecked())  # type: ignore[attr-defined]
                elif ftype == "int":
                    value = int(w.value())  # type: ignore[attr-defined]
                elif ftype == "float":
                    value = float(w.value())  # type: ignore[attr-defined]
                elif ftype in ("select", "enum"):
                    value = w.currentData()  # type: ignore[attr-defined]
                elif ftype in ("array.string", "list.string"):
                    text = str(w.toPlainText())  # type: ignore[attr-defined]
                    value = [s.strip() for s in text.splitlines() if s.strip()]
                elif ftype in ("string", "password"):
                    if hasattr(w, "toPlainText"):
                        value = str(w.toPlainText())  # QTextEdit
                    else:
                        value = str(w.text())  # QLineEdit
                elif ftype == "textarea":
                    value = str(w.toPlainText())  # type: ignore[attr-defined]
                else:
                    # 未対応型は文字列として保存
                    if hasattr(w, "toPlainText"):
                        value = str(w.toPlainText())
                    elif hasattr(w, "text"):
                        value = str(w.text())
                    else:
                        continue
                self._set_by_path(cfg, path, value)
                self._set_ui_value(cfg, path, value)

            save_config(cfg)
            self.accept()
        except Exception as ex:
            QMessageBox.warning(self, "保存エラー", f"設定の保存に失敗しました。\n{ex}")
