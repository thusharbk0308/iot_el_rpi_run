# Centralized Configuration Parameters
# ==========================================

# Directory paths
DATASET_DIR = "dataset"
DB_PATH = "embeddings/authorized_faces.pt"
LOGS_DIR = "logs"
ACCESS_LOG_PATH = "logs/access_log.csv"

# Recognition parameters
SIMILARITY_THRESHOLD = 0.60
UNKNOWN_COOLDOWN = 3.0  # Cooldown in seconds for saving intruder/unknown snapshots (updated for Phase 2)
UNKNOWN_SNAPSHOT_DIR = "logs/unknown_faces"

# Recognition confirmation & lock configuration
RECOGNITION_CONFIRM_TIME = 2.0  # Seconds to continuously recognize face before unlocking
SERVO_OPEN_DURATION = 3.0      # Seconds to keep lock open before relocking
SERVO_GPIO_PIN = 18             # GPIO pin number for Raspberry Pi SG90 servo signal
SERVO_OPEN_ANGLE = 90           # Open angle in degrees
SERVO_CLOSED_ANGLE = 0          # Closed angle in degrees

# Servo pulse widths (tune these if your servo over/under-rotates)
# SG90 full range: 0.5ms (0 deg) → 2.5ms (180 deg)
# To get 90 deg physical rotation: max_pulse = 1.5ms
SERVO_MIN_PULSE_WIDTH = 0.5 / 1000   # Pulse width for CLOSED angle (0 deg)
SERVO_MAX_PULSE_WIDTH = 1.5 / 1000   # Pulse width for OPEN angle  (90 deg)

# Camera stream parameters
CAMERA_RES = (640, 480)
CAMERA_FPS = 30

