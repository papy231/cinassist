"""
CinAssist — Face Detection (Vague 1.3)

Détection de visages via OpenCV Haar cascade + classification framing.

Pourquoi Haar plutôt que MediaPipe / YOLO :
    - opencv-python déjà installé, pas de download modèle externe.
    - Latence ~20-50ms par image sur M4 (largement suffisant pour thumbnails).
    - Précision moyenne mais adéquate pour DÉRUSHAGE (on veut savoir s'il y
      a UN visage, à peu près où et à peu près quelle taille — pas de la
      reconnaissance).
    - Migration future vers DNN OpenCV (Caffe SSD) trivial si besoin +
      précision, sans changer l'interface.

Retourne un dict {face_count, framing, faces[]} exploitable par l'agent :
    - framing ∈ {"extreme_closeup", "closeup", "medium", "wide_with_person", "wide_no_person"}
      Défini par le ratio bbox_area / frame_area du PLUS GROS visage.
    - faces = liste de {bbox: [x,y,w,h], area_ratio, center_x, center_y}
"""
from __future__ import annotations

import logging
from pathlib import Path

import cv2

logger = logging.getLogger("cinassist.face_detect")

_CASCADE_PATH = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
_cascade: cv2.CascadeClassifier | None = None


def _get_cascade() -> cv2.CascadeClassifier:
    global _cascade
    if _cascade is None:
        _cascade = cv2.CascadeClassifier(_CASCADE_PATH)
        if _cascade.empty():
            raise RuntimeError(f"Failed to load Haar cascade at {_CASCADE_PATH}")
        logger.info("Haar cascade loaded.")
    return _cascade


def _classify_framing(max_area_ratio: float, face_count: int) -> str:
    """Classification framing style filmique par ratio bbox/frame."""
    if face_count == 0:
        return "wide_no_person"
    if max_area_ratio > 0.30:
        return "extreme_closeup"
    if max_area_ratio > 0.15:
        return "closeup"
    if max_area_ratio > 0.05:
        return "medium"
    return "wide_with_person"


def detect_faces(image_path: str | Path) -> dict:
    """
    Analyse un frame et retourne face_count + framing + bounding boxes.

    Args:
        image_path : chemin vers un fichier .jpg / .png

    Returns:
        {
            "face_count": int,
            "framing": str,
            "faces": [{"bbox": [x,y,w,h], "area_ratio": float, "center_x": float, "center_y": float}, ...]
        }
    """
    img = cv2.imread(str(image_path))
    if img is None:
        return {"face_count": 0, "framing": "wide_no_person", "faces": [], "error": "image not readable"}

    h, w = img.shape[:2]
    frame_area = float(h * w)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    # equalizeHist améliore la détection sur images sous-exposées
    gray = cv2.equalizeHist(gray)

    cascade = _get_cascade()
    detections = cascade.detectMultiScale(
        gray,
        scaleFactor=1.1,
        minNeighbors=5,
        minSize=(30, 30),
    )

    faces: list[dict] = []
    max_area_ratio = 0.0
    for (x, y, fw, fh) in detections:
        area_ratio = (fw * fh) / frame_area
        max_area_ratio = max(max_area_ratio, area_ratio)
        faces.append({
            "bbox": [int(x), int(y), int(fw), int(fh)],
            "area_ratio": round(area_ratio, 3),
            "center_x": round((x + fw / 2) / w, 3),
            "center_y": round((y + fh / 2) / h, 3),
        })

    return {
        "face_count": len(faces),
        "framing": _classify_framing(max_area_ratio, len(faces)),
        "faces": faces,
        "max_area_ratio": round(max_area_ratio, 3),
    }
