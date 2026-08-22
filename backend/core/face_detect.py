"""
CinAssist — Face Detection (Vague 1.3)

Gesichtserkennung über die Haar-Kaskade von OpenCV, samt Einordnung der Einstellungsgröße.

Warum Haar und nicht MediaPipe oder YOLO:
    - opencv-python ist bereits vorhanden, ein externes Modell muss nicht geladen werden.
    - Rechenzeit von etwa zwanzig bis fünfzig Millisekunden je Bild auf dem M4, für Vorschaubilder reichlich.
    - Mittlere Genauigkeit, für die Sichtung des Materials aber ausreichend: gefragt ist,
      ob ÜBERHAUPT ein Gesicht da ist, ungefähr wo und ungefähr wie groß, nicht
      reconnaissance).
    - Ein späterer Wechsel auf das neuronale Netz von OpenCV (Caffe SSD) wäre unaufwendig,
      falls mehr Genauigkeit nötig wird, ohne die Schnittstelle zu ändern.

Zurück kommt ein Wörterbuch {face_count, framing, faces[]}, das der Assistent auswerten kann:
    - framing ∈ {"extreme_closeup", "closeup", "medium", "wide_with_person", "wide_no_person"}
      Bestimmt über das Verhältnis bbox_area zu frame_area des GRÖSSTEN Gesichts.
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


_profile_cascade: cv2.CascadeClassifier | None = None
_profile_tried = False


def _get_profile_cascade() -> cv2.CascadeClassifier | None:
    global _profile_cascade, _profile_tried
    if not _profile_tried:
        _profile_tried = True
        try:
            c = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_profileface.xml")
            _profile_cascade = None if c.empty() else c
        except Exception:  # noqa: BLE001
            _profile_cascade = None
    return _profile_cascade


def _iou(a, b) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix = max(0, min(ax + aw, bx + bw) - max(ax, bx))
    iy = max(0, min(ay + ah, by + bh) - max(ay, by))
    inter = ix * iy
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


def _nms(boxes, schwelle: float = 0.3):
    """Überlappende Treffer (frontal/profil/gespiegelt) auf einen Kasten zusammenziehen."""
    boxes = sorted((tuple(int(v) for v in b) for b in boxes), key=lambda b: b[2] * b[3], reverse=True)
    out = []
    for b in boxes:
        if all(_iou(b, o) < schwelle for o in out):
            out.append(b)
    return out


def _classify_framing(max_area_ratio: float, face_count: int) -> str:
    """Ordnet die Einstellungsgröße filmisch ein, über das Verhältnis von Rahmen zu Bild."""
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
    Wertet ein Einzelbild aus und gibt face_count, framing und die Rahmen zurück.

    Args:
        image_path: Pfad zu einer .jpg- oder .png-Datei

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
    # equalizeHist verbessert die Erkennung auf unterbelichteten Bildern
    gray = cv2.equalizeHist(gray)

    cascade = _get_cascade()
    # minSize relativ zur Bildbreite: bei 896-px-Frames sind Gesichter in Totalen ~20–30 px.
    min_px = max(20, int(w * 0.025))
    detections = list(cascade.detectMultiScale(gray, scaleFactor=1.08, minNeighbors=5, minSize=(min_px, min_px)))
    # Profil-Kaskade (links + gespiegelt rechts) — Haar-frontal findet in Dialog-Totalen oft nichts.
    prof = _get_profile_cascade()
    if prof is not None:
        for (x, y, fw, fh) in prof.detectMultiScale(gray, scaleFactor=1.08, minNeighbors=6, minSize=(min_px, min_px)):
            detections.append((x, y, fw, fh))
        flipped = cv2.flip(gray, 1)
        for (x, y, fw, fh) in prof.detectMultiScale(flipped, scaleFactor=1.08, minNeighbors=6, minSize=(min_px, min_px)):
            detections.append((w - x - fw, y, fw, fh))
    detections = _nms(detections)

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
