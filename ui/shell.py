from __future__ import annotations

# DesktopMascot 実装（旧 mascot.py）
import sys
import math
import random
import time
from pathlib import Path
from PySide6.QtWidgets import QApplication, QWidget, QMenu, QDialog
from PySide6.QtGui import QPainter, QPixmap, QGuiApplication, QAction, QTransform
from PySide6.QtCore import Qt, QTimer, QPoint, QRect
from ui.chat import Talker
from agent.config import load_config
from settings import SettingsWindow

# 設定（起動時に読み込む）
CFG = load_config()


class DesktopMascot(QWidget):
    def __init__(self):
        super().__init__()

        # --- ウィンドウ設定（枠なし＋透過＋最前面） ---
        self.setWindowFlags(
            Qt.FramelessWindowHint |       # 枠・タイトルバーなし
            Qt.Tool |                       # タスクバーに出にくくする
            Qt.WindowStaysOnTopHint         # 常に最前面
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)

        # --- 画像読み込み（状態別フレーム） ---
        # 優先: ./material 以下（例: material/cat/*）。無ければスクリプト直下
        base_dir = Path(__file__).resolve().parent.parent  # ui/ からプロジェクト直下へ
        material_root = base_dir / "material"
        self.asset_root = material_root if material_root.exists() else base_dir
        self.sprites = self._load_sprites()
        # 現在フレーム
        self.state = "idle"  # "walk" | "idle" | "sleep"
        self.frame_index = 0
        self.current_pixmap = self.sprites[self.state][self.frame_index]
        self.resize(self.current_pixmap.size())

        # --- 画面情報 ---
        screen = QGuiApplication.primaryScreen()
        self.screen_rect: QRect = screen.availableGeometry()

        # 初期位置：画面下の方
        start_x = self.screen_rect.width() // 2
        start_y = self.screen_rect.height() - self.height() - 50
        self.move(start_x, start_y)

        # 位置・速度（簡易モードでは固定表示）
        self.pos_x = float(self.x())
        self.pos_y = float(self.y())
        # ベース移動速度（設定から）
        self.base_speed_px = float(CFG["mascot"].get("base_speed_px", 0.6))
        self.vx = 0.0
        self.vy = 0.0
        self.target_vx = 0.0
        self.target_vy = 0.0

        # --- 移動用タイマー ---
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_position)
        self.timer.start(int(CFG["mascot"]["timer_ms"]))

        # ランダムに進行方向を変えるタイマー
        self.random_timer = QTimer(self)
        self.random_timer.timeout.connect(self.update_velocity_randomly)
        self.schedule_next_velocity_change()

        # アニメーションフレーム更新タイマー
        self.anim_timer = QTimer(self)
        self.anim_timer.timeout.connect(self.advance_frame)
        self._reset_anim_timer_for_state(self.state)

        # 最終移動時刻
        self.last_moved_at = time.monotonic()

        # 向き（左向きなら True）。停止中は最後の向きを保持
        self.face_left = False
        self._mirror_cache: dict[int, QPixmap] = {}
        self._orient_sign = -1  # -1: 左, +1: 右（素材は左向き）
        self._last_orient_update_at = time.monotonic()

        # 睡眠開始時刻（未睡眠なら None）
        self.sleep_started_at: float | None = None

        # 吹き出しトーカー
        self.talker = Talker()
        self.talker.bind(self, self.screen_rect)
        # アプリ終了時にバックグラウンド処理を停止
        try:
            app = QGuiApplication.instance()
            if app is not None:
                app.aboutToQuit.connect(lambda: self.talker.shutdown())
        except Exception:
            pass

        self._drag_offset: QPoint | None = None
        self._dragging: bool = False
        # --- なで判定用 ---
        self.setMouseTracking(True)
        self._petting_distance_px: float = 0.0
        self._petting_last_t: float = time.monotonic()
        self._last_mouse_local: QPoint | None = None

        # --- 最前面維持（新規ウィンドウが出ても前面に保つ） ---
        self._ontop_timer = QTimer(self)
        self._ontop_timer.setInterval(1200)
        self._ontop_timer.timeout.connect(self._ensure_on_top)
        self._ontop_timer.start()
        try:
            QGuiApplication.focusWindowChanged.connect(lambda _w: self._ensure_on_top())
        except Exception:
            pass

    # 右クリックメニュー
    def contextMenuEvent(self, event):
        menu = QMenu(self)
        exit_action = QAction("終了", self)
        exit_action.triggered.connect(QApplication.instance().quit)
        menu.addAction(exit_action)
        # 「話しかける」系は入れ子にせずフラットに配置（おしゃべりトグルは設定へ移動）
        # 省電力モードは廃止（設定からも削除）
        # 手動で話しかける
        ask_action = QAction("話しかける...", self)
        def on_ask():
            # 入力欄を右下固定で直接表示し、フォーカスを当てる
            self.talker.enabled = True
            try:
                # モードに応じて1つだけ開く（統合ポイント）
                self.talker.open_prompt(anchor="screen_br")
            except Exception:
                pass
        ask_action.triggered.connect(on_ask)
        menu.addAction(ask_action)
        # 調べ物メニューは削除
        # 設定を再読込
        # 設定画面（保存時に即時反映）
        settings_action = QAction("設定...", self)
        def on_settings():
            dlg = SettingsWindow(self)
            if dlg.exec() == QDialog.Accepted:
                # 保存されたので設定を再読込して反映
                global CFG
                CFG = load_config(True)
                try:
                    self.timer.stop()
                    self.timer.start(int(CFG["mascot"]["timer_ms"]))
                except Exception:
                    pass
                try:
                    self._reset_anim_timer_for_state(self.state)
                except Exception:
                    pass
                try:
                    self.talker.apply_config()
                except Exception:
                    pass
        settings_action.triggered.connect(on_settings)
        menu.addAction(settings_action)
        menu.exec(event.globalPos())

    # 描画処理
    def paintEvent(self, event):
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
            # 透過ウィンドウでは前フレームが残るため毎回クリアする
            painter.setCompositionMode(QPainter.CompositionMode_Source)
            painter.fillRect(self.rect(), Qt.transparent)
            painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
            painter.drawPixmap(0, 0, self._get_draw_pixmap())
        finally:
            # 例外時でも必ず終了してバックバッファを壊さない
            painter.end()

    # 位置更新
    def update_position(self):
        # 吹き出し表示中は移動を止める
        try:
            if self.talker and self.talker.bubble.isVisible():
                # 設定で「吹き出し表示中は停止」かどうかを制御
                freeze = bool(CFG.get("talk", {}).get("freeze_while_bubble", False))
                if freeze:
                    self.vx = 0.0
                    self.vy = 0.0
                    return
        except Exception:
            # 何かあっても通常の更新にフォールバック
            pass
        # 掴み中は物理移動を停止（ドラッグによる手動移動のみ許可）
        if self._dragging:
            self.vx = 0.0
            self.vy = 0.0
            self.target_vx = 0.0
            self.target_vy = 0.0
            return
        # 速度を目標値へスムージングしてから位置を更新
        alpha = float(CFG["mascot"].get("velocity_smooth_alpha", 0.12))
        self.vx += (self.target_vx - self.vx) * alpha
        self.vy += (self.target_vy - self.vy) * alpha
        self.pos_x += self.vx
        self.pos_y += self.vy
        speed = math.hypot(self.vx, self.vy)
        if speed > float(CFG["mascot"]["speed_eps"]):
            self.last_moved_at = time.monotonic()
        # 横方向の向き更新（ヒステリシス）
        # 素材は左向き：右へ動く時だけ反転表示が必要
        th = float(CFG["mascot"].get("orientation_flip_threshold_px", 0.08))
        hold = float(CFG["mascot"].get("orientation_hold_ms", 250)) / 1000.0
        # 目標値ではなく現在速度で判定し、フリップ直前の違和感を低減
        if abs(self.vx) > th:
            desired = 1 if self.vx > 0 else -1
            if desired != self._orient_sign:
                now_t = time.monotonic()
                if now_t - self._last_orient_update_at >= hold:
                    self._orient_sign = desired
                    self._last_orient_update_at = now_t
        # face_left は「反転するか」のフラグとして使用（右向き=反転）
        self.face_left = (self._orient_sign == 1)

        # 画面端でバウンド（上下左右）
        left = self.screen_rect.left()
        top = self.screen_rect.top()
        right = self.screen_rect.right() - self.width()
        bottom = self.screen_rect.bottom() - self.height()

        if self.pos_x < left:
            self.pos_x = left
            self.vx = abs(self.vx)
            self.target_vx = abs(self.target_vx)
        elif self.pos_x > right:
            self.pos_x = right
            self.vx = -abs(self.vx)
            self.target_vx = -abs(self.target_vx)

        if self.pos_y < top:
            self.pos_y = top
            self.vy = abs(self.vy)
            self.target_vy = abs(self.target_vy)
        elif self.pos_y > bottom:
            self.pos_y = bottom
            self.vy = -abs(self.vy)
            self.target_vy = -abs(self.target_vy)

        self.move(int(self.pos_x), int(self.pos_y))
        self._update_state_from_motion(speed)
        self.current_pixmap = self.sprites[self.state][self.frame_index % len(self.sprites[self.state])]

    def schedule_next_velocity_change(self, interval_ms: int | None = None):
        if interval_ms is None:
            lo, hi = CFG["mascot"]["move_interval_ms"]
            interval_ms = random.randint(int(lo), int(hi))
        self.random_timer.start(interval_ms)

    def update_velocity_randomly(self):
        # 睡眠継続を優先させる（最短継続時間）
        if self.state == "sleep" and self.sleep_started_at is not None:
            now = time.monotonic()
            remain = float(CFG["mascot"]["sleep_min_duration_sec"]) - (now - self.sleep_started_at)
            if remain > 0:
                self.vx = 0.0
                self.vy = 0.0
                self.schedule_next_velocity_change(int(remain * 1000))
                return
        # 一定確率で立ち止まる
        if random.random() < float(CFG["mascot"]["stop_probability"]):
            self.target_vx = 0.0
            self.target_vy = 0.0
            lo, hi = CFG["mascot"]["stop_interval_ms"]
            next_ms = random.randint(int(lo), int(hi))
            self.schedule_next_velocity_change(next_ms)
            return
        # 通常はランダム方向に移動
        angle = random.uniform(0.0, 2.0 * math.pi)
        speed = random.uniform(self.base_speed_px * 0.6, self.base_speed_px * 1.4)
        self.target_vx = math.cos(angle) * speed
        self.target_vy = math.sin(angle) * speed
        lo2, hi2 = CFG["mascot"]["move_interval_ms"]
        next_ms = random.randint(int(lo2), int(hi2))
        self.schedule_next_velocity_change(next_ms)

    # アニメーションフレームを進める
    def advance_frame(self):
        self.frame_index = (self.frame_index + 1) % max(1, len(self.sprites[self.state]))
        self.current_pixmap = self.sprites[self.state][self.frame_index % len(self.sprites[self.state])]
        self.update()

    # 動きから状態を決める
    def _update_state_from_motion(self, speed: float):
        now = time.monotonic()
        # 掴み中は常に float を優先
        if self._dragging:
            if self.state != "float":
                self.state = "float"
                self.frame_index = 0
                self._reset_anim_timer_for_state(self.state)
            return
        next_state = self.state
        if speed > float(CFG["mascot"]["speed_eps"]):
            next_state = "walk"
        else:
            # 短時間の減速では idle にしない（瞬間的な潰れ防止）
            idle_hold = float(CFG["mascot"].get("idle_hold_ms", 250)) / 1000.0
            if now - self.last_moved_at >= float(CFG["mascot"]["sleep_idle_sec"]):
                next_state = "sleep"
            elif now - self.last_moved_at >= idle_hold:
                next_state = "idle"
            else:
                next_state = "walk"
        if next_state != self.state:
            self.state = next_state
            self.frame_index = 0
            self._reset_anim_timer_for_state(self.state)
            if self.state == "sleep":
                self.sleep_started_at = now
            else:
                self.sleep_started_at = None

    def _reset_anim_timer_for_state(self, state: str):
        self.anim_timer.start(int(CFG["mascot"]["anim_interval_ms"].get(state, 800)))

    # 現在の向きに応じた表示用ピクセルを取得
    def _get_draw_pixmap(self) -> QPixmap:
        base = self.current_pixmap
        if self.face_left:
            key = id(base)
            pm = self._mirror_cache.get(key)
            if pm is None:
                # QPixmap.mirrored が無い環境向けに QTransform を使用して左右反転
                t = QTransform().scale(-1, 1)
                pm = base.transformed(t, Qt.SmoothTransformation)
                self._mirror_cache[key] = pm
            return pm
        return base

    # 画像読込（存在するものだけ使用）
    def _load_sprites(self) -> dict[str, list[QPixmap]]:
        # 検索ルート決定（設定の sprite_dir があれば優先）
        base_dir = Path(__file__).resolve().parent.parent  # ui/ からプロジェクト直下へ
        sprite_dir_cfg = str(CFG.get("mascot", {}).get("sprite_dir", "") or "").strip()
        # 優先順位: 設定 > material/move_cat が存在するならそこ > 既定asset_root
        try:
            if sprite_dir_cfg:
                custom_root = Path(sprite_dir_cfg)
                if not custom_root.is_absolute():
                    custom_root = base_dir / custom_root
                if custom_root.exists():
                    search_root = custom_root
                else:
                    search_root = self.asset_root
            else:
                preferred = base_dir / "material" / "move_cat"
                search_root = preferred if preferred.exists() else self.asset_root
        except Exception:
            search_root = self.asset_root

        def natural_key(p: Path):
            import re
            s = str(p.relative_to(search_root)).lower()
            return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", s)]

        # 画像を読み、（必要ならトリミング後に）基準に従ってスケールしたピクセルを返す（キャンバス貼り付けはしない）
        def _scaled_pm_without_canvas(path: Path) -> QPixmap | None:
            src = QPixmap(str(path))
            if src.isNull():
                return None
            try:
                from PySide6.QtGui import QImage, QColor
                alpha_th = int(CFG["mascot"].get("sprite_trim_alpha_threshold", 8))
                if alpha_th > 0:
                    img = src.toImage().convertToFormat(QImage.Format_ARGB32)
                    w, h = img.width(), img.height()
                    min_x, min_y = w, h
                    max_x, max_y = -1, -1
                    for y in range(h):
                        for x in range(w):
                            if img.pixelColor(x, y).alpha() > alpha_th:
                                if x < min_x: min_x = x
                                if y < min_y: min_y = y
                                if x > max_x: max_x = x
                                if y > max_y: max_y = y
                    if max_x >= min_x and max_y >= min_y:
                        rect = QRect(min_x, min_y, (max_x - min_x + 1), (max_y - min_y + 1))
                        src = src.copy(rect)
            except Exception:
                pass
            basis = str(CFG["mascot"].get("sprite_scale_basis", "height")).lower()
            target = int(CFG["mascot"]["icon_size_px"])
            if basis == "width":
                return src.scaledToWidth(target, Qt.SmoothTransformation)
            return src.scaledToHeight(target, Qt.SmoothTransformation)

        # 固定キャンバス（設定値）
        png_files = sorted(search_root.rglob("*.png"), key=natural_key)
        canvas_w = int(CFG["mascot"].get("sprite_canvas_w_px", CFG["mascot"]["icon_size_px"]))
        canvas_h = int(CFG["mascot"].get("sprite_canvas_h_px", CFG["mascot"]["icon_size_px"]))

        def load_image_path(path: Path) -> QPixmap | None:
            pm = _scaled_pm_without_canvas(path)
            if pm is None:
                return None
            # はみ出しガード（等比でキャンバス内へ収め直す）
            if pm.width() > canvas_w or pm.height() > canvas_h:
                ratio = min(canvas_w / max(1, pm.width()), canvas_h / max(1, pm.height()))
                new_w = max(1, int(round(pm.width() * ratio)))
                new_h = max(1, int(round(pm.height() * ratio)))
                pm = pm.scaled(new_w, new_h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            # 共通キャンバスに底辺・中央寄せで配置
            canvas = QPixmap(canvas_w, canvas_h)
            canvas.fill(Qt.transparent)
            painter = QPainter(canvas)
            try:
                x = (canvas_w - pm.width()) // 2
                y = canvas_h - pm.height()
                painter.drawPixmap(x, y, pm)
            finally:
                painter.end()
            return canvas

        # ディレクトリ内の PNG を走査し、パス（フォルダ名含む）キーワードで状態を自動分類
        sprites: dict[str, list[QPixmap]] = {"walk": [], "idle": [], "sleep": [], "float": []}
        sleep_keywords = ("sleep", "break", "rest", "nap", "lie", "lying")
        idle_keywords = ("idle", "groom", "grooming", "sit", "sitting", "lick")
        # run は walk と混在させると高さが合わない素材が混ざりやすいので除外
        walk_keywords = ("walk", "move", "step", "stroll", "mascot")
        float_keywords = ("float",)

        # 指定フォルダ配下を再帰探索（例: material/move_cat/*.png）
        for p in png_files:
            path_label = str(p.relative_to(search_root)).lower()
            target_state: str | None = None
            if any(k in path_label for k in sleep_keywords):
                target_state = "sleep"
            elif any(k in path_label for k in idle_keywords):
                target_state = "idle"
            elif any(k in path_label for k in float_keywords):
                target_state = "float"
            elif any(k in path_label for k in walk_keywords):
                target_state = "walk"
            else:
                # 明確でないものは「座り」に相当する idle に寄せる
                target_state = "idle"
            pm = load_image_path(p)
            if pm is not None:
                sprites[target_state].append(pm)

        # フォールバック：一枚も無い状態があれば search_root 内の sit.png を優先、無ければ mascot.png
        sit_match = next(iter(search_root.rglob("sit.png")), None)
        mascot_match = next(iter(search_root.rglob("mascot.png")), None)
        fallback = (load_image_path(sit_match) if sit_match else None) or (load_image_path(mascot_match) if mascot_match else None)
        for state in sprites:
            if not sprites[state]:
                if fallback is not None:
                    sprites[state].append(fallback)
                else:
                    # 最低限、空にならないようダミー生成
                    dummy = QPixmap(canvas_w, canvas_h)
                    dummy.fill(Qt.transparent)
                    sprites[state].append(dummy)
        return sprites
    # 左クリックで掴んで移動
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self._dragging = True
            # 掴んだ瞬間に浮遊アニメへ切替
            try:
                self.state = "float"
                self.frame_index = 0
                self._reset_anim_timer_for_state(self.state)
            except Exception:
                pass
            # ドリフト防止のため速度をクリア
            self.vx = 0.0
            self.vy = 0.0
            self.target_vx = 0.0
            self.target_vy = 0.0
            event.accept()

    def mouseMoveEvent(self, event):
        # 掴んで移動
        if event.buttons() & Qt.LeftButton and self._drag_offset is not None:
            new_pos = event.globalPosition().toPoint() - self._drag_offset
            self.move(new_pos)
            # 内部座標も同期
            self.pos_x = float(self.x())
            self.pos_y = float(self.y())
            event.accept()
            return
        # なで判定（ボタン未押下でのカーソル移動）
        if not (event.buttons() & Qt.LeftButton):
            now = time.monotonic()
            window_sec = float(CFG.get("talk", {}).get("petting_window_sec", 1.2))
            if now - self._petting_last_t > window_sec:
                self._petting_distance_px = 0.0
                self._last_mouse_local = None
            local_pt = event.position().toPoint()
            if self.rect().contains(local_pt):
                if self._last_mouse_local is not None:
                    dx = local_pt.x() - self._last_mouse_local.x()
                    dy = local_pt.y() - self._last_mouse_local.y()
                    self._petting_distance_px += math.hypot(dx, dy)
                self._last_mouse_local = local_pt
                threshold = float(CFG.get("talk", {}).get("petting_threshold_px", 120.0))
                if self._petting_distance_px >= threshold:
                    self._petting_distance_px = 0.0
                    self._last_mouse_local = None
                    # 反応
                    try:
                        self.talker.on_petted()
                    finally:
                        # 起きるきっかけにもなる
                        self.sleep_started_at = None
                        self.state = "idle"
                        self._reset_anim_timer_for_state(self.state)
                self._petting_last_t = now
            else:
                self._last_mouse_local = None
                self._petting_distance_px = 0.0
        return super().mouseMoveEvent(event)

    # 最前面を維持（フォーカスは奪わない）
    def _ensure_on_top(self) -> None:
        try:
            self.raise_()
            # 関連ウィンドウ（吹き出し/入力/チャット）も前面へ
            try:
                self.talker.raise_windows()
            except Exception:
                pass
        except Exception:
            pass

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_offset = None
            self._dragging = False
            # 速度に応じて状態を再評価（基本は idle/walk/sleep に戻す）
            try:
                self._update_state_from_motion(math.hypot(self.vx, self.vy))
            except Exception:
                pass
            event.accept()

    # ホバーで吹き出し
    def enterEvent(self, event):
        try:
            self.talker.on_hover()
        except Exception:
            pass
        return super().enterEvent(event)

    def moveEvent(self, event):
        # Qtの移動イベントでも追従
        try:
            self.talker.on_host_moved()
        except Exception:
            pass
        return super().moveEvent(event)


__all__ = ["DesktopMascot"]

