import cv2
import config

class CameraStream:
    def __init__(self, src=0):
        """
        Initializes the USB camera stream using OpenCV.
        Attempts to use CAP_V4L2 (standard on Linux/Raspberry Pi) and falls back if needed.
        """
        self.src = src
        print(f"[CAMERA] Opening USB camera (source {src}) with V4L2 backend...")
        
        # Try opening with V4L2 backend (specifically for Linux/Raspberry Pi)
        self.cap = cv2.VideoCapture(src, cv2.CAP_V4L2)
        
        # Fallback if V4L2 fails or is unsupported (e.g., on Windows development machines)
        if not self.cap.isOpened():
            print("[CAMERA] CAP_V4L2 backend failed or unsupported. Trying default backend...")
            self.cap = cv2.VideoCapture(src)
            
        if self.cap.isOpened():
            # Configure resolution and frame rate from central config.py
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, config.CAMERA_RES[0])
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, config.CAMERA_RES[1])
            self.cap.set(cv2.CAP_PROP_FPS, config.CAMERA_FPS)
            
            actual_w = self.cap.get(cv2.CAP_PROP_FRAME_WIDTH)
            actual_h = self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
            actual_fps = self.cap.get(cv2.CAP_PROP_FPS)
            print(f"[CAMERA] [SUCCESS] Camera opened. Resolution: {int(actual_w)}x{int(actual_h)} @ {actual_fps} FPS.")
        else:
            print("[CAMERA] [ERROR] Failed to open camera.")

    def read(self):
        """
        Reads a single frame from the camera stream.
        Returns:
            ret: bool (True if frame captured successfully)
            frame: numpy array (BGR format) or None
        """
        if self.cap and self.cap.isOpened():
            return self.cap.read()
        return False, None

    def isOpened(self):
        """
        Checks if the camera stream is open and active.
        """
        return self.cap is not None and self.cap.isOpened()

    def release(self):
        """
        Releases the camera capture resources.
        """
        if self.cap:
            self.cap.release()
            print("[CAMERA] Video capture resources released.")
            self.cap = None
