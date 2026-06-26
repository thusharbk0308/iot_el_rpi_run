"""
FaceGuard Phase 3 — Main Dashboard Application
Run with:  python dashboard.py

Replaces recognize.py as the primary entry point.
All recognition logic lives in backend/recognition_engine.py.
"""

import os
import sys
import time
import shutil
import socket
import subprocess
import threading
import io
from datetime import datetime, timedelta
from functools import wraps

import cv2
import torch
import psutil
from flask import (Flask, Response, render_template, redirect, url_for,
                   request, session, flash, jsonify, send_file, abort)
from werkzeug.security import generate_password_hash, check_password_hash

import config
from backend.database import Database
from backend.email_alert import EmailAlert
from backend import recognition_engine

# ──────────────────────────────────────────────────────────────
# App init
# ──────────────────────────────────────────────────────────────
app = Flask(
    __name__,
    template_folder=os.path.join(os.path.dirname(__file__), "frontend", "templates"),
    static_folder  =os.path.join(os.path.dirname(__file__), "frontend", "static"),
)
app.secret_key              = config.DASHBOARD_SECRET_KEY
app.permanent_session_lifetime = timedelta(minutes=config.SESSION_TIMEOUT_MINUTES)

db          = Database(config.DB_SQLITE_PATH)
email_alert = EmailAlert()

# ──────────────────────────────────────────────────────────────
# Apply DB-stored settings over config defaults
# ──────────────────────────────────────────────────────────────
def _apply_db_settings():
    converters = {
        "SIMILARITY_THRESHOLD":    float,
        "RECOGNITION_CONFIRM_TIME":float,
        "SERVO_OPEN_DURATION":     float,
        "UNKNOWN_COOLDOWN":        float,
        "EMAIL_ALERTS_ENABLED":    lambda v: v == "True",
        "EMAIL_SENDER":            str,
        "EMAIL_PASSWORD":          str,
        "EMAIL_RECIPIENT":         str,
        "SMTP_SERVER":             str,
        "SMTP_PORT":               int,
        "CAMERA_FPS":              int,
        "DASHBOARD_SECRET_KEY":    str,
    }
    for key, cast in converters.items():
        val = db.get_setting(key)
        if val:
            try:
                setattr(config, key, cast(val))
            except Exception:
                pass

_apply_db_settings()

# ──────────────────────────────────────────────────────────────
# Default admin credentials (first run)
# ──────────────────────────────────────────────────────────────
if not db.get_setting("admin_username"):
    db.set_setting("admin_username", "admin")
    db.set_setting("admin_password_hash", generate_password_hash("admin123"))
    print("=" * 52)
    print("[DASHBOARD] First run — default credentials created")
    print("  Username : admin")
    print("  Password : admin123")
    print("  Please change your password in Settings!")
    print("=" * 52)

# Sync users from dataset/ folder
db.sync_users_from_dataset(config.DATASET_DIR)

# ──────────────────────────────────────────────────────────────
# Auth decorator
# ──────────────────────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "logged_in" not in session:
            if request.path.startswith("/api/"):
                return jsonify({"error": "Unauthorized"}), 401
            return redirect(url_for("login", next=request.url))
        # Inactivity timeout
        last = session.get("last_activity")
        if last:
            delta = (datetime.now() - datetime.fromisoformat(last)).total_seconds()
            timeout = getattr(config, "SESSION_TIMEOUT_MINUTES", 30) * 60
            if delta > timeout:
                session.clear()
                if request.path.startswith("/api/"):
                    return jsonify({"error": "Session expired"}), 401
                flash("Session expired. Please log in again.", "warning")
                return redirect(url_for("login"))
        session["last_activity"] = datetime.now().isoformat()
        return f(*args, **kwargs)
    return decorated

# ──────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────
def _get_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]; s.close(); return ip
    except Exception:
        return "127.0.0.1"

def _get_cpu_temp():
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return round(int(f.read()) / 1000.0, 1)
    except Exception:
        return None

# retrain job state
_retrain = {"running": False, "message": "", "success": None}

def _run_retrain():
    global _retrain
    _retrain = {"running": True, "message": "Retraining embeddings…", "success": None}
    try:
        result = subprocess.run(
            [sys.executable, "enroll.py"], capture_output=True, text=True, timeout=120
        )
        if result.returncode == 0:
            db.sync_users_from_dataset(config.DATASET_DIR)
            recognition_engine.request_db_reload()
            _retrain = {"running": False, "message": "Database retrained successfully.", "success": True}
        else:
            _retrain = {"running": False, "message": result.stderr[:300] or "Unknown error", "success": False}
    except Exception as e:
        _retrain = {"running": False, "message": str(e), "success": False}

# ──────────────────────────────────────────────────────────────
# Auth routes
# ──────────────────────────────────────────────────────────────
@app.route("/login", methods=["GET", "POST"])
def login():
    if "logged_in" in session:
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        stored_user = db.get_setting("admin_username")
        stored_hash = db.get_setting("admin_password_hash")
        if username == stored_user and check_password_hash(stored_hash, password):
            session.permanent = True
            session["logged_in"] = True
            session["username"]  = username
            session["last_activity"] = datetime.now().isoformat()
            return redirect(request.args.get("next") or url_for("dashboard"))
        flash("Invalid username or password.", "danger")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("login"))

# ──────────────────────────────────────────────────────────────
# Page routes
# ──────────────────────────────────────────────────────────────
@app.route("/")
@login_required
def dashboard():
    stats_today   = db.get_stats_today()
    stats_weekly  = db.get_stats_weekly()
    stats_monthly = db.get_stats_monthly()
    users         = db.get_users()
    return render_template("dashboard.html",
        num_users=len(users),
        stats_today=stats_today,
        stats_weekly=stats_weekly,
        stats_monthly=stats_monthly,
    )

@app.route("/camera")
@login_required
def camera():
    return render_template("camera.html")

@app.route("/video_feed")
@login_required
def video_feed():
    def gen():
        while True:
            with recognition_engine.frame_lock:
                frame = recognition_engine.output_frame
            if frame is None:
                time.sleep(0.05)
                continue
            ret, buf = cv2.imencode(".jpg", frame)
            if not ret:
                continue
            yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + buf.tobytes() + b"\r\n")
            time.sleep(1.0 / max(config.CAMERA_FPS, 1))
    return Response(gen(), mimetype="multipart/x-mixed-replace; boundary=frame")

@app.route("/unknown")
@login_required
def unknown():
    date_filter = request.args.get("date", "")
    faces = db.get_unknown_faces(date_filter)
    dates = db.get_unknown_dates()
    return render_template("unknown.html", faces=faces, dates=dates, selected_date=date_filter)

@app.route("/logs")
@login_required
def logs():
    page        = int(request.args.get("page", 1))
    search      = request.args.get("search", "")
    date_filter = request.args.get("date", "")
    rows, total = db.get_access_logs(page=page, per_page=25, search=search, date_filter=date_filter)
    total_pages = max(1, -(-total // 25))
    return render_template("logs.html",
        rows=rows, page=page, total_pages=total_pages,
        search=search, date_filter=date_filter, total=total)

@app.route("/users")
@login_required
def users():
    user_list = db.get_users()
    # Attach thumbnail path for each user
    for u in user_list:
        folder = os.path.join(config.DATASET_DIR, u["name"])
        imgs   = sorted([f for f in os.listdir(folder) if f.lower().endswith((".jpg",".jpeg",".png"))]) \
                 if os.path.isdir(folder) else []
        u["thumbnail"] = imgs[0] if imgs else None
    return render_template("users.html", users=user_list)

@app.route("/users/<name>")
@login_required
def user_detail(name):
    folder = os.path.join(config.DATASET_DIR, name)
    if not os.path.isdir(folder):
        abort(404)
    images = sorted([f for f in os.listdir(folder) if f.lower().endswith((".jpg",".jpeg",".png"))])
    users  = db.get_users()
    user   = next((u for u in users if u["name"] == name), {"name": name, "image_count": len(images), "enabled": 1})
    return render_template("user_detail.html", user=user, images=images)

@app.route("/system")
@login_required
def system():
    return render_template("system.html")

@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    if request.method == "POST":
        action = request.form.get("action", "save_settings")

        if action == "change_password":
            cur  = request.form.get("current_password", "")
            new1 = request.form.get("new_password", "")
            new2 = request.form.get("confirm_password", "")
            h    = db.get_setting("admin_password_hash")
            if not check_password_hash(h, cur):
                flash("Current password is incorrect.", "danger")
            elif new1 != new2:
                flash("New passwords do not match.", "danger")
            elif len(new1) < 6:
                flash("Password must be at least 6 characters.", "danger")
            else:
                db.set_setting("admin_password_hash", generate_password_hash(new1))
                flash("Password changed successfully.", "success")
        else:
            # Save all settings to DB and apply to config at runtime
            fields = [
                "SIMILARITY_THRESHOLD","RECOGNITION_CONFIRM_TIME","SERVO_OPEN_DURATION",
                "UNKNOWN_COOLDOWN","CAMERA_FPS","SERVO_GPIO_PIN","SMTP_SERVER","SMTP_PORT",
                "EMAIL_SENDER","EMAIL_PASSWORD","EMAIL_RECIPIENT",
            ]
            for key in fields:
                val = request.form.get(key)
                if val is not None:
                    db.set_setting(key, val)
            # Boolean toggle
            db.set_setting("EMAIL_ALERTS_ENABLED", str("EMAIL_ALERTS_ENABLED" in request.form))
            _apply_db_settings()
            flash("Settings saved successfully.", "success")

        return redirect(url_for("settings"))

    # GET — pass current effective config values to template
    cfg = {
        "SIMILARITY_THRESHOLD":    config.SIMILARITY_THRESHOLD,
        "RECOGNITION_CONFIRM_TIME":config.RECOGNITION_CONFIRM_TIME,
        "SERVO_OPEN_DURATION":     config.SERVO_OPEN_DURATION,
        "UNKNOWN_COOLDOWN":        config.UNKNOWN_COOLDOWN,
        "CAMERA_FPS":              config.CAMERA_FPS,
        "SERVO_GPIO_PIN":          config.SERVO_GPIO_PIN,
        "EMAIL_ALERTS_ENABLED":    config.EMAIL_ALERTS_ENABLED,
        "EMAIL_SENDER":            config.EMAIL_SENDER,
        "EMAIL_PASSWORD":          config.EMAIL_PASSWORD,
        "EMAIL_RECIPIENT":         config.EMAIL_RECIPIENT,
        "SMTP_SERVER":             config.SMTP_SERVER,
        "SMTP_PORT":               config.SMTP_PORT,
    }
    return render_template("settings.html", cfg=cfg)

# ──────────────────────────────────────────────────────────────
# Media serving
# ──────────────────────────────────────────────────────────────
@app.route("/media/dataset/<name>/<filename>")
@login_required
def serve_dataset_image(name, filename):
    path = os.path.abspath(os.path.join(config.DATASET_DIR, name, filename))
    if not os.path.exists(path):
        abort(404)
    return send_file(path)

@app.route("/media/unknown/<path:filepath>")
@login_required
def serve_unknown_image(filepath):
    path = os.path.abspath(filepath)
    if not os.path.exists(path):
        abort(404)
    return send_file(path)

# ──────────────────────────────────────────────────────────────
# API routes
# ──────────────────────────────────────────────────────────────
@app.route("/api/stats")
@login_required
def api_stats():
    stats = db.get_stats_today()
    temp  = _get_cpu_temp()
    return jsonify({
        "fps":               round(recognition_engine.fps, 1),
        "lock_status":       recognition_engine.lock_status,
        "system_state":      recognition_engine.system_state,
        "confirmed_person":  recognition_engine.confirmed_person,
        "confirmed_conf":    round(recognition_engine.confirmed_conf, 2),
        "confirm_progress":  round(recognition_engine.confirm_progress, 3),
        "authorized_users":  recognition_engine.num_authorized_users,
        "unlock_count":      stats["unlock_count"],
        "recognition_count": stats["recognition_count"],
        "unknown_count":     stats["unknown_count"],
        "cpu_percent":       psutil.cpu_percent(interval=None),
        "ram_percent":       psutil.virtual_memory().percent,
        "cpu_temp":          temp,
        "disk_percent":      psutil.disk_usage("/").percent,
    })

@app.route("/api/timeline")
@login_required
def api_timeline():
    return jsonify(db.get_recent_events(20))

@app.route("/api/chart/hourly")
@login_required
def api_chart_hourly():
    return jsonify(db.get_hourly_chart())

@app.route("/api/chart/weekly")
@login_required
def api_chart_weekly():
    return jsonify(db.get_weekly_chart())

@app.route("/api/delete_unknown", methods=["POST"])
@login_required
def api_delete_unknown():
    uid = request.json.get("id")
    if not uid:
        return jsonify({"error": "Missing id"}), 400
    img_path = db.delete_unknown(uid)
    if img_path and os.path.exists(img_path):
        try:
            os.remove(img_path)
        except Exception:
            pass
    return jsonify({"ok": True})

@app.route("/api/download_unknown/<int:uid>")
@login_required
def api_download_unknown(uid):
    faces = db.get_unknown_faces()
    face  = next((f for f in faces if f["id"] == uid), None)
    if not face or not os.path.exists(face["image_path"]):
        abort(404)
    return send_file(os.path.abspath(face["image_path"]), as_attachment=True)

@app.route("/api/set_user_enabled", methods=["POST"])
@login_required
def api_set_user_enabled():
    name    = request.json.get("name")
    enabled = request.json.get("enabled", True)
    if not name:
        return jsonify({"error": "Missing name"}), 400
    db.set_user_enabled(name, enabled)
    return jsonify({"ok": True, "enabled": enabled})

@app.route("/api/delete_user", methods=["POST"])
@login_required
def api_delete_user():
    name = request.json.get("name")
    if not name:
        return jsonify({"error": "Missing name"}), 400
    db.delete_user(name)
    folder = os.path.join(config.DATASET_DIR, name)
    if os.path.isdir(folder):
        shutil.rmtree(folder, ignore_errors=True)
    recognition_engine.request_db_reload()
    return jsonify({"ok": True})

@app.route("/api/delete_user_image", methods=["POST"])
@login_required
def api_delete_user_image():
    name     = request.json.get("name")
    filename = request.json.get("filename")
    if not name or not filename:
        return jsonify({"error": "Missing params"}), 400
    path = os.path.abspath(os.path.join(config.DATASET_DIR, name, filename))
    if os.path.exists(path):
        os.remove(path)
    db.sync_users_from_dataset(config.DATASET_DIR)
    return jsonify({"ok": True})

@app.route("/api/retrain", methods=["POST"])
@login_required
def api_retrain():
    if _retrain["running"]:
        return jsonify({"ok": False, "message": "Already running"})
    threading.Thread(target=_run_retrain, daemon=True).start()
    return jsonify({"ok": True})

@app.route("/api/retrain_status")
@login_required
def api_retrain_status():
    return jsonify(_retrain)

@app.route("/api/export_csv")
@login_required
def api_export_csv():
    tmp = "/tmp/access_log_export.csv"
    db.export_csv(tmp)
    return send_file(tmp, as_attachment=True,
                     download_name=f"access_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv")

@app.route("/api/system")
@login_required
def api_system():
    temp     = _get_cpu_temp()
    vm       = psutil.virtual_memory()
    disk     = psutil.disk_usage("/")
    uptime_s = int(time.time() - psutil.boot_time())
    h, rem   = divmod(uptime_s, 3600)
    m, s     = divmod(rem, 60)
    return jsonify({
        "cpu_percent":  psutil.cpu_percent(interval=0.5),
        "ram_percent":  vm.percent,
        "ram_used_mb":  round(vm.used / 1024**2),
        "ram_total_mb": round(vm.total / 1024**2),
        "disk_percent": disk.percent,
        "disk_used_gb": round(disk.used / 1024**3, 1),
        "disk_total_gb":round(disk.total / 1024**3, 1),
        "cpu_temp":     temp,
        "uptime":       f"{h}h {m}m {s}s",
        "ip":           _get_ip(),
        "python":       sys.version.split()[0],
        "db_size_kb":   round(os.path.getsize(config.DB_SQLITE_PATH) / 1024, 1)
                        if os.path.exists(config.DB_SQLITE_PATH) else 0,
        "engine_running": recognition_engine.is_running,
        "servo_state":    recognition_engine.lock_status,
        "fps":            round(recognition_engine.fps, 1),
    })

# ──────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import logging
    logging.getLogger("werkzeug").setLevel(logging.ERROR)

    if not os.path.exists(config.DB_PATH):
        print(f"[WARNING] Embeddings not found at {config.DB_PATH}")
        print("[WARNING] Face recognition will be disabled until you run: python enroll.py")

    # Start recognition engine
    recognition_engine.start(db, email_alert)

    ip = _get_ip()
    print("=" * 52)
    print("FaceGuard Security Dashboard — Phase 3")
    print(f"  Model       : FaceNet (CPU)")
    print(f"  Database    : {config.DB_SQLITE_PATH}")
    servo_mode = "READY" if recognition_engine._servo and recognition_engine._servo.is_hardware_active() else "SIMULATION"
    print(f"  GPIO Servo  : {servo_mode}")
    print(f"  Dashboard   : http://{ip}:{config.DASHBOARD_PORT}")
    print(f"  Login       : admin / admin123  (change in Settings)")
    print("=" * 52)

    try:
        app.run(host="0.0.0.0", port=config.DASHBOARD_PORT,
                threaded=True, use_reloader=False)
    except KeyboardInterrupt:
        print("\n[INFO] Shutting down…")
    finally:
        recognition_engine.stop()
        print("[INFO] Teardown complete. Goodbye.")
