# AI 외모 점수 측정기 (재미용)

> 점수와 평가는 **재미용 AI 추정치**입니다. 실제 외모·매력·개인의 가치를 판단하지 않습니다.

## 실행 (PowerShell, 가상환경 없음)
```powershell
cd C:\경희대학교\face-score-app
python -m pip install -r requirements.txt   # 최초 1회
python app.py
```
브라우저에서 http://127.0.0.1:8000 접속 → 사진 선택 → "외모 점수 분석하기". 종료는 `Ctrl+C`.
다른 포트: `$env:PORT=8010; python app.py`

## 구성
- `app.py` — FastAPI 서버 + 분석 로직 (`POST /api/analyze`, `GET /api/health`)
- `static/` — 화면 (HTML/CSS/JS)
- `models/face_detection_yunet_2023mar.onnx` — OpenCV YuNet 얼굴 검출 + 5개 특징점 (CPU)
- `samples/` — 테스트용 이미지 (OpenCV 공식 샘플)

## 점수 방식
종합 = 대칭성 35% + 비율 25% + 스타일/사진 연출 30% + 자세 10% → 55~98점 범위로 매핑.
- 대칭성: 특징점(눈·코·입꼬리) 좌우 반사 오차 + 정렬된 얼굴의 좌우 픽셀 상관도
- 비율: 눈 사이 거리 대비 눈~입 거리, 입 너비, 코 위치
- 스타일: 선명도, 노출, 대비, 구도, 피부톤 균일도

## 참고
- OpenCV는 한글 경로의 모델 파일을 직접 열지 못하므로, 모델을 바이트 버퍼로 읽어 로드합니다.
- 업로드 사진은 메모리에서만 처리하며 저장하지 않습니다. 서버는 127.0.0.1에만 바인딩됩니다.
