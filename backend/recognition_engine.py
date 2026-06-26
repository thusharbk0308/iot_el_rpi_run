"""
Recognition Engine — single source of truth for the FaceGuard recognition pipeline.
Shared state accessible by dashboard.py via import.

Manual Mode:
  Set manual_mode = True to bypass face recognition and control servo directly.
  Set manual_lock_state = "OPEN" or "CLOSED" while in manual mode.
  Set manual_mode = False to return to auto (face recognition) mode.
"""

import os
import cv2
import time
import threading
from datetime import datetime

import torch

from camera_stream import CameraStream
from face_engine import FaceEngine
from servo_controller import ServoController
import config

# ── Shared state (read by dashboard.py) ───────────────────────
output_frame          = None
frame_lock            = threading.Lock()
is_running            = False

lock_status           = "CLOSED"
system_state          = "LOCKED"      # LOCKED | CONFIRMING | UNLOCKED | WAITING_EXIT | MANUAL_OPEN | MANUAL_LOCKED
fps                   = 0.0
confirmed_person      = None
confirmed_conf        = 0.0
confirm_progress      = 0.0

num_authorized_users  = 0
db_reload_requested   = False

# ── Manual mode ─────────────────────────────────────────────────
manual_mode           = False         # True = manual override, False = auto
manual_lock_state     = "CLOSED"      # "OPEN" | "CLOSED" — effective only when manual_mode=True

# ── Private ────────────────────────────────────────────────────
_servo   = None
_thread  = None
_disabled_cache      = set()
_disabled_cache_time = 0.0


def _refresh_disabled(db):
    global _disabled_cache, _disabled_cache_time
    if time.time() - _disabled_cache_time > 5.0:
        try:
            _disabled_cache = {u["name"] for u in db.get_users() if not u["enabled"]}
            _disabled_cache_time = time.time()
        except Exception:
            pass
    return _disabled_cache


# ── Overlay ────────────────────────────────────────────────────
def _draw_overlay(frame, match_results, threshold_pct, conf_person, conf_start):
    font = cv2.FONT_HERSHEY_SIMPLEX

    for box, name, confidence in match_results:
        x1, y1, x2, y2 = map(int, box)
        authorized = (name != "Unknown" and confidence >= threshold_pct)
        color = (0, 210, 90) if authorized else (60, 60, 230)
        label_name = name if authorized else "Unknown"
        label_text = "Access Granted" if authorized else "Access Denied"
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
        ty  = y1 - 35 if y1 >= 35 else y2 + 5
        ry1 = y1 - 35 if y1 >= 35 else y2
        ry2 = y1      if y1 >= 35 else y2 + 35
        cv2.rectangle(frame, (x1, ry1), (x2, ry2), color, -1)
        cv2.putText(frame, f"{label_name} ({confidence:.1f}%)", (x1+5, ty+13), font, 0.42, (255,255,255), 1, cv2.LINE_AA)
        cv2.putText(frame, label_text, (x1+5, ty+29), font, 0.42, (255,255,255), 1, cv2.LINE_AA)

    panel_h = 112 if system_state == "CONFIRMING" else 94
    cv2.rectangle(frame, (12, 12), (292, 12+panel_h), (12, 12, 12), -1)
    cv2.rectangle(frame, (12, 12), (292, 12+panel_h), (50, 50, 50), 1)

    WHITE = (240, 240, 240)
    y = 32
    cv2.putText(frame, f"FPS  :  {fps:.1f}", (24, y), font, 0.45, WHITE, 1, cv2.LINE_AA); y += 18

    lc = (60, 210, 60) if lock_status == "OPEN" else (60, 60, 220)
    cv2.putText(frame, f"LOCK :  {lock_status}", (24, y), font, 0.45, lc, 1, cv2.LINE_AA); y += 18

    sc_map = {
        "LOCKED":       (60, 60, 220),
        "CONFIRMING":   (50, 180, 240),
        "UNLOCKED":     (60, 210, 60),
        "WAITING_EXIT": (60, 146, 251),
        "MANUAL_OPEN":  (60, 210, 60),
        "MANUAL_LOCKED":(180, 60, 60),
    }
    cv2.putText(frame, f"STATE:  {system_state}", (24, y), font, 0.45,
                sc_map.get(system_state, WHITE), 1, cv2.LINE_AA); y += 18

    if system_state == "CONFIRMING" and conf_start:
        elapsed = min(time.time() - conf_start, config.RECOGNITION_CONFIRM_TIME)
        cv2.putText(frame, f"Timer:  {elapsed:.1f}/{config.RECOGNITION_CONFIRM_TIME:.1f}s",
                    (24, y), font, 0.45, (50, 180, 240), 1, cv2.LINE_AA); y += 18

    mode_label = "MANUAL" if manual_mode else "AUTO"
    cv2.putText(frame, f"MODE :  {mode_label}  |  Users: {num_authorized_users}",
                (24, y), font, 0.42, WHITE, 1, cv2.LINE_AA)
    return frame


# ── Recognition loop ───────────────────────────────────────────
def _loop(db, email_alert):
    global output_frame, lock_status, system_state, fps
    global confirmed_person, confirmed_conf, confirm_progress
    global num_authorized_users, db_reload_requested, is_running

    try:
        database = torch.load(config.DB_PATH, weights_only=False)
        num_authorized_users = len(database)
    except Exception as e:
        print(f"[ENGINE ERROR] Failed to load embeddings: {e}")
        is_running = False
        return

    engine = FaceEngine()
    cap    = CameraStream(0)
    if not cap.isOpened():
        print("[ENGINE ERROR] Camera could not be opened.")
        is_running = False
        return

    confirm_person_local = None
    confirm_start_time   = None
    unlock_start_time    = None
    last_snapshot_time   = 0.0
    last_state_key       = "INIT"
    prev_time            = time.time()
    fps_acc              = 0.0
    failures             = 0

    try:
        while is_running:
            # Hot-reload
            if db_reload_requested:
                db_reload_requested = False
                try:
                    database = torch.load(config.DB_PATH, weights_only=False)
                    num_authorized_users = len(database)
                    print(f"[ENGINE] Database reloaded — {num_authorized_users} user(s)")
                except Exception as e:
                    print(f"[ENGINE ERROR] Reload failed: {e}")

            ret, frame = cap.read()
            if not ret:
                failures += 1
                if failures > 30:
                    print("[ENGINE ERROR] Camera stopped responding.")
                    is_running = False
                    break
                time.sleep(0.05)
                continue
            failures = 0

            now = time.time()
            dt  = now - prev_time
            prev_time = now
            if dt > 0:
                fps_acc = 0.9 * fps_acc + 0.1 / dt if fps_acc > 0 else 1.0 / dt
            fps = fps_acc

            annotated    = frame.copy()
            boxes, probs, landmarks = engine.detect_faces(frame)
            match_results = []
            disabled = _refresh_disabled(db)

            if boxes is not None and len(boxes) > 0:
                for box, prob, lm in zip(boxes, probs, landmarks):
                    try:
                        aligned   = engine.align_face(frame, box, lm)
                        embedding = engine.get_embedding(aligned)
                        name, conf = engine.compare_embeddings(embedding, database)
                        if name in disabled:
                            name = "Unknown"
                        match_results.append((box, name, conf))
                    except Exception:
                        pass

            thr = config.SIMILARITY_THRESHOLD * 100.0

            # ── MANUAL MODE ────────────────────────────────────
            if manual_mode:
                confirm_progress = 1.0 if manual_lock_state == "OPEN" else 0.0
                confirmed_person = "Manual Override" if manual_lock_state == "OPEN" else None
                confirmed_conf   = 100.0 if manual_lock_state == "OPEN" else 0.0
                confirm_person_local = None
                confirm_start_time   = None

                if manual_lock_state == "OPEN":
                    lock_status  = "OPEN"
                    system_state = "MANUAL_OPEN"
                    _servo.open_lock()
                else:
                    lock_status  = "CLOSED"
                    system_state = "MANUAL_LOCKED"
                    _servo.close_lock()

            # ── AUTO MODE — 4-state machine ────────────────────
            else:
                valid_auth = [m for m in match_results if m[1] != "Unknown" and m[2] >= thr]

                if system_state in ("MANUAL_OPEN", "MANUAL_LOCKED"):
                    system_state         = "LOCKED"
                    confirm_person_local = None
                    confirm_start_time   = None

                if system_state == "LOCKED":
                    lock_status      = "CLOSED"
                    confirm_progress = 0.0
                    _servo.close_lock()
                    if valid_auth:
                        best = max(valid_auth, key=lambda x: x[2])
                        system_state         = "CONFIRMING"
                        confirm_person_local = best[1]
                        confirm_start_time   = now

                elif system_state == "CONFIRMING":
                    lock_status = "CLOSED"
                    _servo.close_lock()
                    same = any(n == confirm_person_local and c >= thr for _, n, c in match_results)
                    if same:
                        elapsed          = now - confirm_start_time
                        confirm_progress = min(elapsed / config.RECOGNITION_CONFIRM_TIME, 1.0)
                        if elapsed >= config.RECOGNITION_CONFIRM_TIME:
                            system_state      = "UNLOCKED"
                            unlock_start_time = now
                            lock_status       = "OPEN"
                            _servo.open_lock()
                            conf_val = next((c for _, n, c in match_results if n == confirm_person_local), 0.0)
                            db.log_access(confirm_person_local, conf_val, "GRANTED", "OPEN")
                            print(f"[{datetime.now().strftime('%H:%M:%S')}] UNLOCKED → {confirm_person_local} ({conf_val:.1f}%)")
                    else:
                        confirm_progress     = 0.0
                        system_state         = "LOCKED"
                        confirm_person_local = None
                        if valid_auth:
                            best                 = max(valid_auth, key=lambda x: x[2])
                            system_state         = "CONFIRMING"
                            confirm_person_local = best[1]
                            confirm_start_time   = now

                elif system_state == "UNLOCKED":
                    lock_status      = "OPEN"
                    confirm_progress = 1.0
                    _servo.open_lock()
                    if now - unlock_start_time >= config.SERVO_OPEN_DURATION:
                        system_state = "WAITING_EXIT"
                        lock_status  = "CLOSED"
                        _servo.close_lock()

                elif system_state == "WAITING_EXIT":
                    lock_status      = "CLOSED"
                    confirm_progress = 0.0
                    _servo.close_lock()
                    if not any(n == confirm_person_local for _, n, _ in match_results):
                        system_state         = "LOCKED"
                        confirm_person_local = None

                confirmed_person = confirm_person_local
                confirmed_conf   = next((c for _, n, c in match_results if n == confirm_person_local), 0.0) \
                                   if confirm_person_local else 0.0

                # Unknown snapshots (auto mode only)
                unk = [m for m in match_results if m[1] == "Unknown"]
                if unk and system_state == "LOCKED" and now - last_snapshot_time >= config.UNKNOWN_COOLDOWN:
                    last_snapshot_time = now
                    best_unk = max(unk, key=lambda x: x[2])
                    date_s   = datetime.now().strftime("%Y-%m-%d")
                    time_s   = datetime.now().strftime("%H-%M-%S")
                    snap_dir = os.path.join(config.UNKNOWN_SNAPSHOT_DIR, date_s)
                    os.makedirs(snap_dir, exist_ok=True)
                    snap_path = os.path.join(snap_dir, f"unknown_{time_s}.jpg")
                    try:
                        cv2.imwrite(snap_path, frame)
                        db.log_unknown(snap_path, best_unk[2])
                        db.log_access("Unknown", best_unk[2], "DENIED", "CLOSED")
                        if email_alert:
                            email_alert.send(snap_path, best_unk[2])
                        print(f"[{datetime.now().strftime('%H:%M:%S')}] Unknown ({best_unk[2]:.1f}%) — snapshot saved")
                    except Exception as e:
                        print(f"[ENGINE ERROR] Snapshot save failed: {e}")

                # CONFIRMING print
                state_key = f"{system_state}:{confirm_person_local}"
                if state_key != last_state_key:
                    if system_state == "CONFIRMING" and confirm_person_local:
                        cv = next((c for _, n, c in match_results if n == confirm_person_local), 0.0)
                        print(f"[{datetime.now().strftime('%H:%M:%S')}] CONFIRMING → {confirm_person_local} ({cv:.1f}%)")
                    last_state_key = state_key

            # Overlay + shared frame
            annotated = _draw_overlay(annotated, match_results, thr, confirm_person_local, confirm_start_time)
            with frame_lock:
                output_frame = annotated.copy()

            time.sleep(max(0, 1.0 / config.CAMERA_FPS - (time.time() - now)))

    except Exception as e:
        print(f"\n[ENGINE CRITICAL] Crashed: {e}")
        import traceback; traceback.print_exc()
        is_running = False
    finally:
        print("[ENGINE] Loop terminated. Releasing camera…")
        cap.release()
        if _servo:
            _servo.cleanup()


# ── Public API ─────────────────────────────────────────────────
def start(db, email_alert):
    global _servo, _thread, is_running
    is_running = True
    _servo = ServoController(pin=config.SERVO_GPIO_PIN)
    _thread = threading.Thread(target=_loop, args=(db, email_alert), daemon=True)
    _thread.start()
    return _servo


def stop():
    global is_running
    is_running = False


def request_db_reload():
    global db_reload_requested
    db_reload_requested = True


def set_manual_unlock():
    """Enter manual mode and open the lock."""
    global manual_mode, manual_lock_state
    manual_mode       = True
    manual_lock_state = "OPEN"


def set_manual_lock():
    """Enter manual mode and close the lock."""
    global manual_mode, manual_lock_state
    manual_mode       = True
    manual_lock_state = "CLOSED"


def set_auto_mode():
    """Return to automatic face recognition mode."""
    global manual_mode, manual_lock_state
    manual_mode       = False
    manual_lock_state = "CLOSED"
