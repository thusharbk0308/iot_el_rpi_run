"""
recognize.py — Phase 3 thin wrapper.

Kept for backward compatibility. The full recognition pipeline now
lives in backend/recognition_engine.py and is also used by dashboard.py.

For the full dashboard, run:  python dashboard.py
For headless recognition only:  python recognize.py
"""
import signal
import sys
import config
from backend.database import Database
from backend.email_alert import EmailAlert
from backend import recognition_engine

db          = Database(config.DB_SQLITE_PATH)
email_alert = EmailAlert()

def shutdown(sig, frame):
    print("\n[INFO] Shutting down recognition engine…")
    recognition_engine.stop()
    sys.exit(0)

signal.signal(signal.SIGINT,  shutdown)
signal.signal(signal.SIGTERM, shutdown)

if __name__ == "__main__":
    servo = recognition_engine.start(db, email_alert)
    print("[INFO] Recognition engine running. Press Ctrl+C to stop.")
    print("[INFO] For the full dashboard, run: python dashboard.py")

    # Block main thread
    try:
        recognition_engine._thread.join()
    except Exception:
        pass
    finally:
        recognition_engine.stop()
        print("[INFO] Teardown complete.")
