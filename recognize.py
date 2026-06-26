import os
import cv2
import time
import socket
import csv
import sys
import signal
import threading
from datetime import datetime
import torch
from flask import Flask, Response, render_template_string
from camera_stream import CameraStream
from face_engine import FaceEngine
from servo_controller import ServoController
import config

# Initialize Flask app
app = Flask(__name__)

# Global variables for cross-thread communication
output_frame = None
frame_lock = threading.Lock()
is_running = True
lock_status = "CLOSED"
system_state = "LOCKED"

# Initialize Servo Controller
servo = ServoController(pin=config.SERVO_GPIO_PIN)

# HTML Template with dark modern aesthetics matching our guidelines
HTML_PAGE = """
<!DOCTYPE html>
<html>
<head>
    <title>Face Recognition Access Control</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body {
            font-family: 'Outfit', 'Inter', -apple-system, sans-serif;
            background-color: #0f172a;
            color: #f8fafc;
            margin: 0;
            padding: 0;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            min-height: 100vh;
        }
        .header {
            text-align: center;
            margin-bottom: 24px;
        }
        h1 {
            color: #38bdf8;
            margin: 0;
            font-size: 2rem;
            font-weight: 700;
            letter-spacing: -0.025em;
        }
        p {
            color: #94a3b8;
            margin-top: 8px;
            font-size: 1rem;
        }
        .container {
            background-color: #1e293b;
            padding: 24px;
            border-radius: 16px;
            box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.5), 0 8px 10px -6px rgba(0, 0, 0, 0.5);
            text-align: center;
            border: 1px solid #334155;
            max-width: 680px;
            width: 90%;
        }
        .video-wrapper {
            position: relative;
            border-radius: 12px;
            overflow: hidden;
            border: 2px solid #475569;
            background-color: #020617;
            aspect-ratio: 4/3;
        }
        img {
            width: 100%;
            height: 100%;
            object-fit: cover;
            display: block;
        }
        .footer {
            margin-top: 16px;
            color: #64748b;
            font-size: 0.875rem;
        }
    </style>
</head>
<body>
    <div class="header">
        <h1>Face Recognition Access Control</h1>
        <p>Live Monitoring System</p>
    </div>
    <div class="container">
        <div class="video-wrapper">
            <img src="/video_feed" alt="Live Video Feed">
        </div>
        <div class="footer">
            Device: Raspberry Pi 4 | Model: FaceNet (CPU)
        </div>
    </div>
</body>
</html>
"""

def get_ip_address():
    """
    Finds the primary IP address of the local machine to print on startup.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # Doesn't need to be reachable, just triggers OS interface lookup
        s.connect(('10.255.255.255', 1))
        ip = s.getsockname()[0]
    except Exception:
        ip = '127.0.0.1'
    finally:
        s.close()
    return ip

def log_event(name, confidence, result, lock_state):
    """
    Appends an access control transaction log to CSV file.
    """
    os.makedirs(config.LOGS_DIR, exist_ok=True)
    file_exists = os.path.exists(config.ACCESS_LOG_PATH)
    try:
        with open(config.ACCESS_LOG_PATH, mode='a', newline='') as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(["Timestamp", "Name", "Confidence", "Result", "Lock Status"])
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            writer.writerow([timestamp, name, f"{confidence:.2f}%", result, lock_state])
    except Exception as e:
        print(f"[ERROR] Failed to write event to access log: {e}")

@app.route('/')
def index():
    return render_template_string(HTML_PAGE)

def gen_frames():
    """
    Generator that yields JPEG byte streams of annotated frames for Flask.
    """
    global output_frame
    while True:
        with frame_lock:
            if output_frame is None:
                time.sleep(0.01)
                continue
            ret, encoded_image = cv2.imencode('.jpg', output_frame)
            if not ret:
                continue
            frame_bytes = encoded_image.tobytes()
            
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
        # Rate-limiting MJPEG generation
        time.sleep(1.0 / config.CAMERA_FPS)

@app.route('/video_feed')
def video_feed():
    return Response(gen_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')

def recognition_loop():
    """
    Orchestration thread: continuously reads frames, performs face alignment,
    embedding generation, database matching, draws overlays, and manages system state.
    """
    global output_frame, lock_status, system_state, is_running
    
    # Load database
    try:
        database = torch.load(config.DB_PATH)
        num_users = len(database)
    except Exception as e:
        print(f"[ERROR] Failed to load database: {e}")
        is_running = False
        os.kill(os.getpid(), signal.SIGINT)
        return
        
    engine = FaceEngine()
    cap = CameraStream(0)
    
    if not cap.isOpened():
        print("[ERROR] Camera stream could not be opened.")
        is_running = False
        os.kill(os.getpid(), signal.SIGINT)
        return
        
    # State tracking variables
    confirm_person = None
    confirm_start_time = None
    unlock_start_time = None
    last_snapshot_time = 0.0
    last_printed_state = "LOCKED_START"
    
    prev_time = time.time()
    fps = 0.0
    consecutive_failures = 0
    
    try:
        while is_running:
            ret, frame = cap.read()
            if not ret:
                consecutive_failures += 1
                if consecutive_failures > 10:
                    print("[ERROR] Camera stream stopped unexpectedly.")
                    is_running = False
                    break
                time.sleep(0.05)
                continue
            
            consecutive_failures = 0
            current_time = time.time()
            time_diff = current_time - prev_time
            prev_time = current_time
            
            # Compute smooth running average FPS
            if time_diff > 0:
                current_fps = 1.0 / time_diff
                fps = 0.9 * fps + 0.1 * current_fps if fps > 0.0 else current_fps
                
            annotated_frame = frame.copy()
            
            # Detect faces and landmarks
            boxes, probs, landmarks = engine.detect_faces(frame)
            
            match_results = []
            
            if boxes is not None and len(boxes) > 0:
                # Match each detected face against the database
                for box, prob, landmark in zip(boxes, probs, landmarks):
                    try:
                        # Align and crop the face using MTCNN eye landmarks
                        aligned_face = engine.align_face(frame, box, landmark)
                        # Extract 512D normalized embedding
                        embedding = engine.get_embedding(aligned_face)
                        # Classify face
                        name, confidence = engine.compare_embeddings(embedding, database)
                        match_results.append((box, name, confidence))
                    except Exception as e:
                        # Gracefully skip error on single face
                        pass
            
            # ----------------------------------------------------
            # State Machine & Safety Guard Logic
            # ----------------------------------------------------
            # Filter matches above/equal configured threshold
            threshold_percentage = config.SIMILARITY_THRESHOLD * 100.0
            valid_authorized = [m for m in match_results if m[1] != "Unknown" and m[2] >= threshold_percentage]
            
            if system_state == "LOCKED":
                lock_status = "CLOSED"
                servo.close_lock()  # Ensure locked
                
                if valid_authorized:
                    # Select authorized user with highest confidence
                    best_match = max(valid_authorized, key=lambda x: x[2])
                    system_state = "CONFIRMING"
                    confirm_person = best_match[1]
                    confirm_start_time = current_time
            
            elif system_state == "CONFIRMING":
                lock_status = "CLOSED"
                servo.close_lock()
                
                # Check if the same person is recognized continuously
                same_person_detected = False
                confirming_conf = 0.0
                for box, name, confidence in match_results:
                    if name == confirm_person and confidence >= threshold_percentage:
                        same_person_detected = True
                        confirming_conf = confidence
                        break
                
                if same_person_detected:
                    elapsed = current_time - confirm_start_time
                    if elapsed >= config.RECOGNITION_CONFIRM_TIME:
                        system_state = "UNLOCKED"
                        unlock_start_time = current_time
                        lock_status = "OPEN"
                        servo.open_lock()
                else:
                    # Reset confirmation timer immediately and revert to LOCKED
                    confirm_person = None
                    confirm_start_time = None
                    system_state = "LOCKED"
                    
                    # Responsive transition: check if someone else is in this frame
                    if valid_authorized:
                        best_match = max(valid_authorized, key=lambda x: x[2])
                        system_state = "CONFIRMING"
                        confirm_person = best_match[1]
                        confirm_start_time = current_time
            
            elif system_state == "UNLOCKED":
                lock_status = "OPEN"
                servo.open_lock()
                
                elapsed = current_time - unlock_start_time
                if elapsed >= config.SERVO_OPEN_DURATION:
                    system_state = "WAITING_EXIT"
                    lock_status = "CLOSED"
                    servo.close_lock()
            
            elif system_state == "WAITING_EXIT":
                lock_status = "CLOSED"
                servo.close_lock()
                
                # Check if the confirmed person is still in the camera frame
                person_present = False
                if boxes is not None and len(boxes) > 0:
                    for box, name, confidence in match_results:
                        if name == confirm_person:
                            person_present = True
                            break
                
                if not person_present:
                    system_state = "LOCKED"
                    # Reset confirm_person when they leave
                    confirm_person = None
            
            # ----------------------------------------------------
            # Unknown / Intruder Snapshot Capture (every 3 seconds)
            # ----------------------------------------------------
            unknown_matches = [m for m in match_results if m[1] == "Unknown"]
            if unknown_matches and system_state == "LOCKED":
                if current_time - last_snapshot_time >= config.UNKNOWN_COOLDOWN:
                    last_snapshot_time = current_time
                    date_str = datetime.now().strftime("%Y-%m-%d")
                    time_str = datetime.now().strftime("%H-%M-%S")
                    target_dir = os.path.join(config.UNKNOWN_SNAPSHOT_DIR, date_str)
                    os.makedirs(target_dir, exist_ok=True)
                    snapshot_path = os.path.join(target_dir, f"unknown_{time_str}.jpg")
                    try:
                        # Save raw frame
                        cv2.imwrite(snapshot_path, frame)
                    except Exception as e:
                        print(f"[ERROR] Failed to save unknown snapshot: {e}")
            
            # ----------------------------------------------------
            # Terminal Printing Logic (State-change driven)
            # ----------------------------------------------------
            if system_state == "LOCKED":
                if unknown_matches:
                    if last_printed_state != "DENIED":
                        best_unknown = max(unknown_matches, key=lambda x: x[2])
                        print("\n----------------------------------------------------")
                        print("Face Detected")
                        print("Name        : Unknown")
                        print(f"Confidence  : {best_unknown[2]:.2f}%")
                        print("Access      : DENIED")
                        print("Lock Status : CLOSED")
                        print("----------------------------------------------------")
                        log_event("Unknown", best_unknown[2], "DENIED", "CLOSED")
                        last_printed_state = "DENIED"
                else:
                    if last_printed_state == "DENIED":
                        print("\nNo Face Detected")
                        print("Lock Status : CLOSED")
                        last_printed_state = ("LOCKED", None)
                    elif last_printed_state != (system_state, confirm_person):
                        if last_printed_state[0] == "CONFIRMING":
                            print("\n----------------------------------------------------")
                            print("State Change: CONFIRMING -> LOCKED (Reset)")
                            print("Lock Status : CLOSED")
                            print("----------------------------------------------------")
                        elif last_printed_state[0] == "WAITING_EXIT":
                            left_user = last_printed_state[1] if last_printed_state[1] else "User"
                            print("\n----------------------------------------------------")
                            print(f"State Change: WAITING_EXIT -> LOCKED ({left_user} left frame)")
                            print("Lock Status : CLOSED")
                            print("----------------------------------------------------")
                        elif last_printed_state == "LOCKED_START":
                            print("\nNo Face Detected")
                            print("Lock Status : CLOSED")
                        last_printed_state = (system_state, confirm_person)
            else:
                if last_printed_state != (system_state, confirm_person):
                    if system_state == "CONFIRMING":
                        conf = 0.0
                        for box, name, confidence in match_results:
                            if name == confirm_person:
                                conf = confidence
                                break
                        print("\n----------------------------------------------------")
                        print("State Change: LOCKED -> CONFIRMING")
                        print(f"Confirming  : {confirm_person}")
                        print(f"Confidence  : {conf:.2f}%")
                        print("----------------------------------------------------")
                        
                    elif system_state == "UNLOCKED":
                        conf = 0.0
                        for box, name, confidence in match_results:
                            if name == confirm_person:
                                conf = confidence
                                break
                        print("\n----------------------------------------------------")
                        print("State Change: CONFIRMING -> UNLOCKED")
                        print(f"Authorized  : {confirm_person}")
                        print("Access      : GRANTED")
                        print("Lock Status : OPEN")
                        print("----------------------------------------------------")
                        log_event(confirm_person, conf, "GRANTED", "OPEN")
                        
                    elif system_state == "WAITING_EXIT":
                        print("\n----------------------------------------------------")
                        print("State Change: UNLOCKED -> WAITING_EXIT")
                        print("Lock Status : CLOSED (Cooldown)")
                        print("----------------------------------------------------")
                        
                    last_printed_state = (system_state, confirm_person)
            
            # ----------------------------------------------------
            # Draw Bounding Boxes and UI Overlays
            # ----------------------------------------------------
            for box, name, confidence in match_results:
                x1, y1, x2, y2 = map(int, box)
                if name != "Unknown" and confidence >= threshold_percentage:
                    color = (0, 255, 0) # Green for authorized
                    label_name = name
                    label_access = "Access Granted"
                elif name != "Unknown":
                    color = (0, 0, 255) # Red for known but below threshold
                    label_name = "Unknown"
                    label_access = "Access Denied"
                else:
                    color = (0, 0, 255) # Red for unknown
                    label_name = "Unknown"
                    label_access = "Access Denied"
                
                cv2.rectangle(annotated_frame, (x1, y1), (x2, y2), color, 2)
                
                font = cv2.FONT_HERSHEY_SIMPLEX
                text_name = f"{label_name} ({confidence:.2f}%)"
                text_access = label_access
                
                text_y = y1 - 35 if y1 >= 35 else y2 + 5
                rect_y1 = y1 - 35 if y1 >= 35 else y2
                rect_y2 = y1 if y1 >= 35 else y2 + 35
                
                cv2.rectangle(annotated_frame, (x1, rect_y1), (x2, rect_y2), color, -1)
                cv2.putText(annotated_frame, text_name, (x1 + 5, text_y + 13), font, 0.42, (255, 255, 255), 1, cv2.LINE_AA)
                cv2.putText(annotated_frame, text_access, (x1 + 5, text_y + 29), font, 0.42, (255, 255, 255), 1, cv2.LINE_AA)
            
            # Sleek Modern Overlay Panel
            panel_x1, panel_y1 = 15, 15
            panel_w = 275
            panel_h = 110 if system_state == "CONFIRMING" else 92
            panel_x2, panel_y2 = panel_x1 + panel_w, panel_y1 + panel_h
            
            # Draw semi-transparent background card
            cv2.rectangle(annotated_frame, (panel_x1, panel_y1), (panel_x2, panel_y2), (40, 30, 20), -1) # Dark Slate BGR
            cv2.rectangle(annotated_frame, (panel_x1, panel_y1), (panel_x2, panel_y2), (105, 85, 71), 1)  # Slate border
            
            font = cv2.FONT_HERSHEY_SIMPLEX
            TEXT_WHITE = (252, 250, 248)
            y_offset = panel_y1 + 20
            
            # FPS
            cv2.putText(annotated_frame, f"FPS : {fps:.1f}", (panel_x1 + 12, y_offset), font, 0.45, TEXT_WHITE, 1, cv2.LINE_AA)
            y_offset += 18
            
            # LOCK Status
            lock_color = (94, 197, 34) if lock_status == "OPEN" else (68, 68, 239) # Emerald Green vs Soft Red BGR
            cv2.putText(annotated_frame, f"LOCK : {lock_status}", (panel_x1 + 12, y_offset), font, 0.45, lock_color, 1, cv2.LINE_AA)
            y_offset += 18
            
            # STATE Status
            state_colors = {
                "LOCKED": (68, 68, 239),       # Soft Red
                "CONFIRMING": (248, 189, 56),  # Sky Blue BGR
                "UNLOCKED": (94, 197, 34),     # Emerald Green
                "WAITING_EXIT": (60, 146, 251) # Orange
            }
            state_color = state_colors.get(system_state, TEXT_WHITE)
            cv2.putText(annotated_frame, f"STATE : {system_state}", (panel_x1 + 12, y_offset), font, 0.45, state_color, 1, cv2.LINE_AA)
            y_offset += 18
            
            # Recognition Timer
            if system_state == "CONFIRMING":
                elapsed = time.time() - confirm_start_time if confirm_start_time else 0.0
                elapsed = min(elapsed, config.RECOGNITION_CONFIRM_TIME)
                timer_text = f"Recognition Timer : {elapsed:.1f} / {config.RECOGNITION_CONFIRM_TIME:.1f} sec"
                cv2.putText(annotated_frame, timer_text, (panel_x1 + 12, y_offset), font, 0.45, (251, 146, 60), 1, cv2.LINE_AA)
                y_offset += 18
                
            # Authorized Users Count
            cv2.putText(annotated_frame, f"Authorized Users : {num_users}", (panel_x1 + 12, y_offset), font, 0.45, TEXT_WHITE, 1, cv2.LINE_AA)
            
            # Save frame to global buffer under thread lock
            with frame_lock:
                output_frame = annotated_frame.copy()
                
            # Match target FPS loop timing
            time.sleep(1.0 / config.CAMERA_FPS)
            
    except Exception as e:
        print(f"\n[CRITICAL ERROR] Face recognition pipeline crashed: {e}")
        is_running = False
        os.kill(os.getpid(), signal.SIGINT)
    finally:
        print("[INFO] Recognition loop terminating. Releasing camera resources...")
        cap.release()
        if servo:
            servo.cleanup()

if __name__ == "__main__":
    # Suppress Flask web server request logging to keep stdout clean
    import logging
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)
    
    # Pre-verification: Check if embeddings database exists
    if not os.path.exists(config.DB_PATH):
        print(f"[ERROR] Database file not found at: {config.DB_PATH}")
        print("[INFO] Please run 'python enroll.py' first to process embeddings.")
        sys.exit(1)
        
    try:
        database = torch.load(config.DB_PATH)
        num_users = len(database)
    except Exception as e:
        print(f"[ERROR] Failed to load database: {e}")
        sys.exit(1)
        
    ip_addr = get_ip_address()
    
    # Start background processing thread
    rec_thread = threading.Thread(target=recognition_loop, daemon=True)
    rec_thread.start()
    
    # Print startup system header
    print("====================================================")
    print("System Started")
    print("Camera : USB Webcam")
    print("Device : Raspberry Pi 4")
    print("Model : FaceNet (CPU)")
    print(f"Database Loaded : {num_users} Users")
    
    if servo.is_hardware_active():
        print(f"GPIO Servo : READY (GPIO Pin {config.SERVO_GPIO_PIN})")
    else:
        print("GPIO Servo : SIMULATION MODE")
        
    print("Streaming :")
    print(f"http://{ip_addr}:5000")
    print("====================================================")
    
    try:
        app.run(host='0.0.0.0', port=5000, threaded=True, use_reloader=False)
    except KeyboardInterrupt:
        print("\n[INFO] Keyboard Interrupt received. Shutting down...")
    except Exception as e:
        print(f"\n[CRITICAL ERROR] Flask server encountered a fatal error: {e}")
    finally:
        is_running = False
        print("[INFO] Main thread terminating. Cleaning up resources...")
        if servo:
            servo.cleanup()
        print("[INFO] Teardown complete. Goodbye.")
