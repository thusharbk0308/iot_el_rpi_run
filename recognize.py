import os
import cv2
import time
import socket
import csv
import sys
from datetime import datetime
import torch
import threading
from flask import Flask, Response, render_template_string
from camera_stream import CameraStream
from face_engine import FaceEngine
import config

# Initialize Flask app
app = Flask(__name__)

# Global variables for cross-thread communication
output_frame = None
frame_lock = threading.Lock()
is_running = True
lock_status = "CLOSED"

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
    global output_frame, lock_status, is_running
    
    # Load database
    try:
        database = torch.load(config.DB_PATH)
    except Exception as e:
        print(f"[ERROR] Failed to load database: {e}")
        is_running = False
        return
        
    engine = FaceEngine()
    cap = CameraStream(0)
    
    if not cap.isOpened():
        print("[ERROR] Camera stream could not be opened.")
        is_running = False
        return
        
    last_state = "NO_FACE"
    last_snapshot_time = 0.0
    prev_time = time.time()
    fps = 0.0
    
    while is_running:
        ret, frame = cap.read()
        if not ret:
            time.sleep(0.01)
            continue
            
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
        
        dominant_result = "NO_FACE"
        dominant_name = None
        dominant_confidence = 0.0
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
            
            # Determine overall access control decision
            authorized_matches = [m for m in match_results if m[1] != "Unknown"]
            if authorized_matches:
                dominant_result = "GRANTED"
                # Select the authorized user with highest confidence
                best_match = max(authorized_matches, key=lambda x: x[2])
                dominant_name = best_match[1]
                dominant_confidence = best_match[2]
                lock_status = "OPEN"
            elif match_results:
                dominant_result = "DENIED"
                dominant_name = "Unknown"
                best_match = max(match_results, key=lambda x: x[2])
                dominant_confidence = best_match[2]
                lock_status = "CLOSED"
            else:
                dominant_result = "NO_FACE"
                lock_status = "CLOSED"
                
            # Draw boxes and labels on stream
            for box, name, confidence in match_results:
                x1, y1, x2, y2 = map(int, box)
                if name != "Unknown":
                    color = (0, 255, 0) # Green for authorized
                    label_name = name
                    label_access = "Access Granted"
                else:
                    color = (0, 0, 255) # Red for unknown
                    label_name = "Unknown"
                    label_access = "Access Denied"
                
                cv2.rectangle(annotated_frame, (x1, y1), (x2, y2), color, 2)
                
                font = cv2.FONT_HERSHEY_SIMPLEX
                text_name = f"{label_name} ({confidence:.2f}%)"
                text_access = label_access
                
                # Check bounds to draw label above or below box
                text_y = y1 - 35 if y1 >= 35 else y2 + 5
                rect_y1 = y1 - 35 if y1 >= 35 else y2
                rect_y2 = y1 if y1 >= 35 else y2 + 35
                
                cv2.rectangle(annotated_frame, (x1, rect_y1), (x2, rect_y2), color, -1)
                cv2.putText(annotated_frame, text_name, (x1 + 5, text_y + 13), font, 0.42, (255, 255, 255), 1, cv2.LINE_AA)
                cv2.putText(annotated_frame, text_access, (x1 + 5, text_y + 29), font, 0.42, (255, 255, 255), 1, cv2.LINE_AA)
        else:
            dominant_result = "NO_FACE"
            lock_status = "CLOSED"
            
        # Draw top banner for FPS & simulated Lock status
        cv2.rectangle(annotated_frame, (0, 0), (config.CAMERA_RES[0], 40), (15, 23, 42), -1)
        cv2.line(annotated_frame, (0, 40), (config.CAMERA_RES[0], 40), (51, 65, 85), 1)
        font = cv2.FONT_HERSHEY_SIMPLEX
        lock_color = (34, 197, 94) if lock_status == "OPEN" else (239, 68, 68)
        cv2.putText(annotated_frame, f"LOCK : {lock_status}", (15, 26), font, 0.6, lock_color, 2, cv2.LINE_AA)
        cv2.putText(annotated_frame, f"FPS : {fps:.1f}", (config.CAMERA_RES[0] - 120, 26), font, 0.6, (148, 163, 184), 1, cv2.LINE_AA)
        
        # Save frame to global buffer under thread lock
        with frame_lock:
            output_frame = annotated_frame.copy()
            
        # State machine for prints and logging (triggers only on changes)
        state_str = f"GRANTED:{dominant_name}" if dominant_result == "GRANTED" else dominant_result
        if state_str != last_state:
            if dominant_result == "GRANTED":
                print("\n----------------------------------------------------")
                print("Face Detected")
                print(f"Name        : {dominant_name}")
                print(f"Confidence  : {dominant_confidence:.2f}%")
                print("Access      : GRANTED")
                print("Lock Status : OPEN")
                print("----------------------------------------------------")
                log_event(dominant_name, dominant_confidence, "GRANTED", "OPEN")
            elif dominant_result == "DENIED":
                print("\n----------------------------------------------------")
                print("Face Detected")
                print("Name        : Unknown")
                print(f"Confidence  : {dominant_confidence:.2f}%")
                print("Access      : DENIED")
                print("Lock Status : CLOSED")
                print("----------------------------------------------------")
                log_event("Unknown", dominant_confidence, "DENIED", "CLOSED")
            else: # NO_FACE
                print("\nNo Face Detected")
                print("Lock Status : CLOSED")
                
            last_state = state_str
            
        # Save snapshot of unknown face (throttled to 5 seconds)
        if dominant_result == "DENIED":
            if current_time - last_snapshot_time >= config.UNKNOWN_COOLDOWN:
                last_snapshot_time = current_time
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                snapshot_path = os.path.join(config.LOGS_DIR, f"unknown_{timestamp}.jpg")
                try:
                    # Save the raw frame without box overlay as standard practice
                    cv2.imwrite(snapshot_path, frame)
                except Exception as e:
                    print(f"[ERROR] Failed to save unknown snapshot: {e}")
                    
        # Match target FPS loop timing
        time.sleep(1.0 / config.CAMERA_FPS)
        
    cap.release()

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
    print("Streaming :")
    print(f"http://{ip_addr}:5000")
    print("====================================================")
    
    try:
        app.run(host='0.0.0.0', port=5000, threaded=True, use_reloader=False)
    except KeyboardInterrupt:
        print("\n[INFO] Keyboard Interrupt received. Shutting down...")
    finally:
        is_running = False
        print("[INFO] Teardown complete. Goodbye.")
