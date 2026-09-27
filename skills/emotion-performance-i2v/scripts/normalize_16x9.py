#!/usr/bin/env python3
"""參考圖 16:9 正規化（主體安全）。

為什麼需要：`kling-3.0-omni/image-to-video` 單鏡頭下 `aspect_ratio` **只接受 `auto`**
（送 `16:9` 回 422），輸出比例完全跟隨輸入圖。要保證 **輸出 16:9 / 1080p**，
輸入首幀就必須先正規化成 16:9 —— 這是硬前置，不是保險。

（`kling-3.0-omni/reference-to-video` 不同：它只接受具體比例，請求裡寫 `16:9` 即可；
但輸入圖一併正規化仍然更穩。`kling-3.0/video` 可省略比例，會自動適配。）

策略（與 finish_219.py 同一個原則：不為主體之外的構圖犧牲主體）：
  已是 16:9（±1%）      → 直接輸出
  比 16:9 寬（如 21:9）  → 水平裁切，裁切窗依主體位置定位；主體過寬則改補邊
  比 16:9 窄（如 4:5、9:16）→ 嘗試垂直裁切；主體過高則改左右模糊補邊

裁切窗定位用 `--bias` 控制：**0 = 盡量留頭頂、1 = 貼主體上緣（會削頭）**，預設 0.35。
主體塞不進裁切窗時一律改補邊，不會硬裁。

⚠️ 補邊會把模糊邊條烘進畫面。若主體是全身人像、而你要的是臉部特寫，
   改用 `--mode crop --bias 0`（寧可裁掉腳，不削頭、不留邊條）。

用法：
  python normalize_16x9.py <圖片> --out <輸出.jpg>
  python normalize_16x9.py <圖片> --out ref_16x9.jpg --size 1920x1080
  python normalize_16x9.py <圖片> --out ref_16x9.jpg --mode crop --bias 0
"""
import argparse
import json
import os
import sys

from PIL import Image, ImageFilter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from subject_detect import detect_bbox  # noqa: E402

TARGET = 16 / 9


def fit_contain(img, tw, th):
    s = min(tw / img.width, th / img.height)
    return img.resize((max(1, int(img.width * s)), max(1, int(img.height * s))), Image.LANCZOS)


def blur_pad(img, tw, th):
    """完整保留原圖，四周補模糊背景（先放大裁滿再高斯模糊）。"""
    s = max(tw / img.width, th / img.height)
    bg = img.resize((max(1, int(img.width * s)), max(1, int(img.height * s))), Image.LANCZOS)
    left, top = (bg.width - tw) // 2, (bg.height - th) // 2
    bg = bg.crop((left, top, left + tw, top + th)).filter(ImageFilter.GaussianBlur(32))
    fg = fit_contain(img, tw, th)
    bg.paste(fg, ((tw - fg.width) // 2, (th - fg.height) // 2))
    return bg


def main():
    p = argparse.ArgumentParser()
    p.add_argument("image")
    p.add_argument("--out", required=True)
    p.add_argument("--size", default="1920x1080")
    p.add_argument("--mode", choices=["auto", "crop", "pad"], default="auto")
    p.add_argument("--bias", type=float, default=0.35,
                   help="裁切窗在可用空間中的偏好：0 = 盡量留頭頂（靠上），"
                        "1 = 貼主體上緣（會削頭）。預設 0.35")
    args = p.parse_args()

    tw, th = (int(x) for x in args.size.lower().split("x"))
    img = Image.open(args.image).convert("RGB")
    w, h = img.size
    ratio = w / h
    bb = detect_bbox(args.image)

    report = {"source": args.image, "source_size": [w, h],
              "source_ratio": round(ratio, 3), "target_size": [tw, th],
              "subject": bb, "action": None, "mode_used": None, "bias": args.bias}

    if abs(ratio - TARGET) < 0.01:
        out = img.resize((tw, th), Image.LANCZOS)
        report.update(action="already-16:9，僅縮放至目標尺寸", mode_used="passthrough")
    else:
        # 目標框在原圖尺度下的尺寸
        if ratio > TARGET:                      # 太寬 → 水平裁切
            win_w, win_h = h * TARGET, h
        else:                                    # 太窄 → 垂直裁切
            win_w, win_h = w, w / TARGET

        fits = True
        if bb.get("x0") is not None:
            need_w = bb["x1"] - bb["x0"]
            need_h = bb["y1"] - bb["y0"]
            fits = need_w <= win_w and need_h <= win_h

        # 偵測不到主體時保守走 pad（保留全圖），不盲目裁切
        if args.mode != "auto":
            mode = args.mode
        else:
            mode = "crop" if (bb.get("x0") is not None and fits) else "pad"

        if mode == "crop":
            b = min(max(args.bias, 0.0), 1.0)
            if bb.get("x0") is not None:
                # 裁切窗必須完整包住主體。可行區間內用 bias 選位置：
                # 原點越小 = 窗越靠上／靠左 = 主體上方留白越多（不會削頭）。
                x_lo, x_hi = bb["x1"] - win_w, bb["x0"]
                y_lo, y_hi = bb["y1"] - win_h, bb["y0"]
                x = min(x_lo, x_hi) + abs(x_hi - x_lo) * b
                y = min(y_lo, y_hi) + abs(y_hi - y_lo) * b
                headroom = int(bb["y0"] - y)
            else:
                x = (w - win_w) * b
                y = (h - win_h) * b
                headroom = None
            x = int(min(max(0, x), max(0, w - win_w)))
            y = int(min(max(0, y), max(0, h - win_h)))
            img = img.crop((x, y, int(x + win_w), int(y + win_h)))
            report.update(action=f"主體完整，裁切窗 = {int(win_w)}×{int(win_h)} @ ({x},{y})"
                                 + (f"，頭頂保留 {headroom}px" if headroom is not None else ""),
                          mode_used="crop", crop_origin=[x, y], headroom_px=headroom)
            out = img.resize((tw, th), Image.LANCZOS)
        else:
            reason = ("主體大於裁切窗" if bb.get("x0") is not None
                      else "無法定位主體，保守保留全圖")
            report.update(action=f"{reason}，改用模糊補邊保留完整內容",
                          mode_used="pad")
            out = blur_pad(img, tw, th)

    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    out.save(args.out, quality=95, subsampling=0)
    report["output"] = args.out
    report["output_size"] = list(out.size)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
