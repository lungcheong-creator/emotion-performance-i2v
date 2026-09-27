#!/usr/bin/env python3
"""音軌驗收 — 確認成片真的有聲音，而且是語音不是只有底噪。

為什麼需要它
------------
畫面正常但**沒有聲音**是最難察覺的一種失敗：檔名對、長度對、解析度對，
只是整支片是啞的。而 Kling 原生對白只在「台詞寫進提示詞引號內」時才成立，
順序一錯就靜默失敗。

本腳本只做**量測**，不做聽感判斷（機器聽不懂「好聽」，但分得出「有沒有人聲」）：
  1. 有沒有音訊流（對照組：純影片檔沒有）
  2. 能量包絡 → 找出語音事件的位置與長度
  3. 300–3400Hz 共振峰帶佔比 → 區分語音 vs 低頻底噪
  4. 落點是否落在設計的那一拍（可選 --expect）

用法：
  check_audio.py out.mp4
  check_audio.py out.mp4 --expect 2.8 3.8      # 期望語音落在 2.8–3.8s
  check_audio.py out.mp4 --json
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import wave

try:
    import numpy as np
except ImportError:
    print(json.dumps({"error": "需要 numpy。安裝：pip install numpy（或 python3 -m pip install numpy）",
                      "verdict": "ERROR"}, ensure_ascii=False))
    sys.exit(2)

SPEECH_LO, SPEECH_HI = 300, 3400      # 人聲基頻與共振峰主要落點
ENVELOPE_WIN = 0.1                    # 能量包絡窗（秒）
SPEECH_RISE_DB = 4.0                  # 高於底噪中位數幾 dB 算語音事件
SILENCE_FLOOR_MARGIN = 2.0


def _ffmpeg():
    for cand in ("ffmpeg", "/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"):
        if shutil.which(cand) or os.path.exists(cand):
            return cand
    return None


def probe_streams(path):
    """回傳 (has_audio, has_video, duration_sec, stream_lines)。"""
    ff = _ffmpeg()
    if not ff:
        return None, None, None, ["ffmpeg 不可用"]
    p = subprocess.run([ff, "-hide_banner", "-i", path],
                       capture_output=True, text=True)
    txt = p.stderr
    lines = [ln.strip() for ln in txt.splitlines()
             if "Stream #" in ln or "Duration:" in ln]
    has_audio = any("Audio:" in ln for ln in lines)
    has_video = any("Video:" in ln for ln in lines)
    dur = None
    for ln in lines:
        if ln.startswith("Duration:"):
            hms = ln.split("Duration:")[1].split(",")[0].strip()
            try:
                h, m, s = hms.split(":")
                dur = round(int(h) * 3600 + int(m) * 60 + float(s), 2)
            except Exception:  # noqa: BLE001
                pass
    return has_audio, has_video, dur, lines


def decode_mono(path, sr=16000):
    ff = _ffmpeg()
    tmp = tempfile.mktemp(suffix=".wav")
    subprocess.run([ff, "-v", "error", "-y", "-i", path, "-ac", "1",
                    "-ar", str(sr), "-c:a", "pcm_s16le", tmp],
                   capture_output=True)
    if not os.path.exists(tmp):
        return None, None
    with wave.open(tmp) as w:
        rate = w.getframerate()
        x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32)
    os.unlink(tmp)
    return x / 32768.0, rate


def envelope(x, sr):
    win = max(1, int(sr * ENVELOPE_WIN))
    n = len(x) // win
    if n == 0:
        return np.array([]), np.array([])
    rms = np.array([np.sqrt((x[i * win:(i + 1) * win] ** 2).mean()) for i in range(n)])
    db = 20 * np.log10(np.maximum(rms, 1e-6))
    times = np.arange(n) * ENVELOPE_WIN
    return times, db


def speech_events(times, db):
    """能量顯著高於底噪的連續區段 → 候選語音事件。"""
    if len(db) == 0:
        return [], None
    floor = float(np.median(db))
    thresh = floor + SPEECH_RISE_DB
    loud = db > thresh
    events, i = [], 0
    while i < len(loud):
        if loud[i]:
            j = i
            while j + 1 < len(loud) and loud[j + 1]:
                j += 1
            # 合併太近的（0.2s 內視為同一句）
            if events and times[i] - events[-1]["end"] < 0.2:
                events[-1]["end"] = round(float(times[j] + ENVELOPE_WIN), 2)
                events[-1]["peak_db"] = round(max(events[-1]["peak_db"],
                                                  float(db[i:j + 1].max())), 1)
            else:
                events.append({"start": round(float(times[i]), 2),
                               "end": round(float(times[j] + ENVELOPE_WIN), 2),
                               "peak_db": round(float(db[i:j + 1].max()), 1)})
            i = j + 1
        else:
            i += 1
    for e in events:
        e["duration"] = round(e["end"] - e["start"], 2)
    return events, round(floor, 1)


def speech_band_ratio(x, sr):
    from numpy.fft import rfft, rfftfreq
    if len(x) < 64:
        return None
    sp = np.abs(rfft(x * np.hanning(len(x))))
    f = rfftfreq(len(x), 1 / sr)
    tot = sp[(f >= 20) & (f < 8000)].sum()
    if tot <= 0:
        return None
    return round(float(sp[(f >= SPEECH_LO) & (f < SPEECH_HI)].sum() / tot), 4)


def analyse(path, expect=None):
    has_audio, has_video, dur, lines = probe_streams(path)
    rep = {"file": path, "duration_sec": dur,
           "has_video": has_video, "has_audio": has_audio, "streams": lines}
    if has_audio is None:
        rep["verdict"] = "ERROR"
        rep["error"] = "ffmpeg 不可用，無法探測"
        return rep

    if not has_audio:
        rep["verdict"] = "NO_AUDIO"
        rep["reason"] = ("檔案沒有音訊流。若應該有對白／環境音，檢查："
                         "① 請求有沒有送 sound:true（kling）／audio:true（omni）；"
                         "② 對白有沒有寫在提示詞的引號內（只寫在 manifest 不會被唸出來）")
        return rep

    x, sr = decode_mono(path)
    if x is None or len(x) == 0:
        rep["verdict"] = "DECODE_FAILED"
        return rep

    times, db = envelope(x, sr)
    events, floor = speech_events(times, db)
    ratio = speech_band_ratio(x, sr)
    rms = float(np.sqrt((x ** 2).mean()))
    rep.update({
        "rms_dbfs": round(20 * np.log10(max(rms, 1e-6)), 1),
        "noise_floor_db": floor,
        "peak_db": round(float(db.max()), 1) if len(db) else None,
        "dynamic_range_db": round(float(db.max() - db.min()), 1) if len(db) else None,
        "speech_band_ratio": ratio,
        "speech_band_note": "300-3400Hz 人聲帶佔比；明顯高於純低頻底噪時代表有語音成分",
        "candidate_speech_events": events,
    })

    # 判定：有能量事件 + 中頻佔比夠高 → 視為含語音
    has_events = len(events) > 0
    likely_speech = bool(has_events and (ratio or 0) >= 0.30)
    rep["likely_speech"] = likely_speech

    if likely_speech:
        rep["verdict"] = "HAS_SPEECH"
        rep["reason"] = (f"偵測到 {len(events)} 段語音事件，"
                         f"人聲帶佔比 {ratio:.0%}，高於純底噪的預期")
    elif has_events:
        rep["verdict"] = "AMBIENT_ONLY?"
        rep["reason"] = (f"有能量事件但人聲帶佔比僅 {ratio:.0%}，"
                         "可能是純環境音／配樂，或語音極短")
    else:
        rep["verdict"] = "FLAT_AUDIO"
        rep["reason"] = "音軌能量平坦，沒有可辨識的事件或語音"

    if expect and len(expect) == 2:
        lo, hi = expect
        hit = [e for e in events if e["end"] > lo and e["start"] < hi]
        rep["expect_window"] = [lo, hi]
        rep["expect_hit"] = bool(hit)
        if hit:
            rep["expect_note"] = (f"語音落在期望窗口內："
                                  f"{hit[0]['start']}–{hit[0]['end']}s（設計 {lo}–{hi}s）")
        else:
            got = "、".join(f"{e['start']}–{e['end']}s" for e in events) or "無"
            rep["expect_note"] = (f"語音未落在期望窗口 {lo}–{hi}s；實際事件：{got}。"
                                  "提示詞要把時間戳寫明（around the Nth second she says ...）")
    return rep


def main():
    p = argparse.ArgumentParser(description="音軌驗收：真的有聲音嗎？是語音嗎？落在設計的那一拍嗎？")
    p.add_argument("video")
    p.add_argument("--expect", nargs=2, type=float, metavar=("START", "END"),
                   help="期望語音落點窗口（秒），例：--expect 2.8 3.8")
    p.add_argument("--json", action="store_true", help="只輸出 JSON")
    args = p.parse_args()

    if not os.path.exists(args.video):
        print(json.dumps({"error": f"找不到檔案：{args.video}", "verdict": "ERROR"},
                         ensure_ascii=False))
        sys.exit(2)

    rep = analyse(args.video, args.expect)

    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        print(f"[{rep['verdict']}] {os.path.basename(args.video)}")
        for ln in rep.get("streams", []):
            print(f"  {ln}")
        if rep.get("duration_sec") is not None:
            print(f"  長度 {rep['duration_sec']}s")
        if rep["verdict"] in ("NO_AUDIO", "ERROR", "DECODE_FAILED"):
            print(f"  → {rep.get('reason') or rep.get('error')}")
        else:
            print(f"  整體 {rep['rms_dbfs']} dBFS｜底噪 {rep['noise_floor_db']} dB"
                  f"｜峰值 {rep['peak_db']} dB｜動態 {rep['dynamic_range_db']} dB")
            print(f"  人聲帶（300–3400Hz）佔比 {rep['speech_band_ratio']}")
            print("  候選語音事件：")
            for e in rep["candidate_speech_events"]:
                bar = "#" * max(1, int(e["duration"] / 0.1))
                print(f"    {e['start']:5.1f}–{e['end']:5.1f}s  "
                      f"({e['duration']:.1f}s, 峰 {e['peak_db']}dB) {bar}")
            if not rep["candidate_speech_events"]:
                print("    （無）")
            print(f"  判定：{rep['reason']}")
            if "expect_note" in rep:
                flag = "OK" if rep.get("expect_hit") else "MISS"
                print(f"  落點 [{flag}] {rep['expect_note']}")
    sys.exit(0 if rep["verdict"] in ("HAS_SPEECH", "AMBIENT_ONLY?") else 1)


if __name__ == "__main__":
    main()
