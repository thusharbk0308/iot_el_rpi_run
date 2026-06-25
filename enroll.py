import os
import cv2
import torch
from tqdm import tqdm
from face_engine import FaceEngine
import config

def main():
    print("=== Face Enrollment Processor ===")
    
    # Verify dataset directory exists and contains files
    if not os.path.exists(config.DATASET_DIR) or len(os.listdir(config.DATASET_DIR)) == 0:
        print(f"[ERROR] Dataset directory '{config.DATASET_DIR}' does not exist or is empty.")
        print("[INFO] Please run 'python capture.py' first to enroll users.")
        return
        
    print("[INFO] Initializing FaceEngine...")
    engine = FaceEngine()
    
    database = {}
    person_dirs = [d for d in os.listdir(config.DATASET_DIR) if os.path.isdir(os.path.join(config.DATASET_DIR, d))]
    
    if not person_dirs:
        print(f"[ERROR] No subfolders representing users found in '{config.DATASET_DIR}/'.")
        return
        
    print(f"[INFO] Found {len(person_dirs)} user folder(s) to process: {person_dirs}")
    
    for person_name in person_dirs:
        person_path = os.path.join(config.DATASET_DIR, person_name)
        image_files = [f for f in os.listdir(person_path) if f.lower().endswith(('.jpg', '.jpeg', '.png'))]
        
        if not image_files:
            print(f"[WARNING] No images found in folder: {person_path}. Skipping.")
            continue
            
        print(f"\n[PROCESSING] Enrolling '{person_name}' with {len(image_files)} images...")
        embeddings_list = []
        
        for img_name in tqdm(image_files, desc=f"Processing {person_name}"):
            img_path = os.path.join(person_path, img_name)
            frame = cv2.imread(img_path)
            if frame is None:
                print(f"\n[WARNING] Could not read image: {img_path}. Skipping.")
                continue
                
            # Run MTCNN detection & extract facial landmarks
            boxes, probs, landmarks = engine.detect_faces(frame)
            
            if boxes is not None and len(boxes) > 0:
                # Use the most confident detected face (index 0)
                box = boxes[0]
                landmark = landmarks[0]
                
                try:
                    # Align and crop the face
                    aligned_face = engine.align_face(frame, box, landmark)
                    # Extract 512-dimensional embedding
                    embedding = engine.get_embedding(aligned_face)
                    embeddings_list.append(embedding)
                except Exception as e:
                    print(f"\n[ERROR] Failed to process {img_name}: {e}")
                    
        if len(embeddings_list) == 0:
            print(f"[WARNING] No faces could be processed for '{person_name}'. Skipping registration.")
            continue
            
        # Stack individual face embeddings: Shape (N, 512)
        embeddings_tensor = torch.stack(embeddings_list)
        # Compute mean embedding vector: Shape (512,)
        mean_embedding = torch.mean(embeddings_tensor, dim=0)
        # L2-normalize the centroid embedding
        centroid_embedding = mean_embedding / mean_embedding.norm()
        
        database[person_name] = centroid_embedding
        print(f"[SUCCESS] Enrolled '{person_name}' with {len(embeddings_list)} valid face embeddings.")
        
    if database:
        # Create output embeddings directory if it does not exist
        os.makedirs(os.path.dirname(config.DB_PATH) or ".", exist_ok=True)
        print(f"\n[INFO] Saving embeddings database to: {config.DB_PATH}")
        torch.save(database, config.DB_PATH)
        print("[SUCCESS] Face enrollment completed successfully! Database is ready.")
    else:
        print("\n[ERROR] No users were successfully enrolled. Check your dataset images.")

if __name__ == "__main__":
    main()
