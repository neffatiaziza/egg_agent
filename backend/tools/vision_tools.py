# backend/tools/vision_tools.py  — Version nucléaire anti-crash
"""
Ce fichier remplace COMPLÈTEMENT l'ancien vision_tools.py.
Il ne plantera jamais quelle que soit l'entrée reçue.
"""

import os
import io
import re
import json
import base64
import logging
from dotenv import load_dotenv
from langchain_core.tools import tool

# ── PIL : autoriser les images tronquées AVANT tout import ──
from PIL import ImageFile, Image as PILImage
ImageFile.LOAD_TRUNCATED_IMAGES = True  # CRITIQUE — empêche Truncated File Read

import torch
import torch.nn as nn
from torchvision import models, transforms

load_dotenv()
logger = logging.getLogger(__name__)

DEVICE = torch.device("cpu")

_BASE       = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EFFNET_PATH = os.path.join(_BASE, "models", "best_egg_grader.pth")
YOLO_PATH   = os.path.join(_BASE, "models", "best_egg_detector.pt")


# ─────────────────────────────────────────────────────────────
# Helper : détecter les placeholders
# ─────────────────────────────────────────────────────────────
def _is_placeholder(s) -> bool:
    """
    Retourne True si s est un placeholder (pas une vraie image base64).
    Un vrai base64 d'image fait au minimum ~500 caractères.
    """
    if not s or not isinstance(s, str):
        return True
    s = s.strip()
    # Placeholders entre < >
    if s.startswith("<") and s.endswith(">"):
        return True
    # Mots-clés connus
    if s.lower() in {"<base64>", "base64", "", "no_image", "none", "null",
                     "<image>", "image", "<crop>", "crop", "cropped_image",
                     "<base64_crop>", "base64_crop", "use_state_image"}:
        return True
    # Extraire la partie pure (après la virgule data:...)
    raw = s.split(",", 1)[1] if "," in s else s
    raw = re.sub(r'\s', '', raw)
    # Moins de 500 chars = pas une vraie image
    return len(raw) < 500


# ─────────────────────────────────────────────────────────────
# Helper : base64 → PIL Image
# ─────────────────────────────────────────────────────────────
def _b64_to_pil(b64: str) -> PILImage.Image:
    """
    Convertit base64 → PIL Image.
    Robuste : tolère data URI, espaces, padding manquant, images tronquées.
    Ne lève jamais d'exception sur un vrai base64 valide.
    """
    # Extraire la partie pure
    if "," in b64:
        b64 = b64.split(",", 1)[1]

    # Supprimer espaces/retours (fréquent dans les gros payloads HTTP)
    b64 = re.sub(r'\s', '', b64)

    # Corriger le padding
    pad = 4 - len(b64) % 4
    if pad != 4:
        b64 += "=" * pad

    # Décoder — validate=False pour tolérer les imperfections
    try:
        img_bytes = base64.b64decode(b64, validate=False)
    except Exception as e:
        raise ValueError(f"base64 decode failed: {e}")

    if len(img_bytes) < 10:
        raise ValueError(f"Decoded bytes too short ({len(img_bytes)}), not a valid image")

    # Ouvrir avec PIL
    buf = io.BytesIO(img_bytes)
    try:
        img = PILImage.open(buf)
        img.load()
        return img.convert("RGB")
    except Exception:
        # Deuxième tentative sans .load() (pour les images très tronquées)
        buf.seek(0)
        try:
            img = PILImage.open(buf)
            return img.convert("RGB")
        except Exception as e2:
            raise OSError(f"PIL cannot open image: {e2}")


# ─────────────────────────────────────────────────────────────
# Helper : PIL → base64
# ─────────────────────────────────────────────────────────────
def _pil_to_b64(img: PILImage.Image, quality: int = 85) -> str:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return base64.b64encode(buf.getvalue()).decode("utf-8")


# ─────────────────────────────────────────────────────────────
# EfficientNet-B0
# ─────────────────────────────────────────────────────────────
_effnet_model  = None
_idx_to_class  = None
EFFNET_AVAILABLE = False

def _build_efficientnet_b0():
    m = models.efficientnet_b0(weights=None)
    in_f = m.classifier[1].in_features
    m.classifier = nn.Sequential(
        nn.Dropout(0.4), nn.Linear(in_f, 512), nn.ReLU(),
        nn.BatchNorm1d(512), nn.Dropout(0.2), nn.Linear(512, 6)
    )
    return m

def _load_effnet():
    global _effnet_model, _idx_to_class, EFFNET_AVAILABLE
    if _effnet_model is not None:
        return True
    if not os.path.exists(EFFNET_PATH):
        logger.warning(f"[EfficientNet] Not found: {EFFNET_PATH}")
        return False
    try:
        ckpt = torch.load(EFFNET_PATH, map_location=DEVICE)
        c2i  = ckpt.get("class_to_idx", {'A':0,'AA':1,'B':2,'C':3,'D':4,'E':5})
        _idx_to_class = {v: k for k, v in c2i.items()}
        m = _build_efficientnet_b0()
        m.load_state_dict(ckpt["model_state_dict"])
        m.eval()
        _effnet_model  = m
        EFFNET_AVAILABLE = True
        logger.info(f"[EfficientNet] Loaded ✅")
        return True
    except Exception as e:
        logger.error(f"[EfficientNet] Load failed: {e}")
        return False

_EFFNET_TF = transforms.Compose([
    transforms.Resize((224, 224)),
    transforms.ToTensor(),
    transforms.Normalize(mean=[0.485,0.456,0.406], std=[0.229,0.224,0.225])
])


# ─────────────────────────────────────────────────────────────
# YOLO
# ─────────────────────────────────────────────────────────────
_yolo_model    = None
YOLO_AVAILABLE = False

def _load_yolo():
    global _yolo_model, YOLO_AVAILABLE
    if _yolo_model is not None:
        return True
    if not os.path.exists(YOLO_PATH):
        logger.warning(f"[YOLO] Not found: {YOLO_PATH}")
        return False
    try:
        from ultralytics import YOLO
        _yolo_model  = YOLO(YOLO_PATH)
        YOLO_AVAILABLE = True
        logger.info("[YOLO] Loaded ✅")
        return True
    except Exception as e:
        logger.error(f"[YOLO] Load failed: {e}")
        return False

# Charger au démarrage
_load_yolo()
_load_effnet()


# ============================================================
# TOOL 1 : egg_detector
# ============================================================
@tool
def egg_detector(image_input: str) -> dict:
    """
    Détecte les œufs dans une image via YOLOv8.
    image_input: image base64 (data URI ou raw base64).
    Retourne les crops de chaque œuf détecté.
    """
    # Log ce qu'on reçoit pour le debug
    preview = str(image_input)[:80] if image_input else "None"
    logger.info(f"[egg_detector] Received input (first 80 chars): {preview}")
    logger.info(f"[egg_detector] Input length: {len(image_input) if image_input else 0}")

    # Détecter placeholder
    if _is_placeholder(image_input):
        logger.warning(f"[egg_detector] Placeholder detected — returning clean fallback")
        return {
            "status":     "fallback_placeholder",
            "fallback":   True,
            "eggs_found": 0,
            "eggs":       [],
            "note":       f"Placeholder received: '{str(image_input)[:50]}'"
        }

    # YOLO indisponible → fallback avec image entière
    if not _load_yolo():
        logger.warning("[egg_detector] YOLO unavailable — returning image as single egg")
        return {
            "status":     "yolo_unavailable",
            "fallback":   True,
            "eggs_found": 1,
            "eggs": [{
                "egg_id":          "egg_001_noyolo",
                "bbox":            None,
                "yolo_confidence": 0.0,
                "yolo_class":      "unknown",
                "cropped_image":   image_input,
            }],
            "note": "YOLO model not available — full image treated as single egg"
        }

    # Décodage et inférence
    try:
        img_pil = _b64_to_pil(image_input)
        W, H    = img_pil.size
        logger.info(f"[egg_detector] Image decoded OK: {W}x{H}")

        det   = _yolo_model(img_pil, verbose=False)[0]
        boxes = det.boxes
        logger.info(f"[egg_detector] YOLO detected {len(boxes)} box(es)")

        # 0 détections → image entière comme fallback
        if len(boxes) == 0:
            return {
                "status":     "fallback_no_detection",
                "fallback":   True,
                "eggs_found": 1,
                "eggs": [{
                    "egg_id":          "egg_001_fallback",
                    "bbox":            [0, 0, W, H],
                    "yolo_confidence": 0.0,
                    "yolo_class":      "unknown",
                    "cropped_image":   _pil_to_b64(img_pil),
                }],
                "note": "YOLO detected 0 eggs — full image used as fallback"
            }

        YOLO_CLASSES  = ['A','AA','B','C','D','E']
        detected_eggs = []

        for i, box in enumerate(boxes):
            x1, y1, x2, y2 = map(int, box.xyxy[0].cpu().numpy())
            pad_x = int((x2-x1)*0.10); pad_y = int((y2-y1)*0.10)
            x1 = max(0, x1-pad_x); y1 = max(0, y1-pad_y)
            x2 = min(W, x2+pad_x); y2 = min(H, y2+pad_y)

            cropped  = img_pil.crop((x1, y1, x2, y2))
            crop_b64 = _pil_to_b64(cropped)
            cls_id   = int(box.cls[0])

            detected_eggs.append({
                "egg_id":          f"egg_{i+1:03d}",
                "bbox":            [x1, y1, x2, y2],
                "yolo_confidence": round(float(box.conf[0]), 4),
                "yolo_class":      YOLO_CLASSES[cls_id] if cls_id < len(YOLO_CLASSES) else "unknown",
                "cropped_image":   crop_b64,
            })

        return {
            "status":     "success",
            "eggs_found": len(detected_eggs),
            "eggs":       detected_eggs,
        }

    except Exception as e:
        logger.error(f"[egg_detector] Exception: {e}", exc_info=True)
        return {
            "status":     "error",
            "fallback":   True,
            "eggs_found": 0,
            "eggs":       [],
            "error":      str(e)
        }


# ============================================================
# TOOL 2 : visual_egg_grader
# ============================================================
@tool
def visual_egg_grader(crop_b64: str) -> dict:
    """
    Classifie un crop d'œuf via EfficientNet-B0.
    crop_b64: image croppée en base64.
    Retourne le grade prédit (AA/A/B/C/D/E) et les probabilités.
    """
    preview = str(crop_b64)[:80] if crop_b64 else "None"
    logger.info(f"[visual_egg_grader] Received crop (first 80): {preview}")
    logger.info(f"[visual_egg_grader] Crop length: {len(crop_b64) if crop_b64 else 0}")

    if _is_placeholder(crop_b64):
        logger.warning(f"[visual_egg_grader] Placeholder — returning fallback")
        return {
            "status":          "fallback_placeholder",
            "fallback":        True,
            "predicted_grade": None,
            "confidence":      0.0,
            "note":            f"Placeholder: '{str(crop_b64)[:50]}'"
        }

    if not _load_effnet():
        return {
            "status":          "effnet_unavailable",
            "fallback":        True,
            "predicted_grade": None,
            "confidence":      0.0
        }

    try:
        img    = _b64_to_pil(crop_b64)
        tensor = _EFFNET_TF(img).unsqueeze(0).to(DEVICE)

        with torch.no_grad():
            out   = _effnet_model(tensor)
            probs = torch.softmax(out, 1)[0].cpu().numpy()

        pred_idx   = int(probs.argmax())
        pred_grade = _idx_to_class[pred_idx]
        confidence = float(probs[pred_idx])
        conf_level = "high" if confidence >= 0.80 else ("medium" if confidence >= 0.60 else "low")
        all_probs  = {_idx_to_class[j]: round(float(probs[j]), 4) for j in range(len(probs))}

        logger.info(f"[visual_egg_grader] Grade={pred_grade} conf={confidence:.2f}")
        return {
            "status":            "success",
            "predicted_grade":   pred_grade,
            "confidence":        round(confidence, 4),
            "confidence_level":  conf_level,
            "all_probabilities": all_probs,
            "model":             "EfficientNet-B0",
        }

    except Exception as e:
        logger.error(f"[visual_egg_grader] Exception: {e}", exc_info=True)
        return {
            "status":          "error",
            "fallback":        True,
            "predicted_grade": None,
            "confidence":      0.0,
            "error":           str(e)
        }


# ============================================================
# TOOL 3 : vlm_egg_analyzer
# ============================================================
from groq import Groq as _GroqClient

_groq_client = _GroqClient(api_key=os.getenv("GROQ_API_KEY"))

_VLM_PROMPT = """Analyze this egg image. Return ONLY valid JSON, no markdown:
{
  "crack_detected": false,
  "crack_severity": "none",
  "blood_spot_detected": false,
  "shell_condition": "clean",
  "shape_anomaly": false,
  "quality_score": 0.8,
  "estimated_mm_length": null,
  "estimated_mm_width": null,
  "air_cell_height_mm": null,
  "air_cell_mobile": null,
  "double_yolk_detected": false,
  "fertilized": false,
  "freshness_estimate": "fresh",
  "defects_observed": [],
  "preliminary_grade": "A",
  "reasoning": "brief explanation"
}"""

_VLM_DEFAULTS = {
    "crack_detected":      False,
    "crack_severity":      "none",
    "blood_spot_detected": False,
    "shell_condition":     "unknown",
    "shape_anomaly":       False,
    "quality_score":       0.5,
    "air_cell_height_mm":  None,
    "fertilized":          False,
    "defects_observed":    [],
    "preliminary_grade":   "B",
    "freshness_estimate":  "unknown",
    "reasoning":           "No data available"
}


def _call_groq_vision(image_b64: str) -> dict | None:
    """Appelle Groq Vision directement et retourne le dict parsé."""
    try:
        clean = image_b64.split(",", 1)[1] if "," in image_b64 else image_b64
        clean = re.sub(r'\s', '', clean)
        pad   = 4 - len(clean) % 4
        if pad != 4:
            clean += "=" * pad

        resp = _groq_client.chat.completions.create(
            model=os.getenv("GROQ_VISION_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct"),
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{clean}"}},
                    {"type": "text", "text": _VLM_PROMPT}
                ]
            }],
            max_tokens=800,
            temperature=0
        )

        raw = resp.choices[0].message.content.strip()
        if "```" in raw:
            m = re.search(r'```(?:json)?\s*(.*?)\s*```', raw, re.DOTALL)
            if m: raw = m.group(1)

        return json.loads(raw.strip())

    except Exception as e:
        logger.error(f"[vlm_egg_analyzer] Groq Vision call failed: {e}")
        return None


@tool
async def vlm_egg_analyzer(
    vlm_data: dict | None = None,
    image_normal_b64: str | None = None,
    lot_id: str = ""
) -> dict:
    """
    Returns VLM vision analysis.
    Priority: vlm_data (pre-computed) → image_normal_b64 (Groq Vision) → safe fallback.
    """
    analysis = None
    source   = "unknown"

    # Priorité 1 : données pré-calculées (dict substantiel)
    if vlm_data and isinstance(vlm_data, dict) and len(vlm_data) > 2:
        analysis = vlm_data
        source   = "pre_analysis"
        logger.info(f"[vlm_egg_analyzer] Using pre-computed data ({len(vlm_data)} fields)")

    # Priorité 2 : appel Groq Vision direct
    elif image_normal_b64 and not _is_placeholder(image_normal_b64):
        logger.info(f"[vlm_egg_analyzer] Calling Groq Vision for lot {lot_id}")
        analysis = _call_groq_vision(image_normal_b64)
        source   = "direct_groq_vision"

    # Fallback
    if not analysis:
        logger.warning(f"[vlm_egg_analyzer] No data for lot {lot_id} — safe fallback")
        return {"status": "fallback_no_data", "source": "fallback",
                "fallback": True, "lot_id": lot_id, **_VLM_DEFAULTS}

    # S'assurer que tous les champs critiques existent
    for k, v in _VLM_DEFAULTS.items():
        if k not in analysis:
            analysis[k] = v

    return {"status": "success", "source": source, "fallback": False,
            "lot_id": lot_id, **analysis}


@tool
def candling_analyzer(image_candling_b64: str) -> dict:
    """Deprecated. Use vlm_egg_analyzer instead."""
    return {"error": "Use vlm_egg_analyzer", "fallback": True}