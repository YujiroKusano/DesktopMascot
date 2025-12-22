from __future__ import annotations

from typing import Optional, List
import re
import random
import time
import json
from PySide6.QtWidgets import QWidget, QLabel, QLineEdit, QPushButton, QHBoxLayout, QVBoxLayout, QScrollArea, QFrame, QTextEdit, QSizePolicy, QGraphicsDropShadowEffect
from PySide6.QtCore import Qt, QTimer, QRect, QObject, Signal, QEvent
from PySide6.QtGui import QColor, QPainterPath, QRegion, QCursor
import threading
from agent.config import load_config
from agent.safety import check_text_allowed
# ネット検索フォールバックは無効化（シンプル化）
from agent.llm import chat as llm_chat, translate_to_japanese_if_needed
from agent.memory import MemoryStore


class _Bubble(QLabel):
    def __init__(self) -> None:
        super().__init__("")
        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setStyleSheet(
            "background:rgba(255,255,255,.92);"
            "border:1px solid #999;"
            "padding:8px 10px;"
            "border-radius:8px;"
        )
        self.setWordWrap(True)
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide)

    def show_message(self, text: str, host_rect: QRect, screen_rect: QRect, msec: int = 3000) -> None:
        # 表示時間を文字数に応じて延長
        from agent.config import load_config
        try:
            talk = load_config().get("talk", {})
            base_ms = int(talk.get("bubble_time_base_ms", 2000))
            per_ms = int(talk.get("bubble_time_per_char_ms", 30))
            max_ms = int(talk.get("bubble_time_max_ms", 15000))
            dyn_ms = min(max_ms, base_ms + max(0, len(text)) * per_ms)
            if msec is None or msec <= 0:
                msec = dyn_ms
            else:
                msec = max(msec, dyn_ms)
        except Exception:
            pass
        # 画面幅に応じてバブルの最大幅を抑制し、縦方向に自動拡張させる
        try:
            max_w = int(min(screen_rect.width() * 0.42, 460))
            self.setMaximumWidth(max(200, max_w))
        except Exception:
            pass
        self.setText(text)
        self.adjustSize()
        # なるべくホストの上側に出す（はみ出す場合は下側）
        x = host_rect.x() + 20
        y = host_rect.y() - self.height() - 10
        if y < screen_rect.top():
            y = host_rect.bottom() + 10
        # 右端はみ出しを抑制
        if x + self.width() > screen_rect.right():
            x = max(screen_rect.right() - self.width() - 8, screen_rect.left())
        self.move(x, y)
        # 既存の隠すタイマーをリセットしてから表示
        try:
            self._hide_timer.stop()
        except Exception:
            pass
        self.show()
        if msec and msec > 0:
            self._hide_timer.start(msec)


class _InputBar(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self._drag_offset = None  # type: ignore[var-annotated]
        # 視認性のため、背景つきの入力バーにする（軽い角丸と枠）
        self.setStyleSheet(
            "QWidget {"
            "  background:rgba(255,255,255,.96);"
            "  border:1px solid #999;"
            "  border-radius:8px;"
            "}"
            "QLineEdit {"
            "  background:#ffffff;"
            "  border:1px solid #bbb;"
            "  border-radius:6px;"
            "  padding:4px 6px;"
            "}"
            "QPushButton {"
            "  background:#f5f5f5;"
            "  border:1px solid #bbb;"
            "  border-radius:6px;"
            "  padding:4px 10px;"
            "}"
            "QPushButton:pressed {"
            "  background:#e9e9e9;"
            "}"
        )
        self._edit = QLineEdit(self)
        self._edit.setPlaceholderText("エドに話しかける… Enterで送信")
        self._mic = QPushButton("🎤", self)
        self._send = QPushButton("送信", self)
        lay = QHBoxLayout(self)
        # チャットと同じ余白感
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        lay.addWidget(self._edit, 1)
        lay.addWidget(self._mic, 0)
        lay.addWidget(self._send, 0)
        self._on_send_cb = None  # type: ignore[var-annotated]
        self._on_mic_press = None  # type: ignore[var-annotated]
        self._on_mic_release = None  # type: ignore[var-annotated]
        def _try_send():
            text = self._edit.text().strip()
            if text and self._on_send_cb:
                self._on_send_cb(text)
                self._edit.clear()
        self._edit.returnPressed.connect(_try_send)
        self._send.clicked.connect(_try_send)
        self._mic.setToolTip("押している間だけ録音（プッシュトーク）")
        self._mic.pressed.connect(lambda: self._on_mic_press and self._on_mic_press())
        self._mic.released.connect(lambda: self._on_mic_release and self._on_mic_release())

    def bind_send(self, cb) -> None:
        self._on_send_cb = cb
    def bind_mic_press(self, cb) -> None:
        self._on_mic_press = cb
    def bind_mic_release(self, cb) -> None:
        self._on_mic_release = cb

    def focus_edit(self) -> None:
        try:
            self._edit.setFocus()
        except Exception:
            pass

    def show_at(self, host_rect: QRect, screen_rect: QRect, anchor: str = "follow") -> None:
        # anchor: "follow"（マスコット付近） or "screen_br"（画面右下固定）
        self.adjustSize()
        if anchor == "screen_br":
            x = screen_rect.right() - self.width() - 12
            y = screen_rect.bottom() - self.height() - 12
        else:
            # なるべくホストの下側に出す（はみ出す場合は上側）
            x = host_rect.x() + 10
            y = host_rect.bottom() + 10
            if y + self.height() > screen_rect.bottom():
                y = host_rect.y() - self.height() - 10
            if x + self.width() > screen_rect.right():
                x = max(screen_rect.right() - self.width() - 8, screen_rect.left())
        self.move(x, y)
        self.show()

    def set_busy(self, busy: bool) -> None:
        self._edit.setEnabled(not busy)
        self._send.setEnabled(not busy)
        try:
            self._mic.setEnabled(not busy)
        except Exception:
            pass

    def hide_bar(self) -> None:
        self.hide()

    def is_visible(self) -> bool:
        return self.isVisible()

    # --- drag to move ---
    def mousePressEvent(self, event):
        try:
            if event.button() == Qt.LeftButton:
                self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                event.accept()
                return
        except Exception:
            pass
        return super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        try:
            if event.buttons() & Qt.LeftButton and self._drag_offset is not None:
                new_pos = event.globalPosition().toPoint() - self._drag_offset
                self.move(new_pos)
                event.accept()
                return
        except Exception:
            pass
        return super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        try:
            if event.button() == Qt.LeftButton:
                self._drag_offset = None
                event.accept()
                return
        except Exception:
            pass
        return super().mouseReleaseEvent(event)

class _ChatWindow(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        # 背景は不透明（透過を無効化）
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        # スタイルシートで背景を描く前提に統一
        try:
            self.setAttribute(Qt.WA_StyledBackground, True)
        except Exception:
            pass
        self._drag_offset = None  # type: ignore[var-annotated]
        self._manual_position = False
        self._stick_bottom = True
        self._corner_radius_px = 12
        # 端ドラッグでサイズ変更するための状態
        self._resize_margin_px = 8
        self._resizing = False
        self._resize_left = False
        self._resize_right = False
        self._resize_top = False
        self._resize_bottom = False
        self._resize_start_geom = None  # type: ignore[var-annotated]
        self._resize_start_mouse = None  # type: ignore[var-annotated]
        self.setObjectName("chatRoot")
        self.setStyleSheet(
            "QWidget#chatRoot {"
            "  background: qlineargradient(x1:0, y1:0, x2:0, y2:1,"
            "               stop:0 rgba(245,247,252,255),"
            "               stop:1 rgba(220,228,242,255));"
            "  border:1px solid rgba(0,0,0,80);"
            "  border-radius:12px;"
            "}"
            "QScrollArea { background:transparent; border:none; }"
            "QScrollArea > QWidget { background:transparent; }"
            "QScrollArea > QWidget > QWidget { background:transparent; }"
            "QLabel#msg {"
            "  border-radius:8px;"
            "  padding:6px 8px;"
            "  border:1px solid #d0d6e0;"
            "  background:#f2f4f8;"
            "}"
            "QLabel#msg[chatRole=\"user\"] {"
            "  background:#d1eaff;"
            "  border-color:#90caff;"
            "}"
            "QLabel#msg[chatRole=\"assistant\"] {"
            "  background:#f2f4f8;"
            "  border-color:#d0d6e0;"
            "}"
            "QLabel#msg[chatRole=\"system\"] {"
            "  background:#fff4d6;"
            "  border-color:#e3c882;"
            "}"
        )
        # 背景塗りはスタイルに統一（ダブルペイントを避ける）
        try:
            self.setAutoFillBackground(False)
        except Exception:
            pass
        # ドロップシャドウで浮遊感
        try:
            shadow = QGraphicsDropShadowEffect(self)
            shadow.setBlurRadius(12)
            shadow.setOffset(0, 6)
            shadow.setColor(QColor(0, 0, 0, 90))
            self.setGraphicsEffect(shadow)
        except Exception:
            pass
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        # ヘッダー/×ボタンは無し（LINE風：バブル＋入力のみ）

        # History scroll area
        self._scroll = QScrollArea(self)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setWidgetResizable(True)
        try:
            # スクロールエリアを優先的に広げる
            self._scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        except Exception:
            pass
        self._history_container = QWidget()
        self._history_layout = QVBoxLayout(self._history_container)
        self._history_layout.setContentsMargins(0, 0, 0, 0)
        self._history_layout.setSpacing(6)
        # 余白は上側にたまり、メッセージは下から積み上がる
        try:
            self._history_layout.setAlignment(Qt.AlignBottom)
        except Exception:
            pass
        self._scroll.setWidget(self._history_container)
        root.addWidget(self._scroll, 1)

        # Input row（元の仕様：QLineEdit + Enter送信）
        self._on_send_cb = None  # type: ignore[var-annotated]
        self._edit = QLineEdit(self)
        self._edit.setPlaceholderText("エドに話しかける… Enterで送信")
        self._mic = QPushButton("🎤", self)
        self._send = QPushButton("送信", self)
        # オーバーレイ系（テキスト上）
        self._btn_bottom = QPushButton("▼", self)  # 下中央オーバーレイ
        self._btn_bottom.setToolTip("一番下へスクロール")
        self._btn_bottom.setParent(self)
        self._btn_bottom.raise_()
        self._btn_bottom.setFixedSize(28, 28)
        self._btn_bottom.setStyleSheet(
            "QPushButton {"
            "  background: rgba(0,0,0,0.35);"
            "  color: white;"
            "  border: 1px solid rgba(255,255,255,0.6);"
            "  border-radius: 14px;"
            "  font-weight: bold;"
            "}"
            "QPushButton:hover { background: rgba(0,0,0,0.5); }"
        )
        self._btn_close = QPushButton("×", self)   # 右上オーバーレイ
        self._btn_close.setToolTip("閉じる")
        self._btn_close.setParent(self)
        self._btn_close.raise_()
        self._btn_close.setFixedSize(24, 24)
        self._btn_close.setStyleSheet(
            "QPushButton {"
            "  background: rgba(0,0,0,0.25);"
            "  color: white;"
            "  border: 1px solid rgba(255,255,255,0.5);"
            "  border-radius: 12px;"
            "  font-weight: bold;"
            "}"
            "QPushButton:hover { background: rgba(0,0,0,0.4); }"
        )
        bottom = QHBoxLayout()
        bottom.setContentsMargins(0, 0, 0, 0)
        bottom.setSpacing(6)
        # 入力行を薄めにして、履歴エリアを広げる
        try:
            self._edit.setFixedHeight(30)
            self._mic.setFixedHeight(30)
            self._send.setFixedHeight(30)
            self._btn_bottom.setFixedHeight(30)
        except Exception:
            pass
        bottom.addWidget(self._edit, 1)
        bottom.addWidget(self._mic, 0)
        bottom.addWidget(self._send, 0)
        root.addLayout(bottom, 0)
        # レイアウト比率: 履歴(上)を大きく、入力(下)は固定寄り
        try:
            root.setStretch(0, 4)  # scroll
            root.setStretch(1, 0)  # input row
        except Exception:
            pass

        self._on_mic_press = None  # type: ignore[var-annotated]
        self._on_mic_release = None  # type: ignore[var-annotated]

        def _try_send_btn():
            text = self._edit.text().strip()
            if text and self._on_send_cb:
                self._on_send_cb(text)
                self._edit.clear()
                try:
                    # 送信直後に下端へ
                    self.scroll_to_bottom()
                except Exception:
                    pass
        self._edit.returnPressed.connect(_try_send_btn)
        self._send.clicked.connect(_try_send_btn)
        self._btn_bottom.clicked.connect(lambda: self.scroll_to_bottom())
        self._btn_close.clicked.connect(self.hide_panel)
        self._mic.setToolTip("押している間だけ録音（プッシュトーク）")
        self._mic.pressed.connect(lambda: self._on_mic_press and self._on_mic_press())
        self._mic.released.connect(lambda: self._on_mic_release and self._on_mic_release())

        self.apply_config()

        # カーソル更新のためのマウストラッキングとイベントフィルタ
        try:
            self.setMouseTracking(True)
            self._scroll.setMouseTracking(True)
            self._history_container.setMouseTracking(True)
            self._edit.setMouseTracking(True)
            self._mic.setMouseTracking(True)
            self._send.setMouseTracking(True)
            for w in (self, self._scroll, self._history_container, self._edit, self._mic, self._send):
                w.installEventFilter(self)
        except Exception:
            pass

        # スクロールで最下部ボタンの表示を制御
        try:
            self._scroll.verticalScrollBar().valueChanged.connect(lambda _v: self._update_bottom_button_visibility())
        except Exception:
            pass
        # 起動直後は必ず非表示
        try:
            self.hide()
        except Exception:
            pass

    def apply_config(self) -> None:
        try:
            talk = load_config().get("talk", {})
            w = int(talk.get("chat_panel_width_px", 320))
            h = int(talk.get("chat_panel_height_px", 1200))
            w = max(200, min(1200, w))
            h = max(420, min(1400, h))
            self.resize(w, h)
        except Exception:
            self.resize(320, 1200)

    def bind_send(self, cb) -> None:
        self._on_send_cb = cb
    def bind_mic_press(self, cb) -> None:
        self._on_mic_press = cb
    def bind_mic_release(self, cb) -> None:
        self._on_mic_release = cb

    def clear_history(self) -> None:
        try:
            for i in reversed(range(self._history_layout.count())):
                item = self._history_layout.itemAt(i)
                w = item.widget()
                if w is not None:
                    w.setParent(None)
        except Exception:
            pass

    def populate_history(self, turns: List[dict]) -> None:
        try:
            self.clear_history()
            # 旧い→新しい順で下詰めになるよう、そのまま追加
            for t in turns or []:
                r = str(t.get("role", "")).lower()
                c = str(t.get("content", ""))
                if not r or not c:
                    continue
                # system は通知扱いに寄せる
                role = "system" if r not in ("user", "assistant") else r
                self.append_message(c, role=role)
            self.scroll_to_bottom()
        except Exception:
            pass

    def append_message(self, text: str, role: str) -> None:
        # Row container so that bubble doesn't stretch full width (LINE風)
        row = QWidget(self._history_container)
        row_lay = QHBoxLayout(row)
        row_lay.setContentsMargins(0, 0, 0, 0)
        row_lay.setSpacing(6)

        lbl = QLabel(text, row)
        lbl.setObjectName("msg")
        lbl.setWordWrap(False)
        lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lbl.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Maximum)

        # role-based style and alignment
        r = (role or "assistant").lower()
        if r not in ("user", "assistant", "system"):
            r = "assistant"
        lbl.setProperty("chatRole", r)

        # bubble width cap and natural width（横は抑えめ：ウィンドウ幅の82%）
        max_w = max(160, int(self.width() * 0.82))
        lbl.setMaximumWidth(max_w)
        try:
            fm = lbl.fontMetrics()
            content_w = fm.horizontalAdvance(text)
            pad = 18
            natural_w = min(max_w, content_w + pad)  # paddingぶんを加算
            natural_w = max(60, natural_w)
            lbl.setMinimumWidth(natural_w)
            # 折り返しは「自然幅が上限を超えた時のみ」有効化
            lbl.setWordWrap(natural_w >= max_w)
        except Exception:
            pass
        lbl.adjustSize()

        if r == "user":
            row_lay.addStretch(1)
            row_lay.addWidget(lbl, 0, Qt.AlignRight | Qt.AlignVCenter)
        else:
            row_lay.addWidget(lbl, 0, Qt.AlignLeft | Qt.AlignVCenter)
            row_lay.addStretch(1)

        # 末尾に追加（下から流れる）
        self._history_layout.addWidget(row, 0)
        try:
            self.scroll_to_bottom()
            self._update_bottom_button_visibility()
        except Exception:
            pass

    def set_busy(self, busy: bool) -> None:
        self._edit.setEnabled(not busy)
        self._send.setEnabled(not busy)
        try:
            self._mic.setEnabled(not busy)
        except Exception:
            pass

    def focus_edit(self) -> None:
        try:
            self._edit.setFocus()
        except Exception:
            pass

    def show_at(self, host_rect: QRect, screen_rect: QRect, anchor: str = "screen_br") -> None:
        self.adjustSize()
        # 画面サイズに収まるようにリサイズ（はみ出し防止）
        try:
            avail_w = max(200, screen_rect.width() - 24)
            avail_h = max(160, screen_rect.height() - 24)
            # 横は広げず（必要なら縮める）、縦は可能な範囲で拡大する
            new_w = min(self.width(), avail_w)
            target_h = max(self.height(), int(avail_h * 0.9))  # 画面高の90%を目安に広げる
            new_h = min(target_h, avail_h)
            if new_w != self.width() or new_h != self.height():
                self.resize(new_w, new_h)
        except Exception:
            pass
        if anchor == "screen_br":
            x = screen_rect.right() - self.width() - 12
            y = screen_rect.bottom() - self.height() - 12
        else:
            x = host_rect.x() + 10
            y = host_rect.bottom() + 10 - self.height() - 10
            if y < screen_rect.top():
                y = screen_rect.top() + 12
            if x + self.width() > screen_rect.right():
                x = max(screen_rect.right() - self.width() - 8, screen_rect.left())
        self.move(x, y)
        self.show()
        # プログラムで表示した直後は自動追従モード（手動ではない）
        self._manual_position = False
        # オーバーレイ類の配置と可視状態を更新
        try:
            self._reposition_overlays()
            self._update_bottom_button_visibility()
            self._apply_window_mask()
        except Exception:
            pass

    def hide_panel(self) -> None:
        self.hide()

    def is_visible(self) -> bool:
        return self.isVisible()

    def resizeEvent(self, event) -> None:
        try:
            max_w = max(160, int(self.width() * 0.82))
            # update all message labels
            for i in range(self._history_layout.count()):  # 末尾ストレッチは使わない
                item = self._history_layout.itemAt(i)
                row = item.widget()
                if isinstance(row, QWidget):
                    lbl = row.findChild(QLabel, "msg")
                    if isinstance(lbl, QLabel):
                        lbl.setMaximumWidth(max_w)
                        try:
                            fm = lbl.fontMetrics()
                            content_w = fm.horizontalAdvance(lbl.text())
                            pad = 18
                            natural_w = min(max_w, content_w + pad)
                            natural_w = max(60, natural_w)
                            lbl.setMinimumWidth(natural_w)
                            lbl.setWordWrap(natural_w >= max_w)
                        except Exception:
                            pass
                        lbl.adjustSize()
            self._reposition_overlays()
            self._update_bottom_button_visibility()
            self._apply_window_mask()
        except Exception:
            pass
        return super().resizeEvent(event)

    def scroll_to_bottom(self) -> None:
        try:
            v = self._scroll.verticalScrollBar()
            v.setValue(v.maximum())
        except Exception:
            pass
        try:
            self._update_bottom_button_visibility()
        except Exception:
            pass
    
    # --- resize by grabbing window edges ---
    def _hit_edges(self, pos) -> tuple[bool, bool, bool, bool]:
        try:
            m = int(self._resize_margin_px)
            r = self.rect()
            x, y = pos.x(), pos.y()
            on_left = (0 <= x <= m)
            on_right = (r.width() - m <= x <= r.width())
            on_top = (0 <= y <= m)
            on_bottom = (r.height() - m <= y <= r.height())
            return on_left, on_right, on_top, on_bottom
        except Exception:
            return (False, False, False, False)

    def _update_cursor_for_pos(self, pos) -> None:
        try:
            l, r, t, b = self._hit_edges(pos)
            if (l and t) or (r and b):
                self.setCursor(Qt.SizeFDiagCursor)
            elif (r and t) or (l and b):
                self.setCursor(Qt.SizeBDiagCursor)
            elif l or r:
                self.setCursor(Qt.SizeHorCursor)
            elif t or b:
                self.setCursor(Qt.SizeVerCursor)
            else:
                self.setCursor(Qt.ArrowCursor)
        except Exception:
            pass

    def _reposition_overlays(self) -> None:
        try:
            # 下中央
            g = self._scroll.geometry()
            bx = g.x() + (g.width() - self._btn_bottom.width()) // 2
            by = g.y() + g.height() - self._btn_bottom.height() - 8
            self._btn_bottom.move(bx, by)
            # 右上
            self._btn_close.move(self.width() - self._btn_close.width() - 8, 8)
            self._btn_bottom.raise_(); self._btn_close.raise_()
        except Exception:
            pass

    def eventFilter(self, obj, event):
        try:
            if event.type() == QEvent.MouseMove:
                # グローバル座標から自身の座標系へ変換してエッジ判定
                try:
                    gp = event.globalPosition().toPoint()
                except Exception:
                    gp = QCursor.pos()
                pos = self.mapFromGlobal(gp)
                self._update_cursor_for_pos(pos)
        except Exception:
            pass
        return super().eventFilter(obj, event)

    def _update_bottom_button_visibility(self) -> None:
        try:
            v = self._scroll.verticalScrollBar()
            # スクロール可能かつ最下部にいないときだけ表示
            can_scroll = v.maximum() > 0
            at_bottom = (v.maximum() - v.value()) <= 4
            self._btn_bottom.setVisible(bool(can_scroll and not at_bottom))
        except Exception:
            pass

    def _apply_window_mask(self) -> None:
        """
        不透明ウィンドウでも角丸形状になるよう、ウィンドウマスクを適用する。
        透過を使わず角で黒く尖るのを防ぐ。
        """
        try:
            r = max(0, int(self._corner_radius_px))
            if r == 0:
                self.clearMask()
                return
            rect = self.rect().adjusted(0, 0, -1, -1)
            path = QPainterPath()
            path.addRoundedRect(rect, r, r)
            region = QRegion(path.toFillPolygon().toPolygon())
            self.setMask(region)
        except Exception:
            pass

    # --- drag to move ---
    def mousePressEvent(self, event):
        try:
            if event.button() == Qt.LeftButton:
                # まずはリサイズ判定（端にヒットしていればサイズ変更）
                l, r, t, b = self._hit_edges(event.position().toPoint())
                if l or r or t or b:
                    self._resizing = True
                    self._resize_left, self._resize_right = l, r
                    self._resize_top, self._resize_bottom = t, b
                    self._resize_start_geom = self.frameGeometry()
                    self._resize_start_mouse = event.globalPosition().toPoint()
                    event.accept()
                    return
                # 端でなければ移動ドラッグ
                self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                event.accept()
                return
        except Exception:
            pass
        return super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        try:
            # リサイズ中
            if self._resizing and (event.buttons() & Qt.LeftButton) and self._resize_start_geom is not None and self._resize_start_mouse is not None:
                start_g = self._resize_start_geom
                start_p = self._resize_start_mouse
                cur_p = event.globalPosition().toPoint()
                dx = cur_p.x() - start_p.x()
                dy = cur_p.y() - start_p.y()
                x, y, w, h = start_g.x(), start_g.y(), start_g.width(), start_g.height()
                min_w, min_h = 200, 420
                max_w, max_h = 1200, 1400
                if self._resize_left:
                    new_x = x + dx
                    new_w = w - dx
                    if new_w < min_w:
                        new_x = x + (w - min_w)
                        new_w = min_w
                    x, w = new_x, new_w
                if self._resize_right:
                    w = max(min_w, min(max_w, w + dx))
                if self._resize_top:
                    new_y = y + dy
                    new_h = h - dy
                    if new_h < min_h:
                        new_y = y + (h - min_h)
                        new_h = min_h
                    y, h = new_y, new_h
                if self._resize_bottom:
                    h = max(min_h, min(max_h, h + dy))
                # 反映
                self.setGeometry(x, y, int(w), int(h))
                self._manual_position = True
                event.accept()
                return
            # 通常の移動ドラッグ
            if event.buttons() & Qt.LeftButton and self._drag_offset is not None:
                new_pos = event.globalPosition().toPoint() - self._drag_offset
                self.move(new_pos)
                self._manual_position = True
                event.accept()
                return
            # ホバー時カーソル更新（端ならサイズカーソル）
            self._update_cursor_for_pos(event.position().toPoint())
        except Exception:
            pass
        return super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        try:
            if event.button() == Qt.LeftButton:
                if self._resizing:
                    # リサイズ終了
                    self._resizing = False
                    self._resize_left = self._resize_right = False
                    self._resize_top = self._resize_bottom = False
                    self._resize_start_geom = None
                    self._resize_start_mouse = None
                    self._manual_position = True
                    event.accept()
                    return
                # 移動ドラッグ終了
                self._drag_offset = None
                self._manual_position = True
                event.accept()
                return
        except Exception:
            pass
        return super().mouseReleaseEvent(event)

    def enterEvent(self, event):
        try:
            self._update_cursor_for_pos(self.mapFromGlobal(QCursor.pos()))
        except Exception:
            pass
        return super().enterEvent(event)

    def leaveEvent(self, event):
        try:
            self.setCursor(Qt.ArrowCursor)
        except Exception:
            pass
        return super().leaveEvent(event)

    def is_manual_position(self) -> bool:
        return bool(self._manual_position)

class Talker:
    def __init__(self) -> None:
        self.enabled: bool = True
        self.bubble = _Bubble()
        self._input = _InputBar()
        self._chat = _ChatWindow()
        self._chat_mode: bool = False
        self._input_anchor: str = "follow"  # "follow" | "screen_br"
        self._host: Optional[QWidget] = None
        self._screen_rect: Optional[QRect] = None
        self._auto_timer = QTimer()
        self._auto_timer.setSingleShot(True)
        self._auto_timer.timeout.connect(self._on_auto_timer)
        self._auto_min: float = 45.0
        self._auto_max: float = 120.0
        self._messages: List[str] = ["にゃーん"]
        self._last_petted_at: float = 0.0
        self._answer_max_chars: int = 220
        self._mem = MemoryStore()
        self._ask_thread: Optional[threading.Thread] = None
        self._ask_running: bool = False
        # Voice (press-to-talk via sounddevice)
        self._sd_stream = None
        self._sd_frames: list | None = None
        self._sd_samplerate: int = 16000
        # メインスレッドへ結果を渡すブリッジ（スレッド間シグナル）
        class _AsyncBridge(QObject):
            result = Signal(str, str)  # msg, user_text
            voice = Signal(str)        # recognized text
            ui_msg = Signal(str)       # show bubble with text
        self._bridge = _AsyncBridge()
        self._bridge.result.connect(self._on_ask_done)
        self._bridge.voice.connect(self._on_voice_text)
        self._bridge.ui_msg.connect(self._show_ui_message)
        self._ask_timeout: Optional[QTimer] = None
        self._ask_started_at: float = 0.0
        # RAG は未使用（小規模要約モード）
        # 入力バーの送信ハンドラ
        self._input.bind_send(lambda t: self.ask_user(t))
        self._input.bind_mic_press(lambda: self._voice_press())
        self._input.bind_mic_release(lambda: self._voice_release())
        # チャットウィンドウの送信ハンドラ
        self._chat.bind_send(lambda t: self.ask_user(t))
        self._chat.bind_mic_press(lambda: self._voice_press())
        self._chat.bind_mic_release(lambda: self._voice_release())

    # --- Unified entry point for user-initiated conversation ---
    def open_prompt(self, anchor: str = "screen_br") -> None:
        """
        開始UI（チャットモードならチャット、通常は入力バー）を1つだけ開く。
        """
        if not (self._host and self._screen_rect):
            return
        self.set_input_anchor(anchor)
        if self._chat_mode:
            # 確実に入力バーは閉じ、チャットのみ表示
            try:
                self._input.hide_bar()
            except Exception:
                pass
            # 履歴を表示（直近N件）
            try:
                turns = self._mem.recent_turns(int(load_config().get("llm", {}).get("context_turns", 10)) * 2)
                self._chat.populate_history(turns)
            except Exception:
                pass
            self._chat.set_busy(self._ask_running)
            self._chat.show_at(self._host.frameGeometry(), self._screen_rect, anchor=self._input_anchor)
            try:
                self._chat.scroll_to_bottom()
            except Exception:
                pass
            self._chat.focus_edit()
        else:
            # チャットは閉じて、入力バーのみ表示
            try:
                self._chat.hide_panel()
            except Exception:
                pass
            self._input.set_busy(self._ask_running)
            self._input.show_at(self._host.frameGeometry(), self._screen_rect, anchor=self._input_anchor)
            self._input.focus_edit()

    def bind(self, host: QWidget, screen_rect: QRect) -> None:
        self._host = host
        self._screen_rect = screen_rect
        self.apply_config()
        self._schedule_next_auto_talk()
        # 念のためバインド時にも非表示にしておく
        try:
            self._chat.hide_panel()
        except Exception:
            pass

    # --- Press-to-talk using sounddevice (no PyAudio) ---
    def _voice_press(self) -> None:
        try:
            import sounddevice as sd  # type: ignore
            import numpy as np  # type: ignore
            self._sd_frames = []
            self._sd_samplerate = 16000
            def _cb(indata, frames, time_info, status):
                try:
                    if self._sd_frames is not None:
                        self._sd_frames.append(indata.copy())
                except Exception:
                    pass
            self._sd_stream = sd.InputStream(
                samplerate=self._sd_samplerate,
                channels=1,
                dtype="int16",
                callback=_cb,
            )
            self._sd_stream.start()
            self._bridge.ui_msg.emit("録音中…（ボタンを離すと送信）")
        except Exception:
            self._bridge.ui_msg.emit("音声入力が利用できません。SpeechRecognition と sounddevice をインストールしてください。")

    def _voice_release(self) -> None:
        try:
            if self._sd_stream is not None:
                try:
                    self._sd_stream.stop()
                    self._sd_stream.close()
                except Exception:
                    pass
            import numpy as np  # type: ignore
            frames = self._sd_frames or []
            self._sd_frames = None
            if not frames:
                self._bridge.ui_msg.emit("音声が取得できませんでした。")
                return
            pcm = np.concatenate(frames, axis=0).astype(np.int16).tobytes()
            # Recognize in a worker thread
            def _recog_worker(pcm_bytes: bytes, sr: int):
                try:
                    import speech_recognition as srmod  # type: ignore
                except Exception:
                    self._bridge.ui_msg.emit("speech_recognition が見つかりません。pip でインストールしてください。")
                    return
                recog = srmod.Recognizer()
                audio = srmod.AudioData(pcm_bytes, sr, 2)
                try:
                    text = recog.recognize_google(audio, language="ja-JP")
                except Exception:
                    self._bridge.ui_msg.emit("音声認識に失敗しました。")
                    return
                if text:
                    self._bridge.voice.emit(text)
            import threading as _th
            _th.Thread(target=_recog_worker, args=(pcm, self._sd_samplerate), daemon=True).start()
        except Exception:
            self._bridge.ui_msg.emit("音声処理中にエラーが発生しました。")

    def _on_voice_text(self, text: str) -> None:
        # メインスレッドで ask_user を実行（UI操作を安全に）
        try:
            self.ask_user(text)
        except Exception:
            pass

    def _show_ui_message(self, message: str) -> None:
        try:
            if self._host and self._screen_rect:
                if self._chat_mode:
                    self._chat.append_message(message, role="system")
                    if not self._chat.is_visible():
                        self._chat.show_at(self._host.frameGeometry(), self._screen_rect, anchor=self._input_anchor)
                else:
                    self.bubble.show_message(message, self._host.frameGeometry(), self._screen_rect, msec=2500)
        except Exception:
            pass

    def on_hover(self) -> None:
        # LLM応答取得中、または他の吹き出し表示中はホバー発話を抑止
        if self._chat_mode:
            return
        if self.enabled and not self._ask_running and not self.bubble.isVisible() and self._host and self._screen_rect:
            self.bubble.show_message("にゃーん", self._host.frameGeometry(), self._screen_rect, msec=2000)

    def on_host_moved(self) -> None:
        # 吹き出しが表示中なら、ホスト移動に合わせて位置を追従させる
        if not (self._host and self._screen_rect):
            return
        try:
            host_rect = self._host.frameGeometry()
            screen_rect = self._screen_rect
            if self.bubble.isVisible():
                # 上寄せ、はみ出し時は下に
                x = host_rect.x() + 20
                y = host_rect.y() - self.bubble.height() - 10
                if y < screen_rect.top():
                    y = host_rect.bottom() + 10
                if x + self.bubble.width() > screen_rect.right():
                    x = max(screen_rect.right() - self.bubble.width() - 8, screen_rect.left())
                self.bubble.move(x, y)
            if self._input.is_visible() and self._input_anchor == "follow":
                self._input.show_at(host_rect, screen_rect, anchor=self._input_anchor)
            if self._chat.is_visible() and not self._chat.is_manual_position() and self._input_anchor == "follow":
                # 追従は follow のときのみ。微小移動は無視してチラつき抑制
                cur = self.frameGeometry()
                dx = abs(cur.x() - (host_rect.x() + 10))
                dy = abs(cur.y() - (host_rect.bottom() + 10 - self.height() - 10))
                if dx + dy > 2:
                    self._chat.show_at(host_rect, screen_rect, anchor=self._input_anchor)
        except Exception:
            pass

    def apply_config(self) -> None:
        cfg = load_config()
        talk = cfg.get("talk", {})
        self.enabled = bool(talk.get("enabled", True))
        self._chat_mode = bool(talk.get("chat_mode", False))
        # モードに応じて入力UIを片方だけ有効化（もう片方は必ず隠す）
        try:
            if self._chat_mode:
                self._input.hide_bar()
            else:
                self._chat.hide_panel()
        except Exception:
            pass
        # プロファイルのユーザー名が設定されていればメモリへ反映
        try:
            prof = cfg.get("profile", {})
            uname = str(prof.get("user_name", "") or "").strip()
            if uname:
                self._mem.set_user_name(uname)
        except Exception:
            pass
        base_min = float(talk.get("auto_talk_min_sec", 45))
        base_max = float(talk.get("auto_talk_max_sec", 120))
        self._auto_min = min(base_min, base_max)
        self._auto_max = max(base_min, base_max)
        msgs = talk.get("messages", None)
        if isinstance(msgs, list) and msgs:
            self._messages = [str(m) for m in msgs if isinstance(m, str)]
        if self.enabled:
            self._schedule_next_auto_talk()
        else:
            self._auto_timer.stop()

        net_cfg = load_config().get("net", {})
        self._answer_max_chars = int(net_cfg.get("answer_max_chars", 220))
        # chat window sizing refresh
        try:
            self._chat.apply_config()
        except Exception:
            pass

    def _truncate(self, s: str, limit: int) -> str:
        if len(s) <= limit:
            return s
        return s[: max(0, limit - 1)] + "…"

    # ルールベースの名前抽出・意図判定は撤廃（LLMによる動的判定に一本化）

    def ask_user(self, text: str) -> None:
        # 安全チェック
        allowed, reason = check_text_allowed(text)
        if not allowed and self._host and self._screen_rect:
            if hasattr(self, "_chat_mode") and self._chat_mode:
                try:
                    self._chat.append_message(reason or "この内容には対応できません。", role="system")
                    if not self._chat.is_visible():
                        self._chat.show_at(self._host.frameGeometry(), self._screen_rect, anchor=self._input_anchor)
                except Exception:
                    pass
            else:
                self.bubble.show_message(reason or "この内容には対応できません。", self._host.frameGeometry(), self._screen_rect, msec=3500)
            return
        # 記録
        self._mem.inc_counter("ask_count", 1)
        self._mem.add_query(text)
        self._mem.add_turn("user", text)

        # 名前/意図の判定は LLM 側で行う（_run_bg 内で処理）

        # 非同期で応答生成（UIスレッドを塞がない）。試行中は「…」を表示
        if self._ask_running:
            return
        self._ask_running = True
        self._ask_started_at = time.monotonic()
        if self.enabled and self._host and self._screen_rect:
            try:
                to_ms = int(load_config().get("net", {}).get("answer_timeout_ms", 45000))
            except Exception:
                to_ms = 45000
            if self._chat_mode:
                try:
                    self._chat.append_message(text, role="user")
                    # 送信直後に下端へ
                    try:
                        self._chat.scroll_to_bottom()
                    except Exception:
                        pass
                    if not self._chat.is_visible():
                        self._chat.show_at(self._host.frameGeometry(), self._screen_rect, anchor=self._input_anchor)
                except Exception:
                    pass
            else:
                self.bubble.show_message("…", self._host.frameGeometry(), self._screen_rect, msec=to_ms)
            # 入力UIは処理中は無効化
            try:
                self._input.set_busy(True)
                self._chat.set_busy(True)
            except Exception:
                pass

        def _run_bg(user_text: str) -> None:
            msg = ""
            try:
                cfg = load_config()
                # 時刻・位置のコンテキストを付与（任意）
                try:
                    ctx = cfg.get("context", {})
                    lines = []
                    include_time = bool(ctx.get("include_time", False))
                    if include_time:
                        try:
                            now_str = time.strftime("%Y-%m-%d %H:%M")
                            lines.append(f"現在時刻: {now_str}")
                        except Exception:
                            pass
                    if bool(ctx.get("include_location", True)):
                        loc = str(ctx.get("location_text", "") or "").strip()
                        if loc:
                            lines.append(f"現在地: {loc}")
                    if lines:
                        user_text = f"[コンテキスト] {' / '.join(lines)}\n{user_text}"
                except Exception:
                    pass
                if not bool(cfg.get("llm", {}).get("enabled", False)):
                    msg = "LLM_DISABLED"
                else:
                    r = self._ask_llm(user_text, web_context=None)
                    msg = r or "LLM_UNAVAILABLE"
            except Exception:
                msg = ""
            finally:
                try:
                    self._bridge.result.emit(msg, user_text)
                except Exception:
                    pass

        self._ask_thread = threading.Thread(target=_run_bg, args=(text,), daemon=True)
        self._ask_thread.start()
        # タイムアウトで自動フォールバック
        try:
            if self._ask_timeout is not None:
                self._ask_timeout.stop()
                self._ask_timeout.deleteLater()
            self._ask_timeout = QTimer()
            self._ask_timeout.setSingleShot(True)
            def _on_timeout():
                if not self._ask_running:
                    return
                cfg = load_config().get("net", {})
                to_ms = int(cfg.get("answer_timeout_ms", 45000))
                max_wait = int(cfg.get("answer_max_wait_ms", 180000))
                elapsed = int((time.monotonic() - self._ask_started_at) * 1000)
                if elapsed + to_ms <= max_wait:
                    # まだ待つ: 「…」を維持して再タイムアウトをセット
                    if self.enabled and self._host and self._screen_rect:
                        self.bubble.show_message("…", self._host.frameGeometry(), self._screen_rect, msec=to_ms)
                    self._ask_timeout.start(to_ms)
                else:
                    # さすがに諦める
                    self._on_ask_done("LLM_UNAVAILABLE", text)
            self._ask_timeout.timeout.connect(_on_timeout)
            to_ms = int(load_config().get("net", {}).get("answer_timeout_ms", 45000))
            self._ask_timeout.start(max(1000, to_ms))
        except Exception:
            pass
        return

    def _on_ask_done(self, msg: str, user_text: str) -> None:
        # タイムアウトタイマーの後始末
        try:
            if self._ask_timeout is not None:
                self._ask_timeout.stop()
                self._ask_timeout.deleteLater()
                self._ask_timeout = None
        except Exception:
            pass
        self._ask_running = False
        try:
            self._input.set_busy(False)
            self._chat.set_busy(False)
        except Exception:
            pass
        # メッセージ整形と表示・学習（UIスレッド）
        if not self.enabled or not self._host or not self._screen_rect:
            return
        cfg_now = load_config()
        if msg == "LLM_DISABLED":
            if self._chat_mode:
                self._chat.append_message("LLMが無効になっているよ。設定で llm.enabled を true にしてね。", role="system")
            else:
                self.bubble.show_message("LLMが無効になっているよ。設定で llm.enabled を true にしてね。", self._host.frameGeometry(), self._screen_rect, msec=3500)
            return
        if msg == "LLM_UNAVAILABLE" or not msg:
            if self._chat_mode:
                self._chat.append_message("いまLLMに接続できないみたい。LM Studioを起動して Serve をONにしてね。", role="system")
            else:
                self.bubble.show_message("いまLLMに接続できないみたい。LM Studioを起動して Serve をONにしてね。", self._host.frameGeometry(), self._screen_rect, msec=4000)
            return
        # 必ず日本語で返す（必要なら翻訳）
        try:
            msg = translate_to_japanese_if_needed(msg or "")
        except Exception:
            pass
        final_msg = self._truncate(msg, self._answer_max_chars)
        # 画面に出す前に内部メタ/制御文字列を除去
        def _sanitize_for_display(s: str) -> str:
            try:
                if not isinstance(s, str):
                    return ""
                t = s
                # コードフェンス（内部ログやコマンドなど）を丸ごと除去
                t = re.sub(r"```[\\s\\S]*?```", "", t, flags=re.MULTILINE)
                # <|channel|> や <|...|> のような内部タグを除去
                t = re.sub(r"<\\|[^>]*\\|>", "", t)
                # commentary to=..., to=repo_browser... などの内部行を除去
                lines = []
                for line in t.splitlines():
                    if re.search(r"(?:^|\\s)(commentary\\s+to=|to=|recipient_name|repo_browser|functions\\.)", line):
                        continue
                    lines.append(line)
                t = "\n".join(lines)
                # 余分な空行を圧縮
                t = re.sub(r"\\n{3,}", "\\n\\n", t).strip()
                return t
            except Exception:
                return s
        display_msg = _sanitize_for_display(final_msg)
        if not display_msg:
            try:
                display_msg = str(load_config().get("talk", {}).get("unknown_reply", "わかりません。"))
            except Exception:
                display_msg = "わかりません。"
        self._mem.add_turn("assistant", final_msg)
        # 内部プロンプトのエコーを画面に出さないフィルタ
        def _looks_internal_instruction(s: str) -> bool:
            t = s.strip().lower()
            bad = [
                "提供された発話から",
                "ユーザー本人に関する事実",
                "要約して、過去要約に統合",
                "過去の要約:",
            ]
            return any(k in t for k in bad)
        if _looks_internal_instruction(display_msg):
            # 学習用の内部応答はユーザーに見せず、代わりに「分からない」既定文を表示
            try:
                unknown = str(load_config().get("talk", {}).get("unknown_reply", "わかりません。"))
                if self._chat_mode:
                    self._chat.append_message(unknown, role="assistant")
                else:
                    self.bubble.show_message(unknown, self._host.frameGeometry(), self._screen_rect, msec=3500)
            except Exception:
                pass
            return
        if self._chat_mode:
            self._chat.append_message(display_msg, role="assistant")
        else:
            self.bubble.show_message(display_msg, self._host.frameGeometry(), self._screen_rect, msec=4500)
        # 学習・要約はバックグラウンドで実行（UIブロック回避）
        try:
            threading.Thread(target=self._post_learn, args=(user_text, final_msg), daemon=True).start()
        except Exception:
            pass

    def toggle_input_bar(self, show: bool) -> None:
        if not (self._host and self._screen_rect):
            return
        if self._chat_mode:
            if show:
                try:
                    self._chat.set_busy(self._ask_running)
                except Exception:
                    pass
                self._chat.show_at(self._host.frameGeometry(), self._screen_rect, anchor=self._input_anchor)
            else:
                self._chat.hide_panel()
        else:
            if show:
                self._input.set_busy(self._ask_running)
                self._input.show_at(self._host.frameGeometry(), self._screen_rect, anchor=self._input_anchor)
            else:
                self._input.hide_bar()

    def set_input_anchor(self, anchor: str) -> None:
        if anchor not in ("follow", "screen_br"):
            return
        self._input_anchor = anchor
        # 再配置
        if self._host and self._screen_rect and self._input.is_visible():
            self._input.show_at(self._host.frameGeometry(), self._screen_rect, anchor=self._input_anchor)
        if self._host and self._screen_rect and self._chat.is_visible():
            self._chat.show_at(self._host.frameGeometry(), self._screen_rect, anchor=self._input_anchor)
    
    def focus_input(self) -> None:
        try:
            if self._chat_mode:
                self._chat.focus_edit()
            else:
                self._input.focus_edit()
        except Exception:
            pass

    def _post_learn(self, user_text: str, final_msg: str) -> None:
        try:
            self._learn_from_turn(user_text, final_msg)
        except Exception:
            pass
        try:
            self._update_summary(user_text, final_msg)
        except Exception:
            pass

    def shutdown(self) -> None:
        """
        アプリ終了時に呼び出して、バックグラウンドスレッドを安全に停止する。
        """
        try:
            if self._ask_thread is not None and self._ask_thread.is_alive():
                # デーモンスレッドのため待たずに解放（アプリ終了を妨げない）
                self._ask_thread = None
        except Exception:
            pass

    def on_petted(self) -> None:
        now = time.monotonic()
        if now - self._last_petted_at < 3.0:
            return
        self._last_petted_at = now
        # 応答処理中や他の吹き出し表示中は抑止
        if self.enabled and (not self._ask_running) and (not self.bubble.isVisible()) and self._host and self._screen_rect:
            self.bubble.show_message("ごろごろ…気持ちいい〜", self._host.frameGeometry(), self._screen_rect, msec=2500)

    # --- LLM integration ---
    def _ask_llm(self, user_text: str, web_context: str | None = None) -> Optional[str]:
        cfg = load_config()
        llm_cfg = cfg.get("llm", {})
        if not bool(llm_cfg.get("enabled", False)):
            return None
        system_prompt = str(llm_cfg.get("system_prompt", ""))
        # 直近の会話 + 要約 + ユーザープロファイル/事実を渡す
        context_turns = int(llm_cfg.get("context_turns", 10))
        turns = self._mem.recent_turns(context_turns)
        summary = self._mem.get_summary()
        uname = self._mem.get_user_name()
        # facts/RAGは使わない（履歴と要約のみで判断）

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        # 文字数上限をLLMへ明示（吹き出し超過防止）
        try:
            if self._answer_max_chars and self._answer_max_chars > 0:
                messages.append({
                    "role": "system",
                    "content": f"回答は最大{self._answer_max_chars}文字以内にしてください。改行や箇条書きは必要最小限にし、簡潔な日本語で答えてください。"
                })
        except Exception:
            pass
        if summary:
            messages.append({"role": "system", "content": f"これまでの会話の要約:\n{summary}"})
        for t in turns:
            r = t.get("role"); c = t.get("content")
            if isinstance(r, str) and isinstance(c, str):
                messages.append({"role": r, "content": c})
        # RAG/外部Webコンテキストは付与しない
        messages.append({"role": "user", "content": user_text})
        reply = llm_chat(messages)
        return reply

    def _learn_from_turn(self, user_text: str, assistant_reply: Optional[str]) -> None:
        # facts/RAGによる学習は行わない（純要約モード）
        return

    def _update_summary(self, user_text: str, assistant_reply: Optional[str]) -> None:
        cfg = load_config()
        if not bool(cfg.get("learning", {}).get("summarize_enabled", True)):
            return
        llm_cfg = cfg.get("llm", {})
        # LLMが有効でない場合は簡易追記のみ
        if not bool(llm_cfg.get("enabled", False)):
            prev = self._mem.get_summary()
            add = f"・ユーザー: {user_text}\n"
            if assistant_reply:
                add += f"・エド: {assistant_reply}\n"
            self._mem.set_summary((prev + "\n" + add).strip())
            return
        # 直近ターンのみを対象に、短く要約（純要約モード）
        turns = self._mem.recent_turns(int(load_config().get("llm", {}).get("context_turns", 10)))
        convo_lines: List[str] = []
        for t in turns:
            r = str(t.get("role", "")); c = str(t.get("content", ""))
            if r and c:
                convo_lines.append(f"{r}: {c}")
        convo_text = "\n".join(convo_lines[-20:])
        # 設定の learning.max_summary_chars を上限として渡す（既定: 800）
        try:
            max_chars = int(cfg.get("learning", {}).get("max_summary_chars", 800))
        except Exception:
            max_chars = 800
        max_chars = max(120, min(4000, max_chars))
        sys = f"以下の会話を日本語で簡潔に要約してください。箇条書き可。最大{max_chars}字。内部指示は含めない。"
        msgs = [{"role": "system", "content": sys}, {"role": "user", "content": convo_text}]
        resp = llm_chat(msgs)
        if resp:
            self._mem.set_summary(resp.strip())

    # --- internal ---
    def _schedule_next_auto_talk(self) -> None:
        if not self.enabled:
            return
        delay = random.uniform(self._auto_min, self._auto_max)
        # 最小間隔の保護
        delay = max(3.0, delay)
        self._auto_timer.start(int(delay * 1000))

    def _on_auto_timer(self) -> None:
        if self.enabled and self._host and self._screen_rect:
            # すでに吹き出し表示中なら、自動トークは出さない
            if self.bubble.isVisible():
                self._schedule_next_auto_talk()
                return
            import random as _r
            msg = None
            try:
                rate = float(load_config().get("talk", {}).get("auto_talk_facts_rate", 0.0))
                rate = max(0.0, min(1.0, rate))
            except Exception:
                rate = 0.0
            # factsは使用しない
            if msg is None and self._messages:
                msg = random.choice(self._messages)
            if msg:
                if self._chat_mode:
                    self._chat.append_message(msg, role="assistant")
                    if not self._chat.is_visible():
                        self._chat.show_at(self._host.frameGeometry(), self._screen_rect, anchor=self._input_anchor)
                else:
                    self.bubble.show_message(msg, self._host.frameGeometry(), self._screen_rect, msec=3000)
        self._schedule_next_auto_talk()

    def raise_windows(self) -> None:
        try:
            if self.bubble.isVisible():
                self.bubble.raise_()
        except Exception:
            pass
        try:
            if self._input.is_visible():
                self._input.raise_()
        except Exception:
            pass
        try:
            if self._chat.is_visible():
                self._chat.raise_()
        except Exception:
            pass

