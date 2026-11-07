"""앱의 점수가 사람 평가(SCUT-FBP5500 beauty_score, 1~5)와 얼마나 일치하는지 측정.

usage: python eval/evaluate.py [--subset tune|eval|all] [--out eval/results/<name>.json]

지표 (얼굴 매력도 예측 분야 표준 지표)
- PLCC: Pearson 상관계수 (선형 일치도)
- SROCC: Spearman 순위 상관계수 (순위 일치도)
- MAE / RMSE: 앱 점수(55~98)를 사람 점수(1~5) 척도로 바꾼 뒤의 오차.
  척도 변환(선형 회귀)은 튜닝 세트 30장으로만 맞추고 평가 세트에는 그대로 적용한다.
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
import app  # noqa: E402

DATA = ROOT / "eval" / "data"


def load_rows():
    with open(DATA / "labels.csv", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def run(rows):
    out = []
    for r in rows:
        img = cv2.imdecode(np.fromfile(str(DATA / "images" / r["image_name"]), np.uint8), cv2.IMREAD_COLOR)
        try:
            res = app.analyze(img)
            out.append({**r, "pred": res["total_score"], "scores": res["scores"], "style_details": res["style_details"],
                        "metrics": res["metrics"], "error": None})
        except app.AnalysisError as e:
            out.append({**r, "pred": None, "error": e.code})
    return out


def fit_map(preds, labels):
    a, b = np.polyfit(preds, labels, 1)
    return float(a), float(b)


def metrics(items, mapping):
    ok = [x for x in items if x["pred"] is not None]
    p = np.array([x["pred"] for x in ok], float)
    y = np.array([float(x["beauty_score"]) for x in ok], float)
    mapped = mapping[0] * p + mapping[1]
    return {
        "n": len(items),
        "n_scored": len(ok),
        "failures": len(items) - len(ok),
        "plcc": round(float(pearsonr(p, y)[0]), 4),
        "srocc": round(float(spearmanr(p, y)[0]), 4),
        "mae": round(float(np.mean(np.abs(mapped - y))), 4),
        "rmse": round(float(np.sqrt(np.mean((mapped - y) ** 2))), 4),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    items = run(load_rows())
    tune = [x for x in items if x["subset"] == "tune"]
    ev = [x for x in items if x["subset"] == "eval"]
    tune_ok = [x for x in tune if x["pred"] is not None]
    mapping = fit_map([x["pred"] for x in tune_ok], [float(x["beauty_score"]) for x in tune_ok])

    report = {
        "scoring_version": getattr(app, "SCORING_VERSION", "v1-baseline"),
        "mapping_from_tune": {"slope": round(mapping[0], 5), "intercept": round(mapping[1], 4)},
        "tune": metrics(tune, mapping),
        "eval": metrics(ev, mapping),
    }
    # 튜닝 세트에서만: 세부 점수별 사람 점수와의 상관 (오류 분석용)
    keys = list(tune_ok[0]["scores"].keys()) + list(tune_ok[0]["style_details"].keys())
    y = [float(x["beauty_score"]) for x in tune_ok]
    report["tune_component_corr"] = {
        k: round(float(pearsonr([{**x["scores"], **x["style_details"]}[k] for x in tune_ok], y)[0]), 3) for k in keys
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps({"report": report, "items": items}, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
