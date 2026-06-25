# Face Recognition Access Control System - Phase 2: GPIO Servo Smart Door Lock

This is the Phase 2 extension of the real-time Face Recognition Access Control System running on a **Raspberry Pi 4** with a **USB webcam** and an **SG90/MG90S servo motor**. It performs face detection, eye-landmark alignment, 512D FaceNet feature extraction, centroid similarity matching, direct GPIO servo control, event logging, and Flask web streaming.

---

## Project Structure

```text
face_access_control/
│
├── dataset/             # User image folders (e.g. dataset/Thushar/)
├── embeddings/          # Enrolled user database (authorized_faces.pt)
├── logs/
│   ├── access_log.csv   # Access event log
│   └── unknown_faces/   # Intruder snapshots organized by date
│       ├── 2026-07-01/
│       │     unknown_14-20-33.jpg
│       └── ...
│
├── capture.py           # Capture images of new users
├── enroll.py            # Detect, align, extract embeddings, and calculate centroids
├── recognize.py         # Main recognition service and Flask server (Phase 2)
├── servo_controller.py  # GPIO servo driver with safety state guard (Phase 2)
├── face_engine.py       # MTCNN detection, eye-alignment, and FaceNet embeddings
├── camera_stream.py     # Modular camera acquisition wrapper
├── config.py            # Centralized settings and thresholds
├── requirements.txt     # Dependency list
└── README.md            # Project documentation (this file)
```

---

## Servo Wiring

### SG90 / MG90S (Powered from Raspberry Pi 5V Pin)

| Servo Wire | Raspberry Pi Pin     |
|------------|----------------------|
| Signal (Orange) | GPIO 18 (Pin 12) |
| VCC (Red)       | 5V (Pin 2 or 4)  |
| GND (Brown)     | GND (Pin 6)      |

### High-Torque Servo e.g. MG996R (External 5V Supply)

| Servo Wire      | Connection                              |
|-----------------|-----------------------------------------|
| Signal (Orange) | GPIO 18 (Pin 12) on Raspberry Pi        |
| VCC (Red)       | External regulated 5V supply (+)        |
| GND (Brown)     | External supply (−) **AND** Raspberry Pi GND (Pin 6) |

> **Important**: Always share a common ground between the Raspberry Pi and the external power supply. The software does **not** require any changes when switching power configurations.

---

## Lock State Machine

```text
       Known Person Detected (≥ confidence threshold)
LOCKED ────────────────────────────────────► CONFIRMING
  ▲                                               │
  │  Person disappears / changes / low conf       │ Same person recognized ≥ 2 sec
  │◄──────────────────────────────────────────────┘
  │
  │                                          UNLOCKED
  │                                               │
  │                                               │ 3 seconds elapsed
  │                                               ▼
  └─────────────────────────────────── WAITING_EXIT
             Person leaves camera frame
```

| State          | Lock | Condition to Advance                         |
|----------------|------|----------------------------------------------|
| `LOCKED`       | CLOSED | Known person detected above threshold      |
| `CONFIRMING`   | CLOSED | Same person ≥ 2 sec + confidence OK        |
| `UNLOCKED`     | OPEN   | 3 seconds elapsed                          |
| `WAITING_EXIT` | CLOSED | Authorized person leaves camera frame      |

---

## Installation & Setup

1. **System Packages**:
   ```bash
   sudo apt update
   sudo apt install -y python3-pip python3-opencv
   ```

2. **Virtual Environment**:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```

3. **OpenBLAS Fix for ARM64**:
   Add the following to your `~/.bashrc` to prevent `Illegal instruction` crashes from NumPy/OpenBLAS:
   ```bash
   echo 'export OPENBLAS_CORETYPE=ARMV8' >> ~/.bashrc
   source ~/.bashrc
   ```

4. **Install Dependencies**:
   ```bash
   pip install -r requirements.txt
   ```
   > **Note**: Install PyTorch from piwheels (ARM64 compatible). Do **not** use `--index-url https://download.pytorch.org/whl/cpu` as that serves x86/CUDA builds.

---

## How to Run

### Step 1: Capture User Data
```bash
python capture.py
```
Input the user's name. Stand in front of the camera and capture 30 varied facial profiles.

### Step 2: Enroll and Generate Centroids
```bash
python enroll.py
```
Processes `dataset/`, detects/aligns faces, computes L2-normalized FaceNet vectors, calculates the centroid for each user, and saves to `embeddings/authorized_faces.pt`.

### Step 3: Run Real-time Recognition & Servo Control
```bash
python recognize.py
```
Launches the webcam pipeline, servo controller, and Flask stream on port `5000`.

Access the live feed from any browser on the same network:
```text
http://<raspberry_pi_ip>:5000
```

---

## Browser Overlay

The live video stream displays a real-time system status panel:

```text
FPS : 18
LOCK : CLOSED
STATE : CONFIRMING
Recognition Timer : 1.4 / 2.0 sec
Authorized Users : 2
```

After unlocking:
```text
FPS : 24
LOCK : OPEN
STATE : UNLOCKED
Authorized Users : 2
```

During cooldown:
```text
FPS : 22
LOCK : CLOSED
STATE : WAITING_EXIT
Authorized Users : 2
```

---

## Terminal Notifications

Terminal logs fire **only on state transitions**:

### Confirming Authorized User
```text
----------------------------------------------------
State Change: LOCKED -> CONFIRMING
Confirming  : Thushar
Confidence  : 94.30%
----------------------------------------------------
```

### Access Granted (after 2 seconds)
```text
----------------------------------------------------
State Change: CONFIRMING -> UNLOCKED
Authorized  : Thushar
Access      : GRANTED
Lock Status : OPEN
----------------------------------------------------
```

### Relocking after 3 seconds
```text
----------------------------------------------------
State Change: UNLOCKED -> WAITING_EXIT
Lock Status : CLOSED (Cooldown)
----------------------------------------------------
```

### User leaves frame
```text
----------------------------------------------------
State Change: WAITING_EXIT -> LOCKED (Thushar left frame)
Lock Status : CLOSED
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

---

## Emergency Fail-Safe

The system guarantees the servo returns to the **LOCKED position** before terminating under any of these conditions:

- Normal program exit
- Ctrl+C (KeyboardInterrupt)
- Camera stream stops unexpectedly
- Face recognition pipeline crash
- Flask server fatal error
- Any unhandled exception

After locking, GPIO resources are released and the camera is closed cleanly.

---

## Logging and Security

- **Transaction Log**: All access events are appended to `logs/access_log.csv` with columns: `Timestamp, Name, Confidence, Result, Lock Status`.
- **Intruder Snapshots**: High-resolution snapshots of unrecognized faces are saved to `logs/unknown_faces/YYYY-MM-DD/unknown_HH-MM-SS.jpg`, throttled to at most one snapshot every 3 seconds of continuous detection.
