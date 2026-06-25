import os
import cv2
import time
from face_engine import FaceEngine
from camera_stream import CameraStream
import config

def main():
    print("=== Face Dataset Capture Utility ===")
    name = input("Enter the name of the person to enroll: ").strip()
    if not name:
        print("[ERROR] Name cannot be empty. Exiting.")
        return
    
    # Sanitize name for directory safety
    safe_name = "".join([c for c in name if c.isalnum() or c in (" ", "_", "-")]).strip()
    safe_name = safe_name.replace(" ", "_")
    
    output_dir = os.path.join(config.DATASET_DIR, safe_name)
    os.makedirs(output_dir, exist_ok=True)
    
    print("[INFO] Initializing FaceEngine...")
    engine = FaceEngine()
    
    print("[INFO] Opening camera stream...")
    cap = CameraStream(0)
    if not cap.isOpened():
        print("[ERROR] Could not open camera. Ensure it is connected.")
        return
    
    print("\n--- INSTRUCTIONS ---")
    print("1. Stand in front of the camera in a well-lit environment.")
    print("2. The system will automatically capture a face when detected.")
    print("3. Tilt, turn, smile, or change angles slightly during capture to get a variety of features.")
    print("4. Press 'q' on the camera window to cancel and exit.")
    print("Press Enter to start...")
    input()
    
    count = 0
    max_images = 30
    last_capture_time = 0.0
    capture_cooldown = 0.25 # seconds between captures (to allow user to shift pose)
    
    print("[INFO] Starting capture loop. Press 'q' on the camera window to abort.")
    
    while count < max_images:
        ret, frame = cap.read()
        if not ret:
            print("[ERROR] Failed to read frame from camera.")
            break
            
        display_frame = frame.copy()
        
        # Detect faces using the modified engine API
        boxes, probs, landmarks = engine.detect_faces(frame)
        current_time = time.time()
        face_detected = boxes is not None and len(boxes) > 0
        
        if face_detected:
            # Highlight first detected face
            box = boxes[0]
            x1, y1, x2, y2 = map(int, box)
            cv2.rectangle(display_frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
            
            # Check capture cooldown
            if current_time - last_capture_time >= capture_cooldown:
                # Save the raw image (no bounding box overlaid)
                img_filename = os.path.join(output_dir, f"face_{count + 1:03d}.jpg")
                cv2.imwrite(img_filename, frame)
                
                last_capture_time = current_time
                count += 1
                print(f"[CAPTURE] Saved {count}/{max_images}: {img_filename}")
                
        # Draw UI overlay
        cv2.rectangle(display_frame, (0, 0), (config.CAMERA_RES[0], 50), (0, 0, 0), -1)
        status_text = f"User: {name} | Captured: {count}/{max_images}"
        cv2.putText(display_frame, status_text, (15, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
        
        cv2.rectangle(display_frame, (0, config.CAMERA_RES[1] - 50), (config.CAMERA_RES[0], config.CAMERA_RES[1]), (0, 0, 0), -1)
        if face_detected:
            guide_text = "Face detected! Tilt/smile for variety."
            text_color = (0, 255, 0)
        else:
            guide_text = "Align your face in the frame."
            text_color = (0, 0, 255)
        cv2.putText(display_frame, guide_text, (15, config.CAMERA_RES[1] - 18), cv2.FONT_HERSHEY_SIMPLEX, 0.6, text_color, 1)
        
        cv2.imshow("Dataset Capture Tool", display_frame)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            print("[INFO] Capture cancelled by user.")
            break
            
    cap.release()
    cv2.destroyAllWindows()
    
    if count == max_images:
        print(f"\n[SUCCESS] Successfully captured {max_images} images for {name}!")
        print(f"[INFO] Next step: Run 'python enroll.py' to generate embeddings.")
    else:
        print(f"\n[WARNING] Only captured {count}/{max_images} images. Re-run to capture full dataset.")

if __name__ == "__main__":
    main()
