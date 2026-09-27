#!/usr/bin/env python3
"""主體偵測共用模組。

回傳主體在畫面中的矩形範圍（含髮絲與身體擴張），供：
  finish_219.py      → 21:9 垂直裁切決策
  normalize_16x9.py  → 16:9 正規化決策
  read_frame.py      → 景別／構圖／主體位置

偵測策略（依可信度排序）
------------------------
  1. **臉部偵測**（OpenCV Haar，若環境有 cv2）→ 最可信
     由臉框依人體比例推出頭部與身體範圍。並用「臉框內部色調是否像膚色」濾掉誤判。
  2. 膚色連通域（原始方法）→ 沒有臉時的備援
  3. 中心帶梯度能量 → 最後手段，信心度低

**為什麼要加臉部偵測（2026-09-26 實測踩到的坑）**
`seine-bank-scene` 那張秋景人像，膚色法把**右側的乾砌石牆與落葉**判成了主體，
人（臉＋駝色大衣）整個在框外，而 `confidence` 還回報 **1.0**（因為信心是照 blob 大小算的）。
根因：**駝色大衣的 Cb/Cr（114.5/144.6）與她的臉頰（114.4/147.6）幾乎完全相同**，
石牆（116.0/137.8）與落葉（110.4/147.3）也落進同一個範圍。
→ **純顏色法在暖色調秋景上原理上分不開人與環境**，必須引入結構資訊。

實測佐證：Haar 在該圖上回兩個候選，正確的臉表面色 Y=108、誤判的石牆 Y=49——
**亮度一致性可以濾掉誤判**。

⚠️ 臉部偵測只覆蓋正面／準正面。**側臉、背影、遠景沒有臉**，一律退回膚色法，
並在 `source` 標明來源、由呼叫端決定要不要人工複核。
"""
from PIL import Image
import numpy as np

# ---- 臉部偵測參數 ---------------------------------------------------------
# Haar 對小臉不敏感，所以下限放寬到畫面高的 6%，並用兩組 cascade 取聯集。
FACE_MIN_FRAC = 0.06
# 人的比例常數（以 Haar 臉框為單位；Haar 框約等於「眉→下巴」）
HEAD_ABOVE_FACE = 0.55      # 髮頂在臉框上緣之上多少
HEAD_W_FACTOR = 1.55        # 頭的寬度 ≈ 臉寬 ×1.55（含髮）
BODY_BELOW_FACE = 4.5       # 身體往下延伸（約到大腿上部）
BODY_W_FACTOR = 3.0         # 肩寬 ≈ 臉寬 ×3.0（含手臂餘裕）
# 臉框內部的膚色合理性檢查
FACE_SKIN_Y_MIN, FACE_SKIN_Y_MAX = 55, 225
FACE_SKIN_CB = (76, 142)
FACE_SKIN_CR = (128, 190)
# 評分用的膚色質心與容忍度（色度為主，亮度只輕微加權）
SKIN_Y0, SKIN_SIGMA_Y = 125.0, 70.0
SKIN_CB0, SKIN_SIGMA_CB = 120.0, 14.0
SKIN_CR0, SKIN_SIGMA_CR = 150.0, 18.0


def _largest_component(blocks, min_blocks=3):
    if not blocks:
        return set()
    seen, best = set(), set()
    for start in blocks:
        if start in seen:
            continue
        stack, comp = [start], set()
        seen.add(start)
        while stack:
            cur = stack.pop()
            comp.add(cur)
            x, y = cur
            for nb in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if nb in blocks and nb not in seen:
                    seen.add(nb)
                    stack.append(nb)
        if len(comp) > len(best):
            best = comp
    return best if len(best) >= min_blocks else set()


def _skin_mask(arr, relaxed=False):
    r, g, b = arr[..., 0], arr[..., 1], arr[..., 2]
    y = 0.299 * r + 0.587 * g + 0.114 * b
    cb = 128 - 0.168736 * r - 0.331264 * g + 0.5 * b
    cr = 128 + 0.5 * r - 0.418688 * g - 0.081312 * b
    if relaxed:
        # 低光／夜景素材：膚色被壓暗，門檻放寬一格
        return (y > 42) & (cb > 74) & (cb < 140) & (cr > 128) & (cr < 186)
    return (y > 70) & (cb > 80) & (cb < 132) & (cr > 136) & (cr < 178)


def _blob_bbox(skin, W, H, h, w, bs, min_frac, expand_up, expand_down, expand_side):
    bh, bw = h // bs, w // bs
    blocks = set()
    for by in range(bh):
        for bx in range(bw):
            if skin[by * bs:(by + 1) * bs, bx * bs:(bx + 1) * bs].mean() >= min_frac:
                blocks.add((bx, by))
    comp = _largest_component(blocks, min_blocks=3)
    if not comp:
        return None
    xs = [p[0] for p in comp]
    ys = [p[1] for p in comp]
    sx, sy = W / w, H / h
    x0, x1 = min(xs) * bs * sx, (max(xs) + 1) * bs * sx
    y0, y1 = min(ys) * bs * sy, (max(ys) + 1) * bs * sy
    span_y, span_x = max(1.0, y1 - y0), max(1.0, x1 - x0)
    x0 = max(0.0, x0 - span_x * expand_side)
    x1 = min(float(W), x1 + span_x * expand_side)
    y0 = max(0.0, y0 - span_y * expand_up)
    y1 = min(float(H), y1 + span_y * expand_down)
    coverage = (y1 - y0) / H
    conf = round(min(1.0, len(comp) / 40.0) * (1.0 if coverage < 0.92 else 0.4), 2)
    return {"x0": int(x0), "y0": int(y0), "x1": int(x1), "y1": int(y1),
            "source": "skin-blob", "confidence": conf,
            "blob_blocks": len(comp), "coverage_y": round(coverage, 2)}


# ---------------------------------------------------------------------------
# 臉部偵測
# ---------------------------------------------------------------------------

def _load_cv2():
    try:
        import cv2  # noqa: PLC0415
        return cv2
    except Exception:  # noqa: BLE001
        return None


def _face_looks_like_skin(arr, box):
    """臉框內部是否像膚色（濾掉石牆／落葉／木紋這類紋理誤判）。

    只看中央 60%×50% 區域（避開邊緣的頭髮與背景），
    要求亮度落在人類膚色的合理區間，且 Cb/Cr 同時落點。
    """
    x, y, w, h = box
    hh, ww = arr.shape[:2]
    x0 = max(0, int(x + w * 0.20)); x1 = min(ww, int(x + w * 0.80))
    y0 = max(0, int(y + h * 0.25)); y1 = min(hh, int(y + h * 0.75))
    if x1 - x0 < 4 or y1 - y0 < 4:
        return False, None
    reg = arr[y0:y1, x0:x1]
    r, g, b = reg[..., 0], reg[..., 1], reg[..., 2]
    yy = 0.299 * r + 0.587 * g + 0.114 * b
    cb = 128 - 0.168736 * r - 0.331264 * g + 0.5 * b
    cr = 128 + 0.5 * r - 0.418688 * g - 0.081312 * b
    med = (float(np.median(yy)), float(np.median(cb)), float(np.median(cr)))
    ok = (FACE_SKIN_Y_MIN <= med[0] <= FACE_SKIN_Y_MAX
          and FACE_SKIN_CB[0] <= med[1] <= FACE_SKIN_CB[1]
          and FACE_SKIN_CR[0] <= med[2] <= FACE_SKIN_CR[1])
    return ok, med


def _iou(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    x0 = max(ax, bx); y0 = max(ay, by)
    x1 = min(ax + aw, bx + bw); y1 = min(ay + ah, by + bh)
    if x1 <= x0 or y1 <= y0:
        return 0.0
    inter = (x1 - x0) * (y1 - y0)
    return inter / float(aw * ah + bw * bh - inter)


def detect_face(image_path):
    """回傳最佳臉框 dict 或 None。

    同一張圖可能有多個 Haar 回應（人臉 + 紋理／亮塊誤判）。評分：
      **膚色相似度 × 面積 × 靠近畫面中央**

    為什麼一定要把膚色相似度放進來（2026-09-26 `seine-bank-scene` 實測）：
    只按「面積 × 靠近中央」評分時，某一格的水面亮塊（200×200，表面色 Y=159/Cr=130）
    打敗了真臉（188×188，Y=115/Cr=148），因為它面積大又更靠近中央。
    結果偵測框在單幀跳到錯誤位置（臉中心 x 由 1112 跳到 735 再跳回 1102），
    看起來像「跳格」，其實是評分函式選錯了候選。
    **尺寸與位置是弱訊號，顏色才是強訊號。**
    """
    cv2 = _load_cv2()
    if cv2 is None:
        return None
    img = cv2.imread(image_path)
    if img is None:
        return None
    H, W = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    gray = cv2.equalizeHist(gray)
    arr = np.asarray(Image.open(image_path).convert("RGB")).astype(np.float32)

    min_side = max(24, int(H * FACE_MIN_FRAC))
    raw = []
    for name in ("haarcascade_frontalface_default.xml",
                 "haarcascade_frontalface_alt2.xml"):
        d = cv2.CascadeClassifier(cv2.data.haarcascades + name)
        if d.empty():
            continue
        for (x, y, w, h) in d.detectMultiScale(gray, scaleFactor=1.05, minNeighbors=5,
                                               minSize=(min_side, min_side)):
            raw.append((int(x), int(y), int(w), int(h)))
    if not raw:
        return None

    scored = []
    for box in raw:
        ok, med = _face_looks_like_skin(arr, box)
        if not ok:
            continue
        x, y, w, h = box
        cx_off = abs((x + w / 2) / W - 0.5)          # 0 = 正中
        # 膚色相似度：以 Cb/Cr 為主（色度較不受光照影響），Y 只輕微加權。
        dy = (med[0] - SKIN_Y0) / SKIN_SIGMA_Y
        dcb = (med[1] - SKIN_CB0) / SKIN_SIGMA_CB
        dcr = (med[2] - SKIN_CR0) / SKIN_SIGMA_CR
        skin_w = float(np.exp(-0.5 * (0.35 * dy ** 2 + dcb ** 2 + dcr ** 2)))
        score = (w * h) * (1.0 - 0.35 * cx_off) * skin_w
        scored.append((score, box, med, round(skin_w, 3)))
    if not scored:
        return None
    scored.sort(key=lambda t: -t[0])

    # 兩組 cascade 會對同一張臉各回一次，還有紋理誤判。按重疊度去重，
    # 剩下的才算「張數」——這是可用的人數證據，比膚色 blob 可靠得多。
    kept = []
    for score, box, med, sw in scored:
        if all(_iou(box, k[1]) < 0.35 for k in kept):
            kept.append((score, box, med, sw))
    _score, (x, y, w, h), med, sw = kept[0]
    return {"face": [x, y, w, h], "face_h_pct": round(h / H * 100, 1),
            "face_median_YCC": [round(v, 1) for v in med],
            "skin_weight": sw,
            "candidates": len(raw), "passed_skin_check": len(scored),
            "distinct_faces": len(kept),
            "all_faces": [[int(b[0]), int(b[1]), int(b[2]), int(b[3])] for _, b, _, _ in kept]}


def _subject_from_face(face, W, H):
    """由臉框推頭部與身體範圍（人體比例常數見檔頭）。"""
    x, y, w, h = face
    fcx = x + w / 2.0

    head_x0 = max(0.0, fcx - w * HEAD_W_FACTOR / 2.0)
    head_x1 = min(float(W), fcx + w * HEAD_W_FACTOR / 2.0)
    head_y0 = max(0.0, y - h * HEAD_ABOVE_FACE)
    head_y1 = min(float(H), y + h)

    sx0 = max(0.0, fcx - w * BODY_W_FACTOR / 2.0)
    sx1 = min(float(W), fcx + w * BODY_W_FACTOR / 2.0)
    sy0 = head_y0
    sy1 = min(float(H), y + h * BODY_BELOW_FACE)
    return ([int(head_x0), int(head_y0), int(head_x1), int(head_y1)],
            [int(sx0), int(sy0), int(sx1), int(sy1)])


def detect_bbox(image_path, expand_up=1.1, expand_down=2.6, expand_side=0.6):
    """回傳 {'x0','y0','x1','y1','source','confidence',...}（像素座標，原圖尺度）。

    順序：臉部偵測 → 膚色連通域（正常門檻）→ 膚色（放寬門檻，低光）→ 中心帶梯度。
    回傳值額外帶 `head` 與 `face`，供「頭部絕不可切」的裁切決策使用。
    """
    img = Image.open(image_path).convert("RGB")
    W, H = img.size

    # 1) 臉部偵測
    fd = detect_face(image_path)
    if fd:
        head, subj = _subject_from_face(fd["face"], W, H)
        return {"x0": subj[0], "y0": subj[1], "x1": subj[2], "y1": subj[3],
                "head": head, "face": fd["face"],
                "face_h_pct": fd["face_h_pct"],
                "face_median_YCC": fd["face_median_YCC"],
                "distinct_faces": fd.get("distinct_faces", 1),
                "all_faces": fd.get("all_faces", [fd["face"]]),
                "source": "face-haar", "confidence": 0.9,
                "face_candidates": fd["candidates"],
                "width": W, "height": H}

    # 2) 膚色連通域（備援；無臉時的側臉／背影／遠景都走這條）
    small = img.resize((320, max(1, int(320 * H / W))))
    arr = np.asarray(small).astype(np.float32)
    h, w, _ = arr.shape
    bs = 8

    for relaxed, min_frac in ((False, 0.45), (True, 0.25)):
        bb = _blob_bbox(_skin_mask(arr, relaxed), W, H, h, w, bs, min_frac,
                        expand_up, expand_down, expand_side)
        if bb:
            if relaxed:
                bb["source"] = "skin-blob-lowlight"
                bb["confidence"] = round(bb["confidence"] * 0.8, 2)
            # 暖色誤判守衛：blob 大到不合理時，很可能是暖色衣物／石牆／落葉被吃進來
            area_pct = (bb["x1"] - bb["x0"]) * (bb["y1"] - bb["y0"]) / float(W * H)
            if area_pct > 0.35:
                bb["confidence"] = round(min(bb["confidence"], 0.35), 2)
                bb["warm_false_positive_suspected"] = True
            bb["width"], bb["height"] = W, H
            return bb

    gray = np.asarray(img.convert("L")).astype(np.float32)
    x_lo, x_hi = int(W * 0.2), int(W * 0.8)
    band = np.abs(np.diff(gray[:, x_lo:x_hi], axis=1)).mean(axis=1)
    if band.max() > 0:
        rows = np.where(band > band.max() * 0.25)[0]
        if len(rows):
            return {"x0": x_lo, "y0": int(rows.min()), "x1": x_hi, "y1": int(rows.max()),
                    "source": "gradient", "confidence": 0.3,
                    "width": W, "height": H}

    return {"x0": None, "y0": None, "x1": None, "y1": None,
            "source": "none", "confidence": 0.0, "width": W, "height": H}


def draw_subject_check(image_path, out_path, bb=None):
    """把偵測到的框畫回畫面，產出一張人眼複核圖。

    **這是強制步驟的實體化**：skill 一直要求「把量到的線畫回畫面上確認」，
    但那靠人記得做。把它做成一行就會產出的檔案，複核才真的會發生。
    """
    from PIL import ImageDraw
    if bb is None:
        bb = detect_bbox(image_path)
    im = Image.open(image_path).convert("RGB")
    d = ImageDraw.Draw(im)
    if bb.get("x0") is not None:
        d.rectangle([bb["x0"], bb["y0"], bb["x1"], bb["y1"]],
                    outline=(255, 90, 60), width=4)
        if bb.get("head"):
            d.rectangle(bb["head"], outline=(90, 220, 140), width=3)
        if bb.get("face"):
            x, y, w, h = bb["face"]
            d.rectangle([x, y, x + w, y + h], outline=(255, 220, 90), width=3)
        label = (f"source={bb.get('source')}  conf={bb.get('confidence')}  "
                 f"red=subject  green=head  yellow=face")
        d.rectangle([0, 0, min(im.size[0], 860), 30], fill=(0, 0, 0))
        d.text((8, 10), label, fill=(255, 235, 200))
    im.save(out_path, quality=90)
    return out_path
