"""SCUT-FBP5500 (Hugging Face: MnLgt/scut-fbp5500) 에서 100장만 내려받아 30/70으로 분리.

- 이용 조건: SCUT-FBP5500 은 비상업적 학술 연구 목적에 한해 사용 가능 (원본: HCIILAB/SCUT-FBP5500-Database-Release)
- test split(2200장)에서 고정 시드로 100개 인덱스를 무작위 추출 → 앞 30개 튜닝용, 뒤 70개 최종 평가용
- 이미지와 라벨은 eval/data/ 에만 저장 (Git 커밋 제외)
"""
from __future__ import annotations

import csv
import json
import random
import time
import urllib.request
from pathlib import Path

DATASET = "MnLgt/scut-fbp5500"
SPLIT = "test"
SPLIT_SIZE = 2200
N, N_TUNE, SEED = 100, 30, 2026

OUT = Path(__file__).resolve().parent / "data"
API = "https://datasets-server.huggingface.co/rows?dataset={ds}&config=default&split={sp}&offset={o}&length=1"


def get(url: str, tries: int = 8) -> bytes:
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "hw3-eval"}), timeout=30) as r:
                return r.read()
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(10 * (i + 1))  # 429 rate limit backoff
    raise RuntimeError("unreachable")


def main() -> None:
    (OUT / "images").mkdir(parents=True, exist_ok=True)
    (OUT / "meta").mkdir(parents=True, exist_ok=True)
    idx = random.Random(SEED).sample(range(SPLIT_SIZE), N)
    rows = []
    for k, o in enumerate(idx):
        cache = OUT / "meta" / f"{o}.json"
        if cache.exists():
            meta = json.loads(cache.read_text(encoding="utf-8"))
        else:
            meta = json.loads(get(API.format(ds=DATASET.replace("/", "%2F"), sp=SPLIT, o=o)))["rows"][0]["row"]
            time.sleep(1.5)
        name = meta["image_name"]
        if not (OUT / "images" / name).exists():
            if "src" not in meta["image"] or cache.exists():
                meta = json.loads(get(API.format(ds=DATASET.replace("/", "%2F"), sp=SPLIT, o=o)))["rows"][0]["row"]
                time.sleep(1.5)
            (OUT / "images" / name).write_bytes(get(meta["image"]["src"]))
        cache.write_text(json.dumps({kk: v for kk, v in meta.items() if kk != "image"} | {"image": {}}, ensure_ascii=False), encoding="utf-8")
        rows.append({
            "image_name": name,
            "beauty_score": round(float(meta["beauty_score"]), 4),
            "gender": meta.get("gender", ""),
            "race": meta.get("race", ""),
            "subset": "tune" if k < N_TUNE else "eval",
            "source_index": o,
        })
        print(f"{k + 1:3d}/{N} {name} score={rows[-1]['beauty_score']} ({rows[-1]['subset']})")
    with open(OUT / "labels.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print("saved", OUT / "labels.csv")


if __name__ == "__main__":
    main()
