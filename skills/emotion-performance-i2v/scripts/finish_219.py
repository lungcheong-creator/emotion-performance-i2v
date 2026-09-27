#!/usr/bin/env python3
"""後製：主體安全裁切 → 21:9 + 2K + 縮圖 + 16:9 母版保留。

核心原則（用戶要求）：
  21:9 交付**不能為了滿足尺寸而切掉畫面主體**。因此本腳本會先偵測主體
  （膚色 + 中心梯度能量）在畫面中的垂直範圍，再決定：
    crop 模式 — 主體完整落在 21:9 裁切窗內 → 智慧偏移裁切
    pad  模式 — 主體高於裁切窗 → 保留完整 16:9 內容，兩側補模糊背景
                （pillarbox blur），絕不犧牲構圖

用法：
  python finish_219.py output/xxx.mp4 --out-dir output
  python finish_219.py output/xxx.mp4 --out-dir output --mode crop --bias 0.35
  python finish_219.py output/xxx.mp4 --out-dir output --mode pad --no-2k

輸出（以輸入 xxx.mp4 為基底）：
  xxx_169.mp4      16:9 母版（原樣複製，永遠保留）
  xxx_21x9.mp4     21:9 交付（1920×822，crop 或 pad）
  xxx_2k.mp4       2K 放大（2560×1440，從 16:9 母版）
  xxx_thumb.jpg    縮圖
  xxx_finish.json  決策報告（模式、主體 bbox、偏移量、理由）
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

def _resolve_ffmpeg():
    """可移植地找 ffmpeg：環境變數 → PATH → 常見安裝位置。

    寫死絕對路徑會讓 skill 一離開原作者的機器就跑不動，
    而這個腳本是**交付鏈的最後一環**——壞在這裡等於前面全部白做。
    """
    env = os.environ.get("FFMPEG")
    if env and os.path.exists(env):
        return env
    found = shutil.which("ffmpeg")
    if found:
        return found
    for cand in ("/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg",
                 os.path.expanduser("~/.local/bin/ffmpeg"),
                 "/usr/bin/ffmpeg"):
        if os.path.exists(cand):
            return cand
    return None


FFMPEG = _resolve_ffmpeg()
RATIO_21_9 = 21 / 9


def probe(path):
    """本機 ffprobe 不吃 -show_entries，改從 ffmpeg -i 的 stderr 解析。"""
    if not FFMPEG:
        raise SystemExit("找不到 ffmpeg（見 main() 的安裝說明）")
    out = subprocess.run([FFMPEG, "-hide_banner", "-i", path],
                         capture_output=True, text=True).stderr
    info = {}
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("Duration:"):
            info["duration"] = line.split(",")[1].strip()
        if "Video:" in line and "Stream #" in line:
            for part in line.split(","):
                part = part.strip()
                if "x" in part and part.replace("x", "").isdigit():
                    w, h = part.split("x")
                    info["width"], info["height"] = int(w), int(h)
                elif part.endswith("fps"):
                    info["fps"] = part
    return info


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stderr[-800:], file=sys.stderr)
        raise SystemExit(f"ffmpeg failed: {' '.join(cmd[:4])} ...")


def _largest_component(blocks, min_blocks=3):
    """在二值塊網格上找最大四連通域，回傳 {(x,y)} 集合。"""
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


def detect_subject(frame_jpg):
    """回傳主體垂直範圍 {'top','bottom','source','confidence'}（委派共用模組）。"""
    try:
        from subject_detect import detect_bbox
    except ImportError:
        return {"top": None, "bottom": None, "source": "unavailable", "confidence": 0.0}
    bb = detect_bbox(frame_jpg)
    if bb.get("y0") is None:
        return {"top": None, "bottom": None, "source": bb.get("source", "none"),
                "confidence": 0.0}
    out = {"top": bb["y0"], "bottom": bb["y1"], "source": bb["source"],
           "confidence": bb["confidence"],
           "blob_blocks": bb.get("blob_blocks"),
           "coverage": bb.get("coverage_y")}
    # 臉部偵測帶來的額外資訊：頭部框（不可退讓）與臉高。
    if bb.get("head"):
        out["head"] = [bb["head"][1], bb["head"][3]]      # [top, bottom]
    if bb.get("face_h_pct") is not None:
        out["face_h_pct"] = bb["face_h_pct"]
    return out


def choose_plan(subj, width, height, crop_h, bias):
    """決定 crop 或 pad，並算出裁切偏移。"""
    ch = crop_h
    if not subj or subj.get("top") is None:
        # 偵測不到主體時，**貼頂裁切**（y=0），不用中段偏置。
        # 理由：本 skill 只服務人物情感敘事，頭／臉是不可能讓步的部分。
        # y=0 的窗一定包含畫面最上緣，所以永遠不會削到頭；代價是切掉下方胸口，
        # 而胸口是可犧牲的。中段偏置（如 y=90）反而可能在無人察覺的情況下削頭。
        return {"mode": "crop", "y": 0,
                "reason": "無法定位主體 → 貼頂裁切（y=0），保證不削頭；"
                          "下方可能切到胸口。請人工看縮圖確認，或 --mode pad 保留全幅",
                "safe": None, "detection": "failed"}

    top, bottom = subj["top"], subj["bottom"]

    # 臉部偵測成功時走這條：**以頭部框為不可退讓的約束**。
    # 不再用「主體框覆蓋率」判成敗——特寫時身體本來就會撐滿畫面，
    # 那個啟發式會把一次成功的偵測誤判成失敗（2026-09-26 修正）。
    head = subj.get("head")
    if head and subj.get("source") == "face-haar":
        h_top, h_bot = head
        margin = int(ch * 0.04)
        head_need = (h_bot - h_top + 1) + margin * 2
        if head_need > ch:
            return {"mode": "pad", "y": None, "subject": [top, bottom], "head": [h_top, h_bot],
                    "reason": f"頭部高 {h_bot - h_top + 1}px 加餘裕後超過 {ch}px 裁切窗，"
                              f"裁切必然削到頭 → 改用完整 16:9 + 兩側模糊補邊",
                    "safe": False, "detection": "ok-face"}
        y_min = max(0, h_bot + margin - ch + 1)     # 頭的下緣至少離窗底 margin
        y_max = min(height - ch, h_top - margin)    # 頭的上緣至少離窗頂 margin
        if y_max < y_min:
            y_max = y_min
        y_pref = int(round((height - ch) * bias))
        y = min(max(y_pref, y_min), y_max)
        keep = min(bottom, y + ch) - max(top, y)
        return {"mode": "crop", "y": y, "window": [y, y + ch],
                "subject": [top, bottom], "head": [h_top, h_bot],
                "reason": f"臉部偵測（臉高占畫面 {subj.get('face_h_pct')}%）→ 以頭部框為約束，"
                          f"偏移量在 [{y_min},{y_max}] 內取 {y}；"
                          f"主體框保留 {max(0, keep)}/{bottom - top + 1}px",
                "safe": True, "detection": "ok-face"}

    coverage = (bottom - top + 1) / height

    # 覆蓋率 ≥ 0.92 視為「偵測失敗」而非「主體真的佔滿全幅」：
    # 低光場景放寬膚色門檻後，常把整片車艙／牆面吃進遮罩。
    # 此時**貼頂裁切**（y=0）：保證不削頭，並在報告標 uncertain 交由人工看縮圖。
    if coverage >= 0.92:
        return {"mode": "crop", "y": 0, "window": [0, ch],
                "subject": [top, bottom],
                "reason": f"主體偵測覆蓋 {int(coverage * 100)}% 畫面，判定為偵測失敗"
                          f"（低光／低對比場景常見）→ 貼頂裁切 y=0，保證不削頭；"
                          f"請人工看縮圖確認，或改用 --mode pad / --subject-y0/--subject-y1 指定主體範圍",
                "safe": None, "detection": "uncertain"}

    margin = int(ch * 0.04)
    need = (bottom - top + 1) + margin * 2

    if need <= ch:
        # 主體放得下：在容許範圍內選最接近 bias 的位置
        y_min = max(0, bottom + margin - ch + 1)
        y_max = min(height - ch, top - margin)
        if y_max < y_min:
            y_max = y_min
        y_pref = int(round((height - ch) * bias))
        y = min(max(y_pref, y_min), y_max)
        return {"mode": "crop", "y": y, "window": [y, y + ch],
                "subject": [top, bottom],
                "reason": f"主體高 {bottom - top + 1}px 可完整落入 {ch}px 裁切窗，"
                          f"偏移量校正在 [{y_min},{y_max}] 內取 {y}",
                "safe": True, "detection": "ok"}

    return {"mode": "pad", "y": None,
            "subject": [top, bottom],
            "reason": f"主體高 {bottom - top + 1}px 超過 {ch}px 裁切窗，"
                      f"裁切會切掉主體；改用完整 16:9 + 兩側模糊補邊（pillarbox blur）",
            "safe": False, "detection": "ok"}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("video")
    p.add_argument("--out-dir", default="output")
    p.add_argument("--mode", choices=["auto", "crop", "pad"], default="auto")
    p.add_argument("--bias", type=float, default=0.35,
                   help="裁切窗偏好偏移 0(貼頂)-1(貼底)，預設 0.35。"
                        "僅在成功偵測到主體時生效；偵測失敗一律貼頂 y=0（保證不削頭）")
    p.add_argument("--crop-height", type=int, default=822,
                   help="寬 1920 時的 21:9 高度（822 ≈ 21:9）")
    p.add_argument("--no-2k", action="store_true")
    p.add_argument("--thumb-at", type=float, default=2.6)
    p.add_argument("--subject-y0", type=int,
                   help="人工指定主體上緣（像素）。偵測失敗或結果可疑時使用，會跳過自動偵測")
    p.add_argument("--subject-y1", type=int,
                   help="人工指定主體下緣（像素）")
    p.add_argument("--also-pad", action="store_true",
                   help="額外再輸出一份 pad 版 21:9（crop/pad 兩版並存，供人工挑選）")
    args = p.parse_args()

    src = args.video
    if not os.path.exists(src):
        raise SystemExit(f"找不到檔案：{src}")
    if not FFMPEG:
        raise SystemExit(
            "找不到 ffmpeg。請用以下任一方式解決：\n"
            "  • 安裝：macOS `brew install ffmpeg`｜Ubuntu `sudo apt install ffmpeg`\n"
            "  • 或指定路徑：export FFMPEG=/path/to/ffmpeg\n"
            "  • 或把它放進 PATH"
        )

    os.makedirs(args.out_dir, exist_ok=True)
    stem = os.path.splitext(os.path.basename(src))[0]
    meta = probe(src)
    w, h = meta.get("width"), meta.get("height")
    if not w or not h:
        raise SystemExit("無法解析影片尺寸")

    ch = args.crop_height if args.crop_height % 2 == 0 else args.crop_height - 1
    # 模 16 對齊：非模 16 高度會讓 libx264 墊到 832 再靠 conformance crop 補回，
    # 部分 Apple 播放器（VideoToolbox 路徑）解錯這種檔——症狀為「頂部一條內容、其餘全黑」。
    # 816(=2.35:1) 這類變形寬銀幕是安全值。（2026-09-25 nordic-plaza-scene 實測踩坑）
    ch -= ch % 16
    if ch >= h:
        raise SystemExit(f"來源高度 {h} 不足，無法裁到 {ch}")

    # 16:9 母版永遠保留
    out_169 = os.path.join(args.out_dir, f"{stem}_169.mp4")
    shutil.copy2(src, out_169)

    # 取樣一幀做偵測
    sample = os.path.join(args.out_dir, f".{stem}_sample.jpg")
    run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
         "-ss", str(args.thumb_at), "-i", src, "-frames:v", "1", "-q:v", "2", sample])
    subj = detect_subject(sample)
    os.remove(sample)
    if args.subject_y0 is not None and args.subject_y1 is not None:
        subj = {"top": args.subject_y0, "bottom": args.subject_y1,
                "source": "manual", "confidence": 1.0}

    plan = choose_plan(subj, w, h, ch, args.bias)
    mode = args.mode if args.mode != "auto" else plan["mode"]

    out_219 = os.path.join(args.out_dir, f"{stem}_21x9.mp4")
    if mode == "crop":
        y = plan["y"] if plan["y"] is not None else 0
        y -= y % 2
        run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", src,
             "-vf", f"crop={w}:{ch}:0:{y}",
             "-c:v", "libx264", "-profile:v", "main", "-crf", "18", "-preset", "slow",
             "-pix_fmt", "yuv420p", "-movflags", "+faststart", out_219])
        plan["applied"] = {"mode": "crop", "y": y, "height": ch}
    else:
        fg_h = ch
        fg_w = int(round(ch * w / h / 2)) * 2
        fc = (f"[0:v]split=2[bg][fg];"
              f"[bg]scale={w}:{ch}:force_original_aspect_ratio=increase,"
              f"crop={w}:{ch},gblur=sigma=28[bgb];"
              f"[fg]scale={fg_w}:{fg_h}:flags=lanczos[fgs];"
              f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2[v]")
        run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", src,
             "-filter_complex", fc, "-map", "[v]",
             "-c:v", "libx264", "-profile:v", "main", "-crf", "18", "-preset", "slow",
             "-pix_fmt", "yuv420p", "-movflags", "+faststart", out_219])
        plan["applied"] = {"mode": "pad", "inner": [fg_w, fg_h]}

    outputs = {"169": out_169, "21x9": out_219}

    # 可選：crop 之外再輸出一份 pad 版，讓人工兩版並看挑選
    if args.also_pad and mode == "crop":
        out_pad = os.path.join(args.out_dir, f"{stem}_21x9_pad.mp4")
        fg_h = ch
        fg_w = int(round(ch * w / h / 2)) * 2
        fc = (f"[0:v]split=2[bg][fg];"
              f"[bg]scale={w}:{ch}:force_original_aspect_ratio=increase,"
              f"crop={w}:{ch},gblur=sigma=28[bgb];"
              f"[fg]scale={fg_w}:{fg_h}:flags=lanczos[fgs];"
              f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2[v]")
        run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", src,
             "-filter_complex", fc, "-map", "[v]",
             "-c:v", "libx264", "-profile:v", "main", "-crf", "18", "-preset", "slow",
             "-pix_fmt", "yuv420p", "-movflags", "+faststart", out_pad])
        outputs["21x9_pad"] = out_pad

    if not args.no_2k:
        out_2k = os.path.join(args.out_dir, f"{stem}_2k.mp4")
        run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", src,
             "-vf", "scale=2560:1440:flags=lanczos,unsharp=5:5:0.4:5:5:0.0",
             "-c:v", "libx264", "-profile:v", "main", "-level:v", "4.2", "-crf", "17", "-preset", "slow",
             "-pix_fmt", "yuv420p", "-movflags", "+faststart", out_2k])
        outputs["2k"] = out_2k

    out_thumb = os.path.join(args.out_dir, f"{stem}_thumb.jpg")
    run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
         "-ss", str(args.thumb_at), "-i", out_219, "-frames:v", "1",
         "-q:v", "3", out_thumb])
    outputs["thumb"] = out_thumb

    report = {
        "source": src, "source_size": [w, h],
        "target_ratio": round(RATIO_21_9, 3),
        "crop_window_height": ch,
        "subject": subj,
        "plan": plan,
        "outputs": outputs,
    }
    report_path = os.path.join(args.out_dir, f"{stem}_finish.json")
    with open(report_path, "w") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
