#!/usr/bin/env python3
"""畫面讀數 — 劇照讀解（Stage 0.2）的確定性底數。

為什麼要這支腳本
-----------------
「劇照讀解」是語言模型對圖片的推理，推理需要**可量測的事實**當地基，
否則會產出一堆「畫面很有電影感」這種沒用的形容詞，甚至憑空編造
（例如把低光車艙說成「陽光下的草原」）。

本腳本只做**度量**，不做判斷結論。輸出的每一項都是像素算出來的數字。

三種框，各有各的用途，不可混用：
  skin_region  未擴張的膚色連通域 → **量景別與構圖位置**（擴張過的框會撐到滿幅，量不出景別）
  framed_reach 適度外擴（上 0.5／下 0.8／側 0.3）的框 → **判斷主體是否切到畫面邊緣**
               刻意不用預設的 1.1／2.6／0.6：那是為裁切安全設計的，會一路撐到滿幅，邊緣判斷失效
  subject_zone skin_region 外擴 50%／20% → **光源、景深、背景的取樣範圍**

輸出 JSON。`observations` 是「照數字講話」的觀察句，供設計書引用；
`cautions` 是這個畫面在 I2V 上的客觀風險提示。
"""
import argparse
import json
import os
import sys

try:
    from PIL import Image, ImageFilter
    import numpy as np
except ImportError:
    print(json.dumps({
        "error": "需要 Pillow 與 numpy。安裝：pip install pillow numpy",
    }, ensure_ascii=False))
    sys.exit(2)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from subject_detect import detect_bbox  # noqa: E402


def _clamp(v, lo, hi):
    return max(lo, min(hi, int(v)))


def _subject_zone(raw, W, H):
    """以膚色框為中心外擴，得到「主體周邊」取樣區。"""
    if not raw or raw.get("x0") is None:
        return (0, 0, W, H)
    bw, bh = raw["x1"] - raw["x0"], raw["y1"] - raw["y0"]
    return (_clamp(raw["x0"] - bw * 0.5, 0, W),
            _clamp(raw["y0"] - bh * 0.2, 0, H),
            _clamp(raw["x1"] + bw * 0.5, 0, W),
            _clamp(raw["y1"] + bh * 0.2, 0, H))


def _shot_size(skin_h_pct, skin_w_pct, from_skin=True):
    """由膚色區高度推定景別。

    校準依據（膚色區 = 臉 + 頸 + 可能入鏡的裸露手臂，故與純臉高不同）：
      佔滿畫面 55% 以上 → 特寫；33–55% → 近景；18–33% → 中景；8–18% → 全景；<8% → 遠景

    from_skin=False 代表這個框是梯度備援推出來的（沒有真的找到膚色連通域），
    此時寬度判讀沒有意義，不加推論性後綴。
    """
    if skin_h_pct >= 55:
        shot = "特寫（臉／臉+肩）"
    elif skin_h_pct >= 33:
        shot = "近景（胸上）"
    elif skin_h_pct >= 18:
        shot = "中景（腰上）"
    elif skin_h_pct >= 8:
        shot = "全景（全身）"
    else:
        shot = "遠景（人物偏小）"
    if from_skin and skin_w_pct > 40:
        shot += "・膚色區偏寬（可能含裸露手臂）"
    return shot


def _shot_size_from_face(face_h_pct):
    """由**臉高占畫面的百分比**推定景別——這是電影攝影的正確度量。

    人體比例：臉高 ≈ 全身高 / 9。所以 臉高/畫面高 = 1 / (9 × 可見比例)。
      可見全身      → 1/9  ≈ 0.111
      可見 3/4（膝上）→ 0.148
      可見 1/2（腰上）→ 0.222
      可見 1/3（胸上）→ 0.317
      僅頭+肩        → 0.505
    邊界取幾何中點。

    ⚠️ **坐姿／倚靠／大幅度側身會讓對應關係偏移**（坐著時「可見比例」的分母變小，
    同樣的臉高會被讀成更近的景別）。所以 `face_h_pct` 一律原樣輸出，
    由調用方在設計書裡自己校正。
    """
    f = face_h_pct / 100.0
    if f >= 0.40:
        return "特寫（頭／頭+肩）"
    if f >= 0.27:
        return "近景（胸上）"
    if f >= 0.185:
        return "中景（腰上）"
    if f >= 0.13:
        return "中全景（膝上）"
    if f >= 0.085:
        return "全景（全身）"
    return "遠景（人物偏小）"


def _placement(cx_pct):
    if cx_pct < 38:
        side = "左側"
    elif cx_pct > 62:
        side = "右側"
    else:
        side = "中央"
    if abs(cx_pct - 33.3) < 6 or abs(cx_pct - 66.7) < 6:
        side += "・貼近三分線"
    return side


def _light_direction(gray, zone):
    x0, y0, x1, y1 = zone
    reg = gray[y0:y1, x0:x1]
    if reg.size == 0:
        return {"available": False}
    hh, ww = reg.shape
    left = float(reg[:, : max(1, ww // 2)].mean())
    right = float(reg[:, max(1, ww // 2):].mean())
    top = float(reg[: max(1, hh // 2), :].mean())
    bottom = float(reg[max(1, hh // 2):, :].mean())
    dx, dy = right - left, bottom - top
    scale = float(reg.std()) or 1.0
    sx, sy = dx / scale, dy / scale
    if abs(sx) < 0.18 and abs(sy) < 0.18:
        azimuth = "接近平光／無明顯方向性"
    else:
        vert = "上" if sy < 0 else "下"
        horiz = "左" if sx < 0 else "右"
        if abs(sx) >= abs(sy) * 1.6:
            azimuth = f"來自{horiz}側"
        elif abs(sy) >= abs(sx) * 1.6:
            azimuth = f"來自{vert}方"
        else:
            azimuth = f"來自{vert}{horiz}斜角"
    return {"available": True, "left_mean": round(left, 1), "right_mean": round(right, 1),
            "top_mean": round(top, 1), "bottom_mean": round(bottom, 1),
            "dx_norm": round(sx, 2), "dy_norm": round(sy, 2), "azimuth": azimuth}


def _background_stats(gray, zone, H):
    x0, y0, x1, y1 = zone
    mask = np.ones_like(gray, dtype=bool)
    mask[y0:y1, x0:x1] = False
    bg = gray[mask]
    if bg.size < 100:
        return {"available": False}
    std = float(bg.std())
    if std < 22:
        kind = "高度均勻（天空／霧／素牆／水面）——適合當流動的環境面"
    elif std < 45:
        kind = "中等結構（有遠景層次，可做光線推移）"
    else:
        kind = "結構密集（街景／人群／植被）——適合當動態背景或人流對比"
    ring = gray[: max(1, int(H * 0.15)), :]
    return {"available": True, "luma_mean": round(float(bg.mean()), 1), "luma_std": round(std, 1),
            "top_band_mean": round(float(ring.mean()), 1), "kind": kind,
            "subject_bg_contrast": round(abs(float(bg.mean()) - float(gray.mean())), 1)}


def _depth_of_field(gray, zone):
    x0, y0, x1, y1 = zone
    mask = np.zeros_like(gray, dtype=bool)
    mask[y0:y1, x0:x1] = True
    if mask.sum() < 200 or (~mask).sum() < 200:
        return {"available": False}
    sub = gray[mask].astype(np.float32)
    bg = gray[~mask].astype(np.float32)
    sub_edge = float(np.abs(np.diff(sub)).mean())
    bg_edge = float(np.abs(np.diff(bg)).mean())
    if bg_edge <= 0:
        return {"available": False}
    ratio = round(sub_edge / bg_edge, 2)
    if ratio >= 1.5:
        verdict = "主體明顯比背景銳利 → 淺景深（背景已虛化）"
    elif ratio <= 0.7:
        verdict = "背景比主體銳利 → 主體可能失焦或有前景遮擋"
    else:
        verdict = "前後景銳利度接近 → 焦平面較深（環境清晰）"
    return {"available": True, "subject_edge": round(sub_edge, 2),
            "bg_edge": round(bg_edge, 2), "ratio": ratio, "verdict": verdict}


def _people_count(arr):
    a = np.asarray(arr).astype(np.float32)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    y = 0.299 * r + 0.587 * g + 0.114 * b
    cb = 128 - 0.168736 * r - 0.331264 * g + 0.5 * b
    cr = 128 + 0.5 * r - 0.418688 * g - 0.081312 * b
    mask = (y > 55) & (cb > 76) & (cb < 136) & (cr > 132) & (cr < 182)
    H, W = mask.shape
    bs = max(1, H // 120)
    bh, bw = H // bs, W // bs
    blocks = set()
    for by in range(bh):
        for bx in range(bw):
            if mask[by * bs:(by + 1) * bs, bx * bs:(bx + 1) * bs].mean() >= 0.4:
                blocks.add((bx, by))
    seen, comps = set(), []
    for start in list(blocks):
        if start in seen:
            continue
        stack, comp = [start], set()
        seen.add(start)
        while stack:
            cur = stack.pop()
            comp.add(cur)
            x, y2 = cur
            for nb in ((x + 1, y2), (x - 1, y2), (x, y2 + 1), (x, y2 - 1)):
                if nb in blocks and nb not in seen:
                    seen.add(nb)
                    stack.append(nb)
        if len(comp) >= 4:
            comps.append(comp)
    comps.sort(key=len, reverse=True)
    big = [c for c in comps if len(c) >= max(4, len(comps[0]) * 0.18)] if comps else []
    return {"skin_blobs": len(comps), "significant_blobs": len(big),
            "count_hint": "單人" if len(big) <= 1
            else f"疑似 {len(big)} 處膚色區域（可能多人，或裸露肢體／膚色背景誤判）"}


def _acoustic_hint(bg, dof, tone, skin_h_pct):
    """從視覺線索推「音場」——不是推具體音效。

    照片沒有聲音。能推的只有三件事：收音距離、空間開闊度、環境密度與時間感。
    這三件事決定環境音的**底色**，具體音源仍要靠讀解的可動元素清單去挑。

    **距離與開闊度是兩條獨立的軸，不可混用。**
    曠場人物一樣可以是淺景深近距離（大光圈拍人），所以景深只能推收音距離，
    推不出「密閉」。開闊度要用**上方亮面**（天空訊號）與背景均勻度判斷。
    """
    if not bg.get("available"):
        return {"available": False}

    std = bg.get("luma_std", 50)
    top = bg.get("top_band_mean", 100)
    luma = tone.get("luma_mean", 100)
    shallow = bool(dof.get("available") and (dof.get("ratio") or 0) >= 1.5)
    big_subject = skin_h_pct is not None and skin_h_pct >= 33

    if shallow and big_subject:
        distance = "近距離（淺景深 + 主體佔畫面大）——呼吸與衣料摩擦都在收音範圍內"
    elif shallow or big_subject:
        distance = "中近距離（其中一項成立）——環境音與人聲同在一個層次"
    else:
        distance = "中遠距離（景深較深）——主體不突出，環境音會蓋過細節"

    if top < 60:
        enclosure = "密閉（上方暗且被遮：天花板／車頂／樹冠）——低頻堆積，有回彈"
    elif top > luma * 1.3:
        enclosure = "開闊（上方有亮面：天空／大片窗光）——沒有回彈，聲音散掉"
    elif std < 22:
        enclosure = "開闊・無反射（背景高度均勻：霧／曠野／素牆）"
    elif std < 45:
        enclosure = "半開放（有遠景層次）——中距離環境音可聞，略有反射"
    else:
        enclosure = "密閉或高密度（結構密集）——反射多，聲音複雜"

    if std < 22:
        density = "安靜（單一持續底噪）"
    elif std < 45:
        density = "規律（可預期的重複聲源）"
    else:
        density = "熱鬧（不規則、多方聲源）"

    warmth = tone.get("warmth_index", 0)
    if luma < 60:
        tod = "低光：夜晚或暗室（低頻為主，遠處車聲／電器聲）"
    elif luma < 145:
        tod = "日間自然光（中頻為主，環境音清晰）" if warmth <= 10 else "黃昏／暖光時段"
    else:
        tod = "明亮／戶外強光（高頻為主，開闊感強）"

    return {"available": True, "distance": distance, "enclosure": enclosure,
            "density": density, "time_of_day": tod,
            "note": "以上是從畫面的視覺線索推得的**音場**，不是具體音效；"
                    "具體音源要靠讀解的可動元素清單挑選"}


def analyse(path):
    im = Image.open(path)
    W, H = im.size
    rgb = im.convert("RGB")
    arr = np.asarray(rgb)
    gray = arr.mean(axis=2).astype(np.float32)
    a = arr.astype(np.float32)

    raw = detect_bbox(path, expand_up=0, expand_down=0, expand_side=0)
    reach = detect_bbox(path, expand_up=0.5, expand_down=0.8, expand_side=0.3)
    zone = _subject_zone(raw, W, H)

    mx, mn = a.max(axis=2), a.min(axis=2)
    sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1.0), 0.0)
    warmth = float(a[..., 0].mean() - a[..., 2].mean())
    if warmth > 10:
        temp = "暖調（R 明顯高於 B）"
    elif warmth < -4:
        temp = "冷調（B 高於或接近 R）"
    else:
        temp = "中性偏冷（R/B 接近平衡）"
    luma_std = float(gray.std())
    contrast = "高對比" if luma_std > 62 else ("中對比" if luma_std > 42 else "低對比（平光／陰天／霧）")

    report = {"file": path, "size": [W, H], "aspect": round(W / H, 4),
              "is_16x9": abs(W / H - 16 / 9) < 0.01, "megapixels": round(W * H / 1e6, 2)}

    obs, cautions = [], []

    if raw.get("x0") is None:
        report["skin_region"] = None
        cautions.append("完全偵測不到膚色區域：這張圖可能沒有清楚的人物（或人物極小／全遮），"
                        "先確認素材用途再往下走")
        report["framed_reach"] = {"source": reach.get("source")}
    else:
        from_skin = str(raw.get("source", "")).startswith("skin-blob")
        sh = (raw["y1"] - raw["y0"]) / H * 100
        sw = (raw["x1"] - raw["x0"]) / W * 100
        cx = (raw["x0"] + raw["x1"]) / 2 / W * 100
        cy = (raw["y0"] + raw["y1"]) / 2 / H * 100
        face_h_pct = raw.get("face_h_pct")
        if face_h_pct is not None:
            shot = _shot_size_from_face(face_h_pct)
            shot_basis = f"臉高占畫面 {face_h_pct}%"
        else:
            shot = _shot_size(sh, sw, from_skin)
            shot_basis = f"膚色區高占畫面 {sh:.0f}%、寬 {sw:.0f}%"
        report["skin_region"] = {
            "bbox": [raw["x0"], raw["y0"], raw["x1"], raw["y1"]],
            "height_pct": round(sh, 1), "width_pct": round(sw, 1),
            "top_pct": round(raw["y0"] / H * 100, 1),
            "center_x_pct": round(cx, 1), "center_y_pct": round(cy, 1),
            "shot_size": shot, "shot_basis": shot_basis,
            "face_h_pct": face_h_pct,
            "face_median_YCC": raw.get("face_median_YCC"),
            "placement": _placement(cx),
            "from_skin": from_skin,
            "source": raw.get("source"), "confidence": raw.get("confidence"),
        }
        if raw.get("face"):
            fx, fy, fw, fh = raw["face"]
            report["face"] = {
                "bbox": raw["face"], "height_pct": round(fh / H * 100, 1),
                "width_pct": round(fw / W * 100, 1),
                "center_x_pct": round((fx + fw / 2) / W * 100, 1),
                "center_y_pct": round((fy + fh / 2) / H * 100, 1),
                "median_YCC": raw.get("face_median_YCC"),
                "haar_candidates": raw.get("face_candidates"),
                "distinct_faces": raw.get("distinct_faces", 1),
            }
            report["head"] = raw.get("head")
            report["face_count_source"] = "haar-face"
        report["framed_reach"] = {
            "bbox": [reach["x0"], reach["y0"], reach["x1"], reach["y1"]],
            "touches_top": reach["y0"] <= 2, "touches_bottom": reach["y1"] >= H - 2,
            "touches_left": reach["x0"] <= 2, "touches_right": reach["x1"] >= W - 2,
            "source": reach.get("source"),
        }
        obs.append(f"景別 {shot}（{shot_basis}）")
        obs.append(f"構圖 {_placement(cx)}，主體上緣距畫面頂端 {raw['y0'] / H * 100:.1f}%")
        if face_h_pct is not None:
            obs.append(f"臉部偵測成功（Haar 候選 {raw.get('face_candidates')} 個，"
                       f"採最靠近中央且膚色一致者）→ 景別讀數可信")
        if not from_skin and face_h_pct is None:
            cautions.append("**沒有偵測到任何膚色連通域**，上方的框只是梯度備援推出來的——"
                            "若這張圖其實沒有人物，請改走環境敘事（`visual-analysis.md` §6 F 型），"
                            "不要當成遠景人物硬跑情感演繹")
        if raw.get("warm_false_positive_suspected"):
            cautions.append("**暖色誤判守衛觸發**：膚色區大到不合理，很可能是暖色衣物／石牆／"
                            "落葉被吃進來（2026-09-26 `seine-bank-scene` 實測踩到）。"
                            "務必開 `*_subject_check.jpg` 人眼確認，或改走臉部偵測")
        if face_h_pct is None and sh < 8:
            cautions.append("人物極小（膚色區 <8% 畫面高）：臉部像素不足以承載微表情表演，"
                            "這張圖不適合走「情感演繹」，應改走環境敘事或先收緊構圖")
        elif sh < 18:
            cautions.append("人物偏小（<18%）：微表情在大螢幕上是糊的，建議靠姿態與環境演出，"
                            "或先用收緊運鏡把主體放大")
        if raw.get("confidence") is not None and raw["confidence"] < 0.45:
            cautions.append(f"主體偵測信心低（{raw['confidence']}，來源 {raw['source']}）："
                            "低光或低對比素材，後製 21:9 前務必人眼看縮圖，不要直接收下預設值")
        for side, label in (("touches_top", "上緣"), ("touches_bottom", "下緣"),
                            ("touches_left", "左緣"), ("touches_right", "右緣")):
            if report["framed_reach"][side]:
                obs.append(f"主體推定被畫面{label}切斷——畫外仍有持續的空間，"
                           "可作為視線、動作或光線的去處")
    report["subject_zone"] = {"rect": list(zone)}
    report["light"] = _light_direction(gray, zone)
    report["background"] = _background_stats(gray, zone, H)
    report["depth_of_field"] = _depth_of_field(gray, zone)
    report["people"] = _people_count(arr)
    report["tone"] = {"luma_mean": round(float(gray.mean()), 1), "luma_std": round(luma_std, 1),
                      "contrast": contrast, "saturation_mean": round(float(sat.mean()), 3),
                      "warmth_index": round(warmth, 1), "temperature": temp}
    report["acoustic_hint"] = _acoustic_hint(
        report["background"], report["depth_of_field"], report["tone"],
        report["skin_region"]["height_pct"] if report["skin_region"] else None,
    )

    lt = report["light"]
    if lt.get("available"):
        obs.append(f"主光{lt['azimuth']}（左右差 {lt['dx_norm']}σ、上下差 {lt['dy_norm']}σ）")
    bg = report["background"]
    if bg.get("available"):
        obs.append(f"背景亮度 {bg['luma_mean']}、{bg['kind']}")
    dof = report["depth_of_field"]
    if dof.get("available"):
        obs.append(dof["verdict"])
    obs.append(f"色調 {report['tone']['luma_mean']} 亮度 / {contrast} / "
               f"飽和 {report['tone']['saturation_mean']} / {temp}")
    ah = report["acoustic_hint"]
    if ah.get("available"):
        obs.append(f"音場 收音距離：{ah['distance']}")
        obs.append(f"音場 開闊度：{ah['enclosure']}")
        obs.append(f"音場 密度與時間：{ah['density']}；{ah['time_of_day']}")
    pp = report["people"]
    n_faces = (report.get("face") or {}).get("distinct_faces")
    if n_faces is not None:
        # 臉部偵測成功時，人數以「幾張合理的臉」為準。
        # 膚色 blob 在暖色調場景會把大衣／石牆／落葉算成人（2026-09-26 實測）。
        report["people"]["count_source"] = "haar-face"
        if n_faces <= 1:
            report["people"]["count_hint"] = "單人（臉部偵測）"
        else:
            report["people"]["count_hint"] = f"疑似 {n_faces} 人（臉部偵測，各自獨立不重疊）"
            cautions.append(f"偵測到 {n_faces} 張臉——多人素材必須先確認誰是主體，"
                            "否則模型會把注意力分散到第二個人身上")
    elif pp["significant_blobs"] > 1:
        report["people"]["count_source"] = "skin-blob"
        cautions.append(f"{pp['count_hint']}——多人素材必須先確認誰是主體，"
                        "否則模型會把注意力分散到第二個人身上")

    report["observations"] = obs
    report["cautions"] = cautions
    return report


def main():
    p = argparse.ArgumentParser(description="畫面讀數：劇照讀解的確定性底數")
    p.add_argument("image")
    p.add_argument("--json", action="store_true", help="只輸出 JSON（預設即 JSON，保留相容）")
    p.add_argument("--no-check-image", action="store_true",
                   help="不要產出 *_subject_check.jpg 人眼複核圖")
    args = p.parse_args()
    try:
        rep = analyse(args.image)
    except FileNotFoundError:
        print(json.dumps({"error": f"找不到檔案：{args.image}"}, ensure_ascii=False))
        sys.exit(2)

    # 強制步驟的實體化：把偵測框畫回畫面。skill 一直要求「人眼確認」，
    # 但靠人記得做；做成一行就會產出的檔案，複核才真的會發生。
    if not args.no_check_image:
        stem = os.path.splitext(args.image)[0]
        out = f"{stem}_subject_check.jpg"
        try:
            from subject_detect import draw_subject_check
            draw_subject_check(args.image, out)
            rep["subject_check_image"] = out
            sys.stderr.write(f"[read_frame] 人眼複核圖 → {out}\n")
        except Exception as e:  # noqa: BLE001
            sys.stderr.write(f"[read_frame] 複核圖產出失敗（不影響讀數）：{e}\n")

    print(json.dumps(rep, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
