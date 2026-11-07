"""튜닝 세트(30장)만 사용해 매력도 회귀 헤드를 맞춘다. 최종 평가 세트(70장)는 읽지 않는다.

시도(attempt) 정의 — 최대 2회
  1: SFace 얼굴 임베딩(128d, L2 정규화) + Ridge 회귀
  2: 시도 1 + 전처리 개선 (좌우 반전 임베딩 평균(TTA) + 정렬 크롭 밝기 정규화(CLAHE))

usage: python eval/tune.py --attempt 1 [--save]
- Ridge 강도(alpha)는 튜닝 세트 내부 Leave-One-Out 교차검증으로만 선택
- --save: 30장 전체로 다시 맞춘 헤드를 models/beauty_head.json 에 저장
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import cv2
import numpy as np
from scipy.stats import pearsonr, spearmanr

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import beauty  # noqa: E402

DATA = ROOT / "eval" / "data"
ALPHAS = [0.01, 0.03, 0.1, 0.3, 1.0, 3.0, 10.0]


def ridge_fit(X, y, alpha):
    mu = X.mean(axis=0)
    ym = y.mean()
    Xc = X - mu
    w = np.linalg.solve(Xc.T @ Xc + alpha * np.eye(X.shape[1]), Xc.T @ (y - ym))
    return w, float(ym - mu @ w)


def loo(X, y, alpha):
    preds = np.empty_like(y)
    for i in range(len(y)):
        m = np.ones(len(y), bool)
        m[i] = False
        w, b = ridge_fit(X[m], y[m], alpha)
        preds[i] = X[i] @ w + b
    return preds


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--attempt", type=int, choices=[1, 2], required=True)
    ap.add_argument("--save", action="store_true")
    args = ap.parse_args()

    with open(DATA / "labels.csv", encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r["subset"] == "tune"]  # 튜닝 세트만

    cfg = beauty.ATTEMPT_CONFIGS[args.attempt]
    X, y = [], []
    for r in rows:
        img = cv2.imdecode(np.fromfile(str(DATA / "images" / r["image_name"]), np.uint8), cv2.IMREAD_COLOR)
        emb = beauty.embed(img, **cfg)
        if emb is None:
            print("skip (no face):", r["image_name"])
            continue
        X.append(emb)
        y.append(float(r["beauty_score"]))
    X, y = np.array(X), np.array(y)

    results = []
    for a in ALPHAS:
        p = loo(X, y, a)
        results.append({"alpha": a, "loo_plcc": round(float(pearsonr(p, y)[0]), 4),
                        "loo_srocc": round(float(spearmanr(p, y)[0]), 4),
                        "loo_mae": round(float(np.mean(np.abs(p - y))), 4)})
        print(results[-1])
    best = max(results, key=lambda r: r["loo_plcc"])
    print("best (by LOO PLCC on tune set):", best)

    if args.save:
        w, b = ridge_fit(X, y, best["alpha"])
        out = {
            "attempt": args.attempt,
            "config": cfg,
            "alpha": best["alpha"],
            "tune_loo": best,
            "n_tune": int(len(y)),
            "weights": [round(float(v), 7) for v in w],
            "bias": round(b, 6),
            "train_label_range": [float(y.min()), float(y.max())],
            "source": "SCUT-FBP5500 (HF MnLgt/scut-fbp5500) tune subset only, non-commercial academic use",
        }
        (ROOT / "models" / "beauty_head.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
        print("saved models/beauty_head.json")


if __name__ == "__main__":
    main()
