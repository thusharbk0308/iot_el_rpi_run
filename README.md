# Face Recognition Access Control System - Phase 1 Prototype

This is the Phase 1 software prototype of the real-time Face Recognition Access Control System designed to run on a **Raspberry Pi 4** using a **USB webcam**. It performs face detection, eye-landmark alignment, 512D FaceNet feature extraction, centroid similarity matching, simulated lock state visualization, event logging, and Flask web streaming.

---

## Project Structure

```text
face_access_control/
│
├── dataset/             # User image folders (e.g. dataset/Thushar/)
├── embeddings/          # Enrolled user database (authorized_faces.pt)
├── logs/                # Access event logs (access_log.csv) and intruder snapshots
│
├── capture.py           # Capture images of new users
├── enroll.py            # Detect, align, extract embeddings, and calculate centroids
├── recognize.py         # Main recognition service and Flask server
├── face_engine.py       # MTCNN detection, eye-alignment, and FaceNet embeddings
├── camera_stream.py     # Modular camera acquisition wrapper
├── config.py            # Centralized settings and thresholds
├── requirements.txt     # Dependency list
└── README.md            # Project documentation (this file)
```

---

## Installation & Setup

1. **System Packages**:
   Make sure Python 3 and OpenCV dependencies are installed on the Raspberry Pi:
   ```bash
   sudo apt update
   sudo apt install -y python3-pip python3-opencv
   ```

2. **Virtual Environment**:
   It is recommended to use a virtual environment:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```

3. **Install Dependencies**:
   Install all dependencies listed in `requirements.txt`:
   ```bash
   pip install -r requirements.txt
   ```
   *Note: For Raspberry Pi running on CPU, PyTorch will run in CPU-only mode automatically.*

---

## How to Run

### Step 1: Capture User Data
To capture enrollment images for a new user, run:
```bash
python capture.py
```
* **Process**: Input the user's name. Stand in front of the camera, tilting and smile to capture 30 varied facial profiles. The frames are saved directly inside `dataset/<person_name>/`.

### Step 2: Enroll and Generate Centroids
To process user folders and build the authorized database, run:
```bash
python enroll.py
```
* **Process**: The system will scan `dataset/`, detect/align faces in all images, compute L2-normalized FaceNet vectors, calculate the centroid average vector for each user, and save the registry to `embeddings/authorized_faces.pt`.

### Step 3: Run Real-time Recognition & Streaming
To launch the real-time access controller and streaming monitor, run:
```bash
python recognize.py
```
* **Process**: The script runs the webcam capture and recognition pipeline in a background thread, updates a global frame buffer, and serves a modern, dark-themed Flask website on port `5000`.
* **Access the feed**: Open your laptop browser and navigate to:
  ```text
  http://<raspberry_pi_ip_or_localhost>:5000
  ```
  The browser displays the real-time feed with color-coded bounding boxes, confidence percentages, access results, and simulated lock states.

---

## Terminal Notifications & Outputs

The terminal logs status updates **only when the recognition state changes** to avoid logs spam:

### Authorized Access
```text
----------------------------------------------------
Face Detected
Name        : Thushar
Confidence  : 97.42%
Access      : GRANTED
Lock Status : OPEN
----------------------------------------------------
```

### Unauthorized Access
```text
----------------------------------------------------
Face Detected
Name        : Unknown
Confidence  : 41.73%
Access      : DENIED
Lock Status : CLOSED
----------------------------------------------------
```

### Empty frame / No face
```text
No Face Detected
Lock Status : CLOSED
```

---

## Logging and Security
* **Transaction Log**: All access events are appended to `logs/access_log.csv` (`Timestamp, Name, Confidence, Result, Lock Status`).
* **Intruder Snaps**: High-resolution snapshots of unrecognized faces are saved inside `logs/` as `unknown_<timestamp>.jpg`, throttled to at most one snapshot every 5 seconds of continuous detection.
