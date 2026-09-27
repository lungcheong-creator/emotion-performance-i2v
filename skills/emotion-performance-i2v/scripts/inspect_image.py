#!/usr/bin/env python3
"""素材體檢 — I2V 前置檢查。

檢查四項並輸出 JSON 報告：
  1. 有效內容邊界（白底 / 黑邊條帶偵測）
  2. 臉部可見度（膚色區域占比與位置分布）
  3. 解析度與比例
  4. 色調統計（驗證是否與系列基調一致）

verdict: PASS / WARN / BLOCK
"""
import argparse
import json
import sys

try:
    from PIL import Image
    import numpy as np
except ImportError:
    print(json.dumps({
        "verdict": "ERROR",
        "error": "需要 Pillow 與 numpy。安裝：pip install pillow numpy",
    }, ensure_ascii=False))
    sys.exit(2)

WHITE_TH, BLACK_TH = 200, 40


def band_check(a):
    """偵測四周的純白 / 純黑條帶。"""
    h, w = a.shape
    out = {"white": {}, "black": {}}
    rows_dark = a.min(axis=1)
    rows_bright = a.max(axis=1)
    cols_dark = a.min(axis=0)
    cols_bright = a.max(axis=0)

    def run_len(vals, pred, reverse=False):
        idx = range(len(vals) - 1, -1, -1) if reverse else range(len(vals))
        n = 0
        for i in idx:
            if pred(vals[i]):
                n += 1
            else:
                break
        return n

    white_row = lambda v: v > WHITE_TH          # noqa: E731
    black_row = lambda v: v < BLACK_TH          # noqa: E731
    bright_col = lambda v: v > WHITE_TH         # noqa: E731
    dark_col = lambda v: v < BLACK_TH           # noqa: E731

    out["white"] = {
        "top": run_len(rows_dark, white_row),
        "bottom": run_len(rows_dark, white_row, reverse=True),
        "left": run_len(cols_dark, bright_col),
        "right": run_len(cols_dark, bright_col, reverse=True),
    }
    out["black"] = {
        "top": run_len(rows_bright, black_row),
        "bottom": run_len(rows_bright, black_row, reverse=True),
        "left": run_len(cols_bright, dark_col),
        "right": run_len(cols_bright, dark_col, reverse=True),
    }
    out["white_pct"] = {
        k: round(v / (h if k in ("top", "bottom") else w) * 100, 1)
        for k, v in out["white"].items()
    }
    out["black_pct"] = {
        k: round(v / (h if k in ("top", "bottom") else w) * 100, 1)
        for k, v in out["black"].items()
    }
    return out


def content_bbox(arr):
    """非白非黑的內容邊界。arr: HxWx3 ndarray。"""
    g = np.asarray(arr).mean(axis=2)
    mask = (g < WHITE_TH - 25) & (g > BLACK_TH)
    rows = np.where(mask.any(axis=1))[0]
    cols = np.where(mask.any(axis=0))[0]
    if not len(rows) or not len(cols):
        return None
    h, w = g.shape
    return {
        "x": int(cols.min()), "y": int(rows.min()),
        "w": int(cols.max() - cols.min() + 1),
        "h": int(rows.max() - rows.min() + 1),
        "height_pct": round(float(rows.max() - rows.min() + 1) / h * 100, 1),
        "width_pct": round(float(cols.max() - cols.min() + 1) / w * 100, 1),
    }


def skin_stats(arr):
    """粗估膚色像素：YCbCr 範圍法。回傳占比與垂直重心。arr: HxWx3 ndarray。"""
    arr = np.asarray(arr).astype(np.float32)
    r, g, b = arr[..., 0], arr[..., 1], arr[..., 2]
    y = 0.299 * r + 0.587 * g + 0.114 * b
    cb = 128 - 0.168736 * r - 0.331264 * g + 0.5 * b
    cr = 128 + 0.5 * r - 0.418688 * g - 0.081312 * b
    mask = (y > 60) & (cb > 77) & (cb < 135) & (cr > 133) & (cr < 180)
    ratio = float(mask.mean())
    if mask.sum() == 0:
        return {"ratio": 0.0, "centroid_y_pct": None}
    ys, xs = np.where(mask)
    h = arr.shape[0]
    return {
        "ratio": round(ratio, 4),
        "centroid_y_pct": round(float(ys.mean()) / h * 100, 1),
        "centroid_x_pct": round(float(xs.mean()) / arr.shape[1] * 100, 1),
        "bbox_y_pct": [round(float(ys.min()) / h * 100, 1),
                       round(float(ys.max()) / h * 100, 1)],
    }


def tone_stats(arr):
    """arr: HxWx3 ndarray。"""
    arr = np.asarray(arr).astype(np.float32)
    mx, mn = arr.max(axis=2), arr.min(axis=2)
    sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1), 0)
    g = arr.mean(axis=2)
    return {
        "luma_mean": round(float(g.mean()), 1),
        "luma_std": round(float(g.std()), 1),
        "saturation_mean": round(float(sat.mean()), 3),
        "hint": "低飽和 (<0.25) + 中低亮度 = 冷調寫實基調" if sat.mean() < 0.25
                else "飽和度偏高，與冷調低飽和系列基調不一致",
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("image")
    p.add_argument("--json", action="store_true", help="只輸出 JSON")
    args = p.parse_args()

    im = Image.open(args.image)
    w, h = im.size
    rgb = im.convert("RGB")
    arr = np.asarray(rgb)
    gray = arr.mean(axis=2)

    report = {
        "file": args.image,
        "size": [w, h],
        "aspect": round(w / h, 3),
        "megapixels": round(w * h / 1e6, 2),
    }
    report["bands"] = band_check(gray)
    report["content_bbox"] = content_bbox(arr)
    report["skin"] = skin_stats(arr)
    report["tone"] = tone_stats(arr)

    reasons, verdict = [], "PASS"

    wb = report["bands"]["white_pct"]
    lb = report["bands"]["black_pct"]
    worst_white = max(wb.values())
    worst_black = max(lb.values())
    if worst_white > 5:
        verdict = "BLOCK"
        reasons.append(f"偵測到純白條帶（最大 {worst_white}%），模型會把白底學進畫面，先裁切或換圖")
    if worst_black > 5:
        verdict = "BLOCK"
        reasons.append(f"偵測到純黑邊（最大 {worst_black}%），同上")

    cb = report["content_bbox"]
    if cb and cb["height_pct"] < 60 and verdict != "BLOCK":
        verdict = "BLOCK"
        reasons.append(f"有效內容僅占畫面高度 {cb['height_pct']}%，構圖資訊不足")

    sk = report["skin"]
    if sk["ratio"] < 0.02:
        if verdict != "BLOCK":
            verdict = "WARN"
        reasons.append("膚色像素極少，可能看不到完整臉部——環繞或轉頭時模型會腦補五官（換臉風險最高）")
    elif sk["ratio"] > 0.5:
        reasons.append("膚色占比過高，可能是大特寫，注意鏡頭幅度需大幅收斂")

    if max(w, h) < 1280:
        if verdict == "PASS":
            verdict = "WARN"
        reasons.append(f"解析度偏低（{w}×{h}），生成後細節會不足")

    if not reasons:
        reasons.append("四項檢查全數通過：無白底黑邊、內容滿幅、臉部可見、色調與系列一致")

    report["verdict"] = verdict
    report["reasons"] = reasons

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"[{verdict}] {report['file']}")
        print(f"  {w}×{h}  比例 {report['aspect']}  {report['megapixels']}MP")
        print(f"  內容邊界 {cb}")
        print(f"  膚色占比 {sk['ratio']}（垂直重心 {sk.get('centroid_y_pct')}%）")
        print(f"  亮度 {report['tone']['luma_mean']}  飽和 {report['tone']['saturation_mean']}")
        print("  判定：")
        for r in reasons:
            print(f"   - {r}")
    sys.exit(0 if verdict != "BLOCK" else 1)


if __name__ == "__main__":
    main()
