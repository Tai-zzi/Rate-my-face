"""매력도 예측 v2: 사전학습 얼굴 임베딩(OpenCV Zoo SFace) + 소규모 Ridge 회귀 헤드.

- SFace(face_recognition_sface_2021dec.onnx, Apache-2.0)는 얼굴 인식용으로 사전학습된 모델이며
  SCUT-FBP5500 으로 학습되지 않았다. 대규모 재학습 없이 128차원 임베딩만 추출한다.
- 회귀 헤드(models/beauty_head.json)는 공개 데이터 SCUT-FBP5500 의 튜닝용 30장으로만 맞췄다.
- 결과는 데이터셋 평가자들의 평균 취향을 흉내 낸 재미용 추정치이다.
"""
from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np

BASE_DIR = Path(__file__).resolve().parent
SFACE_PATH = BASE_DIR / "models" / "face_recognition_sface_2021dec.onnx"
HEAD_PATH = BASE_DIR / "models" / "beauty_head.json"

# 튜닝 시도별 전처리 설정 (eval/tune.py 와 공유)
ATTEMPT_CONFIGS = {
    1: {"flip_tta": False, "clahe": False},
    2: {"flip_tta": True, "clahe": True},
}

_recognizer = None
_detector = None


def _load_recognizer():
    global _recognizer
    if _recognizer is None:
        path = str(SFACE_PATH)
        if not path.isascii():  # OpenCV는 비 ASCII 경로를 못 여므로 임시 폴더로 복사
            import shutil
            import tempfile
            path = str(Path(tempfile.gettempdir()) / SFACE_PATH.name)
            if not Path(path).exists():
                shutil.copyfile(SFACE_PATH, path)
        _recognizer = cv2.FaceRecognizerSF.create(path, "")
    return _recognizer


def _load_detector():
    global _detector
    if _detector is None:
        import app  # 앱의 YuNet 검출기를 재사용 (튜닝 스크립트용)
        _detector = app
    return _detector


def _feature(crop: np.ndarray) -> np.ndarray:
    f = _load_recognizer().feature(crop).reshape(-1).astype(np.float64)
    return f / (np.linalg.norm(f) + 1e-12)


def _clahe(crop: np.ndarray) -> np.ndarray:
    lab = cv2.cvtColor(crop, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    l = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(4, 4)).apply(l)
    return cv2.cvtColor(cv2.merge([l, a, b]), cv2.COLOR_LAB2BGR)


def embed_face(img: np.ndarray, face_row: np.ndarray, flip_tta: bool = False, clahe: bool = False):
    """YuNet 검출 결과(15값, 원본 좌표)로 정렬 크롭 후 SFace 임베딩."""
    crop = _load_recognizer().alignCrop(img, face_row.astype(np.float32))
    if clahe:
        crop = _clahe(crop)
    f = _feature(crop)
    if flip_tta:
        f = f + _feature(cv2.flip(crop, 1))
        f = f / (np.linalg.norm(f) + 1e-12)
    return f


def embed(img: np.ndarray, flip_tta: bool = False, clahe: bool = False):
    faces, _ = _load_detector().detect_faces(img)
    if not faces:
        return None
    return embed_face(img, faces[0], flip_tta=flip_tta, clahe=clahe)


class BeautyHead:
    def __init__(self, path: Path = HEAD_PATH):
        d = json.loads(path.read_text(encoding="utf-8"))
        self.w = np.array(d["weights"], dtype=np.float64)
        self.b = float(d["bias"])
        self.config = d["config"]
        self.meta = {k: d[k] for k in ("attempt", "alpha", "n_tune")}
        _load_recognizer()  # 시작 시 모델 로드 검증

    def predict(self, img: np.ndarray, face_row: np.ndarray) -> float:
        """사람 평가 척도(1~5)의 예측 매력도."""
        f = embed_face(img, face_row, **self.config)
        return float(np.clip(f @ self.w + self.b, 1.0, 5.0))
