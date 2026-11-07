"""AI 외모 평가 웹앱 (재미용 추정치)

- 얼굴 검출/특징점: OpenCV YuNet (face_detection_yunet_2023mar.onnx, CPU)
- 점수: 특징점 기하(대칭·비율) + 이미지 분석(좌우 픽셀 대칭, 선명도, 노출, 피부톤 균일도, 구도)
- 업로드 이미지는 메모리에서만 처리하며 디스크에 저장하지 않음

실행:  python app.py   (기본 http://127.0.0.1:8000)
"""
from __future__ import annotations

import math
import os
import time
from pathlib import Path

import cv2
import numpy as np
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

BASE_DIR = Path(__file__).resolve().parent
MODEL_PATH = BASE_DIR / "models" / "face_detection_yunet_2023mar.onnx"
STATIC_DIR = BASE_DIR / "static"

# 배포 버전 식별용 (CI에서 빌드 인자로 주입, 무중단 배포 검증 시 응답에 포함)
APP_VERSION = os.environ.get("APP_VERSION", "1.0.0")
APP_REVISION = os.environ.get("APP_REVISION", "local")

MAX_BYTES = 10 * 1024 * 1024  # 10MB
ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp", "image/bmp"}
DETECT_MAX_SIDE = 640  # 검출용 축소 크기 (CPU 속도)

DISCLAIMER = (
    "이 결과는 재미용 AI 추정치입니다. 사진의 각도·조명·화질에 따라 크게 달라지며, "
    "실제 외모·매력·개인의 가치를 판단하지 않습니다."
)


# ---------------------------------------------------------------------------
# 모델 로딩
# ---------------------------------------------------------------------------
def load_detector() -> cv2.FaceDetectorYN:
    if not MODEL_PATH.exists():
        raise RuntimeError(f"모델 파일이 없습니다: {MODEL_PATH}")
    # OpenCV는 Windows에서 한글 등 비 ASCII 경로의 모델 파일을 직접 열지 못하므로
    # Python으로 읽은 바이트 버퍼로 로드한다.
    buf = np.frombuffer(MODEL_PATH.read_bytes(), dtype=np.uint8)
    return cv2.FaceDetectorYN.create(
        "onnx", buf, np.array([], dtype=np.uint8), (320, 320), 0.7, 0.3, 50
    )


DETECTOR = load_detector()


# ---------------------------------------------------------------------------
# 유틸
# ---------------------------------------------------------------------------
def clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return float(max(lo, min(hi, v)))


def gauss_score(value: float, ideal: float, tolerance: float) -> float:
    """ideal에서 tolerance만큼 벗어나면 약 60점이 되는 종형 점수 (0~100)."""
    return 100.0 * math.exp(-0.5 * ((value - ideal) / tolerance) ** 2)


class AnalysisError(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status, self.code, self.message = status, code, message


def decode_image(data: bytes) -> np.ndarray:
    arr = np.frombuffer(data, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None or img.size == 0:
        raise AnalysisError(400, "INVALID_IMAGE", "이미지를 해석할 수 없습니다. 손상되지 않은 JPG/PNG/WEBP 파일을 올려주세요.")
    h, w = img.shape[:2]
    if min(h, w) < 64:
        raise AnalysisError(422, "IMAGE_TOO_SMALL", "이미지가 너무 작습니다 (최소 64×64 픽셀).")
    return img


def detect_faces(img: np.ndarray):
    h, w = img.shape[:2]
    scale = min(1.0, DETECT_MAX_SIDE / max(h, w))
    small = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA) if scale < 1 else img
    sh, sw = small.shape[:2]
    DETECTOR.setInputSize((sw, sh))
    _, faces = DETECTOR.detect(small)
    if faces is None:
        return [], scale
    out = []
    for f in faces:
        f = f.copy()
        f[:14] /= scale  # 원본 좌표로 환원 (bbox 4 + landmarks 10)
        out.append(f)
    out.sort(key=lambda f: f[2] * f[3], reverse=True)
    return out, scale


# ---------------------------------------------------------------------------
# 점수 계산
# ---------------------------------------------------------------------------
def analyze(img: np.ndarray) -> dict:
    t0 = time.perf_counter()
    H, W = img.shape[:2]
    faces, _ = detect_faces(img)
    if not faces:
        raise AnalysisError(
            422, "NO_FACE",
            "얼굴을 찾지 못했습니다. 정면을 바라보고 얼굴이 잘 보이는 밝은 사진을 사용해 주세요.",
        )
    f = faces[0]
    x, y, bw, bh = [float(v) for v in f[:4]]
    conf = float(f[14])
    lm = f[4:14].reshape(5, 2).astype(np.float64)
    # YuNet 순서: 오른쪽 눈(화면 왼쪽), 왼쪽 눈, 코끝, 오른쪽 입꼬리, 왼쪽 입꼬리
    eye_r, eye_l, nose, mouth_r, mouth_l = lm

    if bw < 48 or bh < 48:
        raise AnalysisError(422, "FACE_TOO_SMALL", "얼굴이 너무 작게 나왔습니다. 얼굴이 더 크게 나온 사진을 사용해 주세요.")

    iod = float(np.linalg.norm(eye_l - eye_r))  # 눈 사이 거리
    if iod < 1:
        raise AnalysisError(422, "BAD_LANDMARKS", "얼굴 특징점을 안정적으로 찾지 못했습니다. 다른 사진을 사용해 주세요.")

    eye_mid = (eye_l + eye_r) / 2
    mouth_mid = (mouth_l + mouth_r) / 2

    # --- 1) 기하 대칭: 눈 중점-입 중점 축 기준 좌우 특징점 반사 오차 ---
    axis = mouth_mid - eye_mid
    axis_len = np.linalg.norm(axis)
    axis_u = axis / axis_len if axis_len > 0 else np.array([0.0, 1.0])
    normal = np.array([-axis_u[1], axis_u[0]])

    def side_dist(p):  # 축으로부터의 부호 있는 거리
        return float(np.dot(p - eye_mid, normal))

    eye_asym = abs(abs(side_dist(eye_l)) - abs(side_dist(eye_r))) / iod
    mouth_asym = abs(abs(side_dist(mouth_l)) - abs(side_dist(mouth_r))) / iod
    nose_off = abs(side_dist(nose)) / iod
    eye_height_diff = abs(float(np.dot(eye_l - eye_r, axis_u))) / iod
    mouth_height_diff = abs(float(np.dot(mouth_l - mouth_r, axis_u))) / iod
    geo_err = 0.9 * eye_asym + 0.8 * mouth_asym + 0.9 * nose_off + 0.6 * eye_height_diff + 0.6 * mouth_height_diff
    geo_sym = clamp(100 - geo_err * 260)

    # 고개 기울기(roll)
    roll = math.degrees(math.atan2(eye_l[1] - eye_r[1], eye_l[0] - eye_r[0]))

    # --- 2) 픽셀 대칭: 눈 수평 정렬 후 얼굴 좌우 반전 비교 ---
    M = cv2.getRotationMatrix2D((float(eye_mid[0]), float(eye_mid[1])), roll, 1.0)
    aligned = cv2.warpAffine(img, M, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
    nose_a = M @ np.array([nose[0], nose[1], 1.0])
    mid_x = (float((M @ np.array([*eye_mid, 1.0]))[0]) + float(nose_a[0])) / 2
    half = int(iod * 1.05)
    top = int(eye_mid[1] - iod * 0.9)
    bot = int(mouth_mid[1] + iod * 0.55)
    x0, x1 = int(mid_x - half), int(mid_x + half)
    pix_sym = None
    face_crop = None
    if x0 >= 0 and top >= 0 and x1 <= W and bot <= H and bot - top > 20 and half > 10:
        face_crop = aligned[top:bot, x0:x1]
        g = cv2.cvtColor(face_crop, cv2.COLOR_BGR2GRAY)
        g = cv2.resize(g, (128, 128), interpolation=cv2.INTER_AREA)
        g = cv2.GaussianBlur(g, (5, 5), 0)
        g = cv2.equalizeHist(g).astype(np.float64)
        left, right = g[:, :64], np.fliplr(g[:, 64:])
        lz = (left - left.mean()) / (left.std() + 1e-6)
        rz = (right - right.mean()) / (right.std() + 1e-6)
        corr = float((lz * rz).mean())
        pix_sym = clamp((corr - 0.2) / 0.7 * 100)
    symmetry = geo_sym if pix_sym is None else 0.65 * geo_sym + 0.35 * pix_sym

    # --- 3) 얼굴 비율(균형) ---
    eye_mouth = float(np.linalg.norm(mouth_mid - eye_mid))
    mouth_w = float(np.linalg.norm(mouth_l - mouth_r))
    nose_pos = float(np.dot(nose - eye_mid, axis_u)) / (axis_len + 1e-6)  # 눈~입 사이 코 위치 (0~1)
    r1 = eye_mouth / iod  # 이상치 약 1.0~1.1
    r2 = mouth_w / iod  # 이상치 약 0.75~0.85
    r3 = iod / bw  # 이상치 약 0.40~0.45
    proportion = (
        0.4 * gauss_score(r1, 1.05, 0.18)
        + 0.3 * gauss_score(r2, 0.8, 0.16)
        + 0.15 * gauss_score(r3, 0.43, 0.08)
        + 0.15 * gauss_score(nose_pos, 0.55, 0.15)
    )

    # --- 4) 스타일/사진 연출: 선명도, 노출, 대비, 구도, 피부톤 균일도 ---
    fx0, fy0 = max(0, int(x)), max(0, int(y))
    fx1, fy1 = min(W, int(x + bw)), min(H, int(y + bh))
    face_roi = img[fy0:fy1, fx0:fx1]
    gray_face = cv2.cvtColor(face_roi, cv2.COLOR_BGR2GRAY)
    gray_face_n = cv2.resize(gray_face, (160, int(160 * gray_face.shape[0] / max(1, gray_face.shape[1]))))
    lap_var = float(cv2.Laplacian(gray_face_n, cv2.CV_64F).var())
    sharpness = clamp(math.log10(lap_var + 1) / math.log10(400) * 100)
    mean_l = float(gray_face.mean())
    exposure = gauss_score(mean_l, 140, 45)
    contrast_v = float(gray_face.std())
    contrast = gauss_score(contrast_v, 55, 25)
    face_cx, face_cy = (x + bw / 2) / W, (y + bh / 2) / H
    area_ratio = (bw * bh) / (W * H)
    composition = 0.5 * gauss_score(math.hypot(face_cx - 0.5, face_cy - 0.45), 0.0, 0.22) + 0.5 * gauss_score(
        area_ratio, 0.18, 0.14
    )

    # 피부톤 균일도: 볼 영역의 Lab 색 편차
    def patch(cx, cy, r):
        cx, cy, r = int(cx), int(cy), max(3, int(r))
        p = img[max(0, cy - r):min(H, cy + r), max(0, cx - r):min(W, cx + r)]
        return p if p.size else None

    cheek_r = patch(eye_r[0], (eye_r[1] + mouth_r[1]) / 2, iod * 0.15)
    cheek_l = patch(eye_l[0], (eye_l[1] + mouth_l[1]) / 2, iod * 0.15)
    skin = 70.0
    if cheek_r is not None and cheek_l is not None:
        lab_r = cv2.cvtColor(cheek_r, cv2.COLOR_BGR2LAB).reshape(-1, 3).astype(np.float64)
        lab_l = cv2.cvtColor(cheek_l, cv2.COLOR_BGR2LAB).reshape(-1, 3).astype(np.float64)
        within = (lab_r.std(axis=0).mean() + lab_l.std(axis=0).mean()) / 2
        between = float(np.linalg.norm(lab_r.mean(axis=0) - lab_l.mean(axis=0)))
        skin = clamp(100 - within * 4.0 - between * 1.2)

    style = 0.3 * sharpness + 0.2 * exposure + 0.15 * contrast + 0.2 * composition + 0.15 * skin

    # 고개 기울기 페널티(포즈)
    pose = gauss_score(abs(roll), 0, 12)

    # --- 종합 점수 (재미용으로 55~98 범위에 매핑) ---
    raw = 0.35 * symmetry + 0.25 * proportion + 0.30 * style + 0.10 * pose
    total = round(55 + raw / 100 * 43, 1)

    details = {
        "symmetry": round(symmetry, 1),
        "proportion": round(proportion, 1),
        "style": round(style, 1),
        "skin": round(skin, 1),
        "pose": round(pose, 1),
    }
    style_details = {
        "sharpness": round(sharpness, 1),
        "exposure": round(exposure, 1),
        "contrast": round(contrast, 1),
        "composition": round(composition, 1),
    }
    report = build_report(total, details, style_details, roll, mean_l, len(faces))

    return {
        "ok": True,
        "total_score": total,
        "max_score": 100,
        "grade": grade_of(total),
        "scores": details,
        "style_details": style_details,
        "metrics": {
            "roll_degrees": round(roll, 1),
            "geometric_symmetry": round(geo_sym, 1),
            "pixel_symmetry": None if pix_sym is None else round(pix_sym, 1),
            "eye_mouth_ratio": round(r1, 3),
            "mouth_eye_ratio": round(r2, 3),
            "face_brightness": round(mean_l, 1),
            "detection_confidence": round(conf, 3),
        },
        "faces_detected": len(faces),
        "face": {
            "box": [x / W, y / H, bw / W, bh / H],
            "landmarks": [[float(p[0] / W), float(p[1] / H)] for p in lm],
        },
        "image_size": [W, H],
        "report": report,
        "disclaimer": DISCLAIMER,
        "model": "OpenCV YuNet face_detection_yunet_2023mar (CPU) + 특징점 기하/이미지 분석 휴리스틱",
        "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1),
        "version": APP_VERSION,
        "revision": APP_REVISION,
    }


def grade_of(total: float) -> str:
    if total >= 90:
        return "S"
    if total >= 82:
        return "A"
    if total >= 74:
        return "B"
    if total >= 66:
        return "C"
    return "D"


def build_report(total, d, s, roll, brightness, n_faces) -> list[str]:
    lines = []
    if total >= 90:
        lines.append("전체적으로 매우 조화로운 인상으로 추정됩니다. 사진 연출까지 훌륭해요! ✨")
    elif total >= 82:
        lines.append("균형 잡히고 호감 가는 인상으로 추정됩니다.")
    elif total >= 74:
        lines.append("안정적인 인상입니다. 사진 조건을 조금만 바꿔도 점수가 더 오를 수 있어요.")
    else:
        lines.append("사진 조건(조명·각도·화질)의 영향이 커 보입니다. 다른 사진으로도 시도해 보세요.")

    if d["symmetry"] >= 80:
        lines.append(f"얼굴 대칭성 {d['symmetry']}점: 좌우 눈·입꼬리·코의 위치가 고르게 정렬되어 있습니다.")
    elif d["symmetry"] >= 60:
        lines.append(f"얼굴 대칭성 {d['symmetry']}점: 대체로 대칭적이며, 표정이나 각도에 따른 약간의 차이가 보입니다.")
    else:
        lines.append(f"얼굴 대칭성 {d['symmetry']}점: 좌우 차이가 크게 측정되었습니다. 정면 사진이면 더 정확해집니다.")

    if d["proportion"] >= 75:
        lines.append(f"얼굴 비율 {d['proportion']}점: 눈 사이 거리와 눈~입 거리의 비율이 균형적입니다.")
    else:
        lines.append(f"얼굴 비율 {d['proportion']}점: 개성 있는 비율로, 카메라 거리·렌즈 왜곡의 영향도 있을 수 있습니다.")

    lines.append(
        f"스타일/연출 {d['style']}점 (선명도 {s['sharpness']}, 노출 {s['exposure']}, 대비 {s['contrast']}, 구도 {s['composition']})."
    )
    tips = []
    if s["sharpness"] < 60:
        tips.append("초점이 맞은 선명한 사진")
    if brightness < 90:
        tips.append("더 밝은 조명")
    elif brightness > 200:
        tips.append("과노출을 줄인 조명")
    if abs(roll) > 8:
        tips.append(f"고개 기울기 줄이기(현재 약 {abs(roll):.0f}°)")
    if s["composition"] < 55:
        tips.append("얼굴이 중앙에 적당한 크기로 나오도록 구도 조정")
    if tips:
        lines.append("점수 향상 팁: " + ", ".join(tips) + ".")
    if n_faces > 1:
        lines.append(f"사진에서 얼굴 {n_faces}개가 감지되어 가장 크게 나온 얼굴만 평가했습니다.")
    return lines


# ---------------------------------------------------------------------------
# FastAPI
# ---------------------------------------------------------------------------
app = FastAPI(title="AI 외모 평가 (재미용)", version=APP_VERSION)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.exception_handler(AnalysisError)
async def analysis_error_handler(_: Request, exc: AnalysisError):
    return JSONResponse(status_code=exc.status, content={"ok": False, "code": exc.code, "error": exc.message})


@app.exception_handler(HTTPException)
async def http_error_handler(_: Request, exc: HTTPException):
    detail = exc.detail if isinstance(exc.detail, dict) else {"code": "HTTP_ERROR", "error": str(exc.detail)}
    return JSONResponse(status_code=exc.status_code, content={"ok": False, **detail})


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
async def health():
    return {
        "ok": True,
        "model": MODEL_PATH.name,
        "opencv": cv2.__version__,
        "version": APP_VERSION,
        "revision": APP_REVISION,
    }


@app.post("/api/analyze")
async def analyze_endpoint(file: UploadFile = File(...)):
    if file.content_type not in ALLOWED_TYPES:
        raise HTTPException(
            415, {"code": "UNSUPPORTED_TYPE", "error": f"지원하지 않는 파일 형식입니다 ({file.content_type}). JPG, PNG, WEBP, BMP만 가능합니다."}
        )
    data = await file.read(MAX_BYTES + 1)
    if len(data) == 0:
        raise HTTPException(400, {"code": "EMPTY_FILE", "error": "빈 파일입니다."})
    if len(data) > MAX_BYTES:
        raise HTTPException(413, {"code": "FILE_TOO_LARGE", "error": "파일이 너무 큽니다 (최대 10MB)."})
    img = decode_image(data)
    try:
        return analyze(img)
    except AnalysisError:
        raise
    except Exception as e:  # 예상치 못한 오류도 JSON으로 안내
        raise HTTPException(500, {"code": "INTERNAL_ERROR", "error": f"분석 중 오류가 발생했습니다: {type(e).__name__}"})


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", "8000"))
    host = os.environ.get("HOST", "127.0.0.1")  # 컨테이너에서는 0.0.0.0
    print(f"\n  AI 외모 평가 앱 실행 중 → http://127.0.0.1:{port}  (종료: Ctrl+C)\n")
    uvicorn.run(app, host=host, port=port)
