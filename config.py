# Centralized Configuration Parameters
# ==========================================

# Directory paths
DATASET_DIR        = "dataset"
DB_PATH            = "embeddings/authorized_faces.pt"
LOGS_DIR           = "logs"
ACCESS_LOG_PATH    = "logs/access_log.csv"
UNKNOWN_SNAPSHOT_DIR = "logs/unknown_faces"

# SQLite database
DB_SQLITE_PATH = "security.db"

# Recognition parameters
SIMILARITY_THRESHOLD    = 0.60
UNKNOWN_COOLDOWN        = 3.0   # Seconds between unknown snapshots

# Recognition confirmation & lock
RECOGNITION_CONFIRM_TIME = 2.0  # Seconds to hold face before unlocking
SERVO_OPEN_DURATION      = 3.0  # Seconds to keep lock open

# Servo hardware
SERVO_GPIO_PIN      = 18
SERVO_OPEN_ANGLE    = 90
SERVO_CLOSED_ANGLE  = 0
SERVO_MIN_PULSE_WIDTH = 0.5 / 1000   # 0 deg  → 500 us
SERVO_MAX_PULSE_WIDTH = 1.5 / 1000   # 90 deg → 1500 us

# Camera
CAMERA_RES = (640, 480)
CAMERA_FPS = 30

# Email alert settings
EMAIL_ALERTS_ENABLED = False
EMAIL_SENDER         = ""        # Your Gmail address
EMAIL_PASSWORD       = ""        # Gmail App Password (not account password)
EMAIL_RECIPIENT      = ""        # Where alerts are sent
SMTP_SERVER          = "smtp.gmail.com"
SMTP_PORT            = 587

# Dashboard authentication
DASHBOARD_PORT          = 5000
DASHBOARD_SECRET_KEY    = "faceguard-change-this-secret"  # Change in Settings!
SESSION_TIMEOUT_MINUTES = 30
