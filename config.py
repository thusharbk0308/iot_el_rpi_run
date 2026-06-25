# Centralized Configuration Parameters
# ==========================================

# Directory paths
DATASET_DIR = "dataset"
DB_PATH = "embeddings/authorized_faces.pt"
LOGS_DIR = "logs"
ACCESS_LOG_PATH = "logs/access_log.csv"

# Recognition parameters
SIMILARITY_THRESHOLD = 0.60
UNKNOWN_COOLDOWN = 5.0  # Cooldown in seconds for saving intruder/unknown snapshots

# Camera stream parameters
CAMERA_RES = (640, 480)
CAMERA_FPS = 30
