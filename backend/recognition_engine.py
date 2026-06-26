"""
Recognition Engine — single source of truth for the FaceGuard recognition pipeline.

Exposes module-level shared state that dashboard.py reads live:
  output_frame, frame_lock, is_running, lock_status, system_state,
  fps, confirmed_person, confirmed_conf, confirm_progress,
  num_authorized_users, db_reload_requested

Public API:
  start(db, email_alert)  → starts background recognition thread
  stop()                  → signals loop to exit cleanly
  request_db_reload()     → hot-reload embeddings without restart
"""

import os
import cv2
import time
import threading
from datetime import datetime

import torch

# Root-level modules (unchanged from Phase 1/2)
from camera_stream import CameraStream
from face_engine import FaceEngine
from servo_controller import ServoController
import config

# ──────────────────────────────────────────────────────────────
# Shared state  (read by dashboard.py via import)
# ──────────────────────────────────────────────────────────────
output_frame          = None
frame_lock            = threading.Lock()
is_running            = False

lock_status           = "CLOSED"
system_state          = "LOCKED"      # LOCKED | CONFIRMING | UNLOCKED | WAITING_EXIT
fps                   = 0.0
confirmed_person      = None
confirmed_conf        = 0.0
confirm_progress      = 0.0           # 0.0–1.0, used by overlay & dashboard timer

num_authorized_users  = 0             # Updated on load/reload

db_reload_requested   = False         # Set True by dashboard to hot-reload embeddings

# ── Private references ─────────────────────────────────────────
_servo   = None
_thread  = None

# ── Disabled-user cache (refresh every 5 s) ───────────────────
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


# ──────────────────────────────────────────────────────────────
# Overlay drawing
# ──────────────────────────────────────────────────────────────
def _draw_overlay(frame, match_results, threshold_pct, conf_person, conf_start):
    """Draw bounding boxes and the status panel onto the frame."""
    font = cv2.FONT_HERSHEY_SIMPLEX

    for box, name, confidence in match_results:
        x1, y1, x2, y2 = map(int, box)
        authorized = (name != "Unknown" and confidence >= threshold_pct)
        color      = (0, 210, 90) if authorized else (60, 60, 230)
        label_name = name if authorized else "Unknown"
        label_text = "Access Granted" if authorized else "Access Denied"

        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
        ty     = y1 - 35 if y1 >= 35 else y2 + 5
        ry1    = y1 - 35 if y1 >= 35 else y2
        ry2    = y1      if y1 >= 35 else y2 + 35
        cv2.rectangle(frame, (x1, ry1), (x2, ry2), color, -1)
        cv2.putText(frame, f"{label_name} ({confidence:.1f}%)", (x1+5, ty+13), font, 0.42, (255,255,255), 1, cv2.LINE_AA)
        cv2.putText(frame, label_text,                          (x1+5, ty+29), font, 0.42, (255,255,255), 1, cv2.LINE_AA)

    # Status panel
    panel_h = 112 if system_state == "CONFIRMING" else 94
    cv2.rectangle(frame, (12, 12), (292, 12+panel_h), (22, 17, 11), -1)
    cv2.rectangle(frame, (12, 12), (292, 12+panel_h), (80, 65, 50),  1)

    WHITE = (250, 248, 245)
    y = 32

    cv2.putText(frame, f"FPS  :  {fps:.1f}",   (24, y), font, 0.45, WHITE, 1, cv2.LINE_AA); y += 18

    lc = (60, 210, 60) if lock_status == "OPEN" else (60, 60, 230)
    cv2.putText(frame, f"LOCK :  {lock_status}", (24, y), font, 0.45, lc, 1, cv2.LINE_AA); y += 18

    sc_map = {
        "LOCKED":       (60, 60, 230),
        "CONFIRMING":   (248, 180, 56),
        "UNLOCKED":     (60, 210, 60),
        "WAITING_EXIT": (60, 146, 251),
    }
    cv2.putText(frame, f"STATE:  {system_state}", (24, y), font, 0.45, sc_map.get(system_state, WHITE), 1, cv2.LINE_AA); y += 18

    if system_state == "CONFIRMING" and conf_start:
        elapsed = min(time.time() - conf_start, config.RECOGNITION_CONFIRM_TIME)
        cv2.putText(frame, f"Timer:  {elapsed:.1f}/{config.RECOGNITION_CONFIRM_TIME:.1f}s",
                    (24, y), font, 0.45, (60, 146, 251), 1, cv2.LINE_AA); y += 18

    cv2.putText(frame, f"Users:  {num_authorized_users}", (24, y), font, 0.45, WHITE, 1, cv2.LINE_AA)
    return frame


# ──────────────────────────────────────────────────────────────
# Recognition loop
# ──────────────────────────────────────────────────────────────
def _loop(db, email_alert):
    global output_frame, lock_status, system_state, fps
    global confirmed_person, confirmed_conf, confirm_progress
    global num_authorized_users, db_reload_requested, is_running

    # Load embeddings
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

    # Local state
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
            # Hot-reload embeddings if requested
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

            annotated = frame.copy()
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

            # ── 4-State Machine ────────────────────────────────
            thr = config.SIMILARITY_THRESHOLD * 100.0
            valid_auth = [m for m in match_results if m[1] != "Unknown" and m[2] >= thr]

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
                        _print_transition("UNLOCKED", confirm_person_local, conf_val)
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
                    _print_transition("WAITING_EXIT", confirm_person_local, 0)

            elif system_state == "WAITING_EXIT":
                lock_status      = "CLOSED"
                confirm_progress = 0.0
                _servo.close_lock()
                still_here = any(n == confirm_person_local for _, n, _ in match_results)
                if not still_here:
                    _print_transition("LOCKED_after_exit", confirm_person_local, 0)
                    system_state         = "LOCKED"
                    confirm_person_local = None
                    confirm_start_time   = None

            # Update shared state
            confirmed_person = confirm_person_local
            confirmed_conf   = next((c for _, n, c in match_results if n == confirm_person_local), 0.0) \
                               if confirm_person_local else 0.0

            # ── Unknown Snapshot ───────────────────────────────
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
                    _print_unknown(best_unk[2])
                except Exception as e:
                    print(f"[ENGINE ERROR] Snapshot save failed: {e}")

            # ── Terminal state-change prints ───────────────────
            state_key = f"{system_state}:{confirm_person_local}"
            if state_key != last_state_key:
                if system_state == "CONFIRMING" and confirm_person_local:
                    conf_v = next((c for _, n, c in match_results if n == confirm_person_local), 0.0)
                    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] LOCKED → CONFIRMING : {confirm_person_local} ({conf_v:.1f}%)")
                last_state_key = state_key

            # ── Overlay ────────────────────────────────────────
            annotated = _draw_overlay(annotated, match_results, thr, confirm_person_local, confirm_start_time)
            with frame_lock:
                output_frame = annotated.copy()

            time.sleep(max(0, 1.0 / config.CAMERA_FPS - (time.time() - now)))

    except Exception as e:
        print(f"\n[ENGINE CRITICAL] Crashed: {e}")
        is_running = False
    finally:
        print("[ENGINE] Loop terminated. Releasing camera...")
        cap.release()
        if _servo:
            _servo.cleanup()


def _print_transition(new_state, person, conf):
    t = datetime.now().strftime("%H:%M:%S")
    if new_state == "UNLOCKED":
        print(f"\n[{t}] CONFIRMING → UNLOCKED — {person} ({conf:.1f}%) — LOCK OPEN")
    elif new_state == "WAITING_EXIT":
        print(f"\n[{t}] UNLOCKED → WAITING_EXIT — LOCK CLOSED (cooldown)")
    elif new_state == "LOCKED_after_exit":
        print(f"\n[{t}] WAITING_EXIT → LOCKED — {person or 'user'} left frame")


def _print_unknown(conf):
    t = datetime.now().strftime("%H:%M:%S")
    print(f"\n[{t}] Unknown person detected ({conf:.1f}%) — snapshot saved")


# ──────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────
def start(db, email_alert):
    """Initialize servo and start recognition loop in a daemon thread."""
    global _servo, _thread, is_running
    is_running = True
    _servo = ServoController(pin=config.SERVO_GPIO_PIN)
    _thread = threading.Thread(target=_loop, args=(db, email_alert), daemon=True)
    _thread.start()
    return _servo


def stop():
    """Signal the recognition loop to exit."""
    global is_running
    is_running = False


def request_db_reload():
    """Request a hot-reload of the embeddings database on the next loop tick."""
    global db_reload_requested
    db_reload_requested = True
