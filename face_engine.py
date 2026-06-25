import os
import ssl
# Bypass SSL verification to download pre-trained weights on networks/machines with certificate issues
ssl._create_default_https_context = ssl._create_unverified_context
import cv2
import torch
import numpy as np
from facenet_pytorch import MTCNN, InceptionResnetV1
import config

class FaceEngine:
    def __init__(self):
        """
        Initializes MTCNN face detector and FaceNet encoder.
        All calculations are run on CPU as per Phase 1 requirements.
        """
        self.device = torch.device("cpu")
        print(f"[ENGINE] Initializing FaceEngine on device: {self.device}")
        
        # MTCNN for face and landmark detection
        # margin=14 adds a buffer margin around detected faces.
        # keep_all=True allows detecting multiple faces in a single frame.
        self.detector = MTCNN(
            image_size=160,
            margin=14,
            min_face_size=40,
            thresholds=[0.6, 0.7, 0.7],
            keep_all=True,
            device=self.device
        )
        
        # InceptionResnetV1 pretrained on VGGFace2 for generating face embeddings
        self.encoder = InceptionResnetV1(
            pretrained='vggface2',
            device=self.device
        ).eval()

    def detect_faces(self, frame):
        """
        Detects all faces in a BGR frame and extracts their facial landmarks.
        Returns:
            boxes: np.ndarray of shape (N, 4) with [x1, y1, x2, y2] or None
            probs: np.ndarray of shape (N,) with detection confidences or None
            landmarks: np.ndarray of shape (N, 5, 2) with landmark coordinates or None
        """
        if frame is None:
            return None, None, None
        
        # Convert BGR to RGB for MTCNN
        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        
        # Detect faces and landmarks
        boxes, probs, landmarks = self.detector.detect(frame_rgb, landmarks=True)
        return boxes, probs, landmarks

    def align_face(self, frame, box, landmarks):
        """
        Aligns a face using eye landmarks, applies affine transformation, crops,
        and resizes to 160x160.
        Args:
            frame: Raw BGR input frame
            box: Bounding box coordinates [x1, y1, x2, y2]
            landmarks: 5 facial landmarks (left eye, right eye, nose, left mouth, right mouth)
        Returns:
            aligned_crop: Aligned face crop resized to 160x160 (BGR format)
        """
        # MTCNN 5 landmarks: index 0 = left eye, index 1 = right eye (viewer's perspective)
        left_eye = landmarks[0]
        right_eye = landmarks[1]
        
        # Calculate angle of the eyes relative to horizontal
        dy = right_eye[1] - left_eye[1]
        dx = right_eye[0] - left_eye[0]
        angle = np.degrees(np.arctan2(dy, dx))
        
        # Find the center between the eyes as the center of rotation
        eye_center = (float(left_eye[0] + right_eye[0]) / 2.0, float(left_eye[1] + right_eye[1]) / 2.0)
        
        # Generate rotation matrix
        M = cv2.getRotationMatrix2D(eye_center, angle, scale=1.0)
        
        # Apply affine transformation to the entire frame
        h, w = frame.shape[:2]
        rotated_frame = cv2.warpAffine(frame, M, (w, h), flags=cv2.INTER_LINEAR)
        
        # Crop the face using the bounding box from the rotated frame
        x1, y1, x2, y2 = map(int, box)
        x1 = max(0, x1)
        y1 = max(0, y1)
        x2 = min(w, x2)
        y2 = min(h, y2)
        
        # Fallback to direct crop if coordinates are invalid
        if (x2 - x1) < 10 or (y2 - y1) < 10:
            crop = frame[max(0, int(box[1])):min(h, int(box[3])), max(0, int(box[0])):min(w, int(box[2]))]
        else:
            crop = rotated_frame[y1:y2, x1:x2]
            
        # Resize to 160x160
        aligned_crop = cv2.resize(crop, (160, 160))
        return aligned_crop

    def get_embedding(self, face_crop):
        """
        Extracts a L2-normalized 512-dimensional embedding vector from a 160x160 BGR face crop.
        """
        # Convert BGR crop to RGB
        face_rgb = cv2.cvtColor(face_crop, cv2.COLOR_BGR2RGB)
        
        # Convert to PyTorch float tensor and change layout to (C, H, W)
        face_tensor = torch.tensor(face_rgb, dtype=torch.float32).permute(2, 0, 1)
        
        # Normalize to [-1, 1] range as expected by InceptionResnetV1: (x - 127.5) / 128.0
        face_tensor = (face_tensor - 127.5) / 128.0
        
        # Generate and normalize embedding
        with torch.no_grad():
            embedding = self.encoder(face_tensor.unsqueeze(0)) # Shape: (1, 512)
            # L2 Normalization
            embedding = embedding / embedding.norm(dim=1, keepdim=True)
            
        return embedding[0] # Return 512-dimensional Tensor

    def compare_embeddings(self, embedding, database):
        """
        Compares live embedding against enrolled centroids using cosine similarity.
        Args:
            embedding: 512D torch.Tensor
            database: dict mapping person name -> 512D centroid torch.Tensor
        Returns:
            name: matched name or "Unknown"
            confidence: similarity score percentage (0.0 to 100.0)
        """
        best_name = "Unknown"
        best_similarity = -1.0
        
        if database:
            for name, centroid in database.items():
                # Since both embedding and centroid are L2-normalized,
                # cosine similarity is simply the dot product
                sim = torch.dot(embedding, centroid).item()
                if sim > best_similarity:
                    best_similarity = sim
                    best_name = name
            
            # If highest similarity score is below the threshold, classify as Unknown
            if best_similarity < config.SIMILARITY_THRESHOLD:
                best_name = "Unknown"
                
        # Scale similarity score to a percentage (0.0 to 100.0)
        confidence = max(0.0, best_similarity) * 100.0
        return best_name, confidence
