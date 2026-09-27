#!/usr/bin/env python3
"""MLTY Universe I2V Production — image-to-video via Kie.ai.

Converts confirmed storyboard stills (from the nano-banana-2 skill) into
5-8 second motion videos using Kling 3.0 (Kie.ai async job API).

Per shot:
  1. Resolve the image to a public URL. If it is a local path, upload it to
     Kie.ai's file-upload API (https://kieai.redpandaai.co) and use the
     returned fileUrl. If it is already an http(s) URL, use it directly.
  2. createTask with a model-specific request body.
  3. Poll recordInfo until success / fail.
  4. Parse resultJson -> resultUrls, swap each for a temp download link via
     /api/v1/common/download-url, then save the .mp4 locally.

Duration policy (enforced here): per-shot duration is decided by the caller
(the agent) and must land in [3, 15] -- the API's own range. Out-of-range values
are a HARD ERROR (DURATION_OUT_OF_RANGE), never a silent clamp.
The workflow's recommended gears are 5 / 8 / 10 / 15s.

Native audio policy (2026-09-26): Kling 3.0 renders ambience, SFX, music AND
lip-synced speech in the SAME pass as the video, so dialogue does NOT need
post-production. A spoken line is supplied via `dialogue` (composed into the
prompt in quotes, which is where Kling reads it from) and requires audio to be
on (`--audio` / "audio": true). Lines that cannot be spoken inside the clip are
a HARD ERROR (DIALOGUE_TOO_LONG) because they desync visibly.
A 5-30s voice sample can pin the timbre via --voice-ref (kling_elements audio).

Credentials resolve from config.json or env vars. Never hardcoded.
"""
import argparse
import json
import mimetypes
import os
import random
import re
import string
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_BASE = "https://api.kie.ai"
DEFAULT_UPLOAD_BASE = "https://kieai.redpandaai.co"

# Duration range is the API's, verified by live probing 2026-09-25 on all three
# models: "duration cannot be greater than 15" / "cannot be less than 3".
# A single generated clip therefore spans 3-15s. Values outside the range are a
# HARD ERROR, never a silent clamp: silently turning a requested 10s into 8s is
# the same class of trap as omni's silent 720p downgrade.
DURATION_MIN = 3
DURATION_MAX = 15
# Convenience gears the emotion-performance-i2v workflow offers to the user.
DURATION_GEARS = (5, 8, 10, 15)

# ---------------------------------------------------------------------------
# Native audio + dialogue (Kling 3.0)
# ---------------------------------------------------------------------------
# Verified 2026-09-26 against https://docs.kie.ai/market/kling/kling-3-0 and
# https://kie.ai/kling-3-0 (product page). Kling 3.0 renders audio natively in
# the SAME pass as the video -- ambient, SFX, music AND lip-synced speech -- so
# dialogue does NOT need post-production. How it is driven:
#   * kling-3.0/video        -> input.sound  (bool)
#   * kling-3.0-omni/*       -> input.audio  (bool)
#   * the spoken line goes IN THE PROMPT, wrapped in quotes:
#       "she says quietly, 'I thought hanging up would be enough.'"
#   * voice timbre can be pinned with a 5-30s audio reference inside
#     kling_elements[].element_input_audio_urls (kling-3.0/video only).
# Source notes worth keeping:
#   * lip sync ≈ 90% frame-accurate; FIRST AND LAST WORDS drift most.
#   * ≤1 speaker per clip is markedly better than 2+.
#   * English is the most reliable; Chinese/Japanese/Korean/Spanish supported.
#   * audio costs ~43-50% more: pro 18 -> 27 credits/sec, std 14 -> 20.
# A dialogue that physically cannot be spoken inside the clip WILL desync, so it
# is a hard error here rather than a warning (same reasoning as duration).
DIALOGUE_RATE_ZH = 3.5   # CJK characters per second, dramatic delivery
DIALOGUE_RATE_EN = 2.5   # latin words per second
# Speaking room available inside a clip of N seconds (leaves head/foot room for
# the beats that must stay silent). Keys are the gears the workflow offers.
DIALOGUE_SECONDS = {5: 2.5, 8: 3.5, 10: 4.5, 15: 6.0}
DEFAULT_DIALOGUE_STYLE = "says quietly"
# Element refs: max 3 per task, each @name costs 37 characters of prompt.
MAX_KLING_ELEMENTS = 3
ELEMENT_NAME_COST_CHARS = 37

# Model registry. Keys are the CLI/manifest model names; each maps to the real
# Kie.ai model id and the request-body shape it needs.
#   kling          -> kling-3.0/video                  (mode std|pro|4K, negative_prompt ok)
#   omni-image     -> kling-3.0-omni/image-to-video    (exactly 1 first-frame image)
#   omni-reference -> kling-3.0-omni/reference-to-video (multi image_urls / video_urls / elements)
# Verified by live probing against api.kie.ai on 2026-09-25. Omni accepts
# neither `mode` nor `negative_prompt`, and its `resolution` must be passed
# explicitly (default is 720p).
#
# aspect_ratio policy DIFFERS between the two omni endpoints -- do not unify:
#   omni-image     single shot -> MUST be "auto". Any concrete ratio, including
#                  "16:9", is rejected with 422:
#                  "aspect_ratio must be auto for image-to-video without
#                  custom multi-shot". Output ratio therefore follows the INPUT
#                  IMAGE, so to guarantee 16:9 the caller must normalise the
#                  still to 16:9 BEFORE upload (normalize_16x9.py).
#   omni-reference multi refs  -> MUST be a concrete ratio ("16:9" ok). "auto"
#                  is NOT an allowed option -> 500 "aspect_ratio is not within
#                  the range of allowed options".
#   kling          ratio is optional; when omitted it adapts from the image.
MODEL_REGISTRY = {
    "kling": {"model_id": "kling-3.0/video", "kind": "kling30", "ar_policy": "optional"},
    "omni-image": {"model_id": "kling-3.0-omni/image-to-video", "kind": "omni",
                   "ar_policy": "auto-only"},
    "omni-reference": {"model_id": "kling-3.0-omni/reference-to-video", "kind": "omni",
                       "ar_policy": "explicit"},
}


def load_config():
    cfg = {}
    env_map = {
        "KIE_API_BASE": "KIE_API_BASE",
        "KIE_UPLOAD_BASE": "KIE_UPLOAD_BASE",
        "KIE_KLING_KEY": ("MODELS", "kling", "api_key"),
        "KIE_OMNI_REF_KEY": ("MODELS", "omni-reference", "api_key"),
        "KIE_OMNI_IMAGE_KEY": ("MODELS", "omni-image", "api_key"),
    }
    for env_k, target in env_map.items():
        v = os.environ.get(env_k)
        if not v:
            continue
        if isinstance(target, str):
            cfg[target] = v
        else:
            cfg.setdefault(target[0], {}).setdefault(target[1], {})[target[2]] = v
    cfg_path = os.path.join(os.path.dirname(SKILL_DIR), "config.json")
    if not os.path.exists(cfg_path):
        cfg_path = os.path.join(SKILL_DIR, "config.json")
    if os.path.exists(cfg_path):
        try:
            with open(cfg_path) as f:
                data = json.load(f)
            for k, v in data.items():
                if k not in cfg and v is not None:
                    cfg[k] = v
                elif k == "MODELS" and isinstance(v, dict):
                    cfg.setdefault("MODELS", {})
                    for mk, mv in v.items():
                        cfg["MODELS"].setdefault(mk, {}).update(mv)
        except Exception:
            pass
    return cfg


def http_json(url, method="GET", token=None, body=None, timeout=180):
    req = urllib.request.Request(url, method=method)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    data = None
    if body is not None:
        req.add_header("Content-Type", "application/json")
        data = json.dumps(body).encode()
    try:
        with urllib.request.urlopen(req, data=data, timeout=timeout) as resp:
            raw = resp.read().decode()
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode()
        try:
            return json.loads(detail)
        except Exception:
            return {"code": e.code, "msg": detail}
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}


def _build_multipart(fields, files):
    boundary = "----i2v" + "".join(random.choices(string.ascii_letters + string.digits, k=16))
    parts = []
    for k, v in fields.items():
        parts.append(f"--{boundary}\r\n".encode())
        parts.append(f'Content-Disposition: form-data; name="{k}"\r\n\r\n'.encode())
        parts.append(str(v).encode() + b"\r\n")
    for k, path in files.items():
        parts.append(f"--{boundary}\r\n".encode())
        fn = os.path.basename(path)
        parts.append(f'Content-Disposition: form-data; name="{k}"; filename="{fn}"\r\n'.encode())
        mt = mimetypes.guess_type(path)[0] or "application/octet-stream"
        parts.append(f"Content-Type: {mt}\r\n\r\n".encode())
        with open(path, "rb") as f:
            parts.append(f.read())
        parts.append(b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def upload_local_file(local_path, upload_base, token):
    """Upload a local image to Kie.ai file-upload API; return the fileUrl."""
    fields = {"uploadPath": "mlty-i2v"}
    fn = os.path.basename(local_path)
    body, ctype = _build_multipart(fields, {"file": local_path})
    req = urllib.request.Request(f"{upload_base}/api/file-stream-upload", method="POST",
                                 data=body)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", ctype)
    # Explicit Content-Length is REQUIRED: without it urllib falls back to
    # chunked transfer, which kieai.redpandaai.co rejects for larger bodies
    # (Broken pipe). A User-Agent also helps the gateway accept the request.
    req.add_header("Content-Length", str(len(body)))
    req.add_header("User-Agent", "MLTY-I2V/1.0")
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            d = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return None, f"upload HTTP {e.code}: {e.read().decode()[:200]}"
    except Exception as e:  # noqa: BLE001
        return None, str(e)
    data = d.get("data", {})
    # Actual endpoint returns `downloadUrl` (tempfile.redpandaai.co); older docs
    # referenced `fileUrl`. Prefer downloadUrl, fall back to assembling a
    # kieai.redpandaai.co URL from filePath.
    url = data.get("downloadUrl") or data.get("fileUrl")
    if not url and data.get("filePath"):
        url = f"https://kieai.redpandaai.co/files/{data['filePath']}"
    if url:
        return url, None
    return None, f"upload unexpected response: {str(d)[:200]}"


def resolve_image_url(image, upload_base, token):
    if image.startswith(("http://", "https://")):
        return image, None
    if not os.path.exists(image):
        return None, f"FILE_NOT_FOUND: {image}"
    return upload_local_file(image, upload_base, token)


def clamp_duration(value, strict=False):
    """Return the duration as the string the API expects.

    Range is [DURATION_MIN, DURATION_MAX] = [3, 15], matching the API.

    strict=False (default, used when building a body): out-of-range or
    unparsable values are clamped, and the caller can compare the result with
    the request to notice. Prefer validate_duration() before building.
    strict=True: raise ValueError instead of clamping. Use this when you want
    a bad request to fail loudly rather than be silently altered.
    """
    try:
        v = int(value)
    except (TypeError, ValueError):
        if strict:
            raise ValueError(f"duration is not an integer: {value!r}")
        v = 5
    if strict and not (DURATION_MIN <= v <= DURATION_MAX):
        raise ValueError(
            f"duration {v} is outside the API range {DURATION_MIN}-{DURATION_MAX}")
    v = max(DURATION_MIN, min(DURATION_MAX, v))
    return str(v)


def validate_duration(value):
    """Return (ok, message). Checks a user-supplied duration against the API.

    Why this exists: previously a requested 10s was silently clamped to 8s, so
    the caller got a shorter clip with no error. Silent downgrade is worse than
    a failure. Anything outside 3-15 must be reported, not absorbed.
    """
    try:
        v = int(value)
    except (TypeError, ValueError):
        return False, f"duration must be an integer number of seconds, got {value!r}"
    if v < DURATION_MIN or v > DURATION_MAX:
        return False, (f"duration {v}s is outside the supported range "
                       f"{DURATION_MIN}-{DURATION_MAX}s; it will NOT be clamped silently")
    return True, str(v)


def _shot_images(shot):
    """Accept either `image` (single) or `images` (list)."""
    imgs = shot.get("images")
    if isinstance(imgs, list) and imgs:
        return [i for i in imgs if i]
    one = shot.get("image")
    return [one] if one else []


def dialogue_budget(duration):
    """Speaking room (seconds) inside a clip of `duration` seconds.

    Uses the gear table for the four gears the workflow offers and a linear
    estimate otherwise. Deliberately NOT the whole clip: the opening beat must
    stay silent (the objective has to land before anyone speaks) and the tail
    needs air.
    """
    try:
        d = int(duration)
    except (TypeError, ValueError):
        d = 5
    if d in DIALOGUE_SECONDS:
        return DIALOGUE_SECONDS[d]
    return max(1.5, min(6.0, round(d * 0.43, 2)))


def dialogue_length(text):
    """Estimate how many seconds `text` takes to speak.

    Handles mixed Chinese/English: CJK characters are counted individually,
    runs of latin letters/digits count as words. CJK punctuation is counted too
    because it carries a pause.
    """
    zh = sum(1 for ch in text
             if "\u4e00" <= ch <= "\u9fff"          # CJK ideographs
             or "\u3000" <= ch <= "\u303f"          # CJK punctuation
             or "\uff00" <= ch <= "\uffef")         # fullwidth forms
    words = re.findall(r"[A-Za-z0-9']+", text)
    en = len(words)
    return round(zh / DIALOGUE_RATE_ZH + en / DIALOGUE_RATE_EN, 2), zh, en


def validate_dialogue(text, duration):
    """Return (ok, message). A line that cannot be spoken in the clip desyncs.

    Source guidance rates lip sync at ~90% frame-accurate and notes that the
    FIRST AND LAST WORDS drift most. Over-long lines therefore do not just look
    rushed, they visibly break. Hard error, `--allow-long-dialogue` to override.

    A 5% grace band absorbs the estimation error at the margin (the rate model
    is a good average, not per-syllable truth) so a line sitting exactly on the
    documented limit is not rejected over rounding.
    """
    budget = dialogue_budget(duration)
    need, zh, en = dialogue_length(text)
    detail = f"{zh} CJK chars / {en} latin words"
    if need > budget * 1.05:
        return False, (f"dialogue needs ~{need}s of speech but a {duration}s clip only has "
                       f"~{budget}s of speaking room ({detail})")
    return True, (f"dialogue fits: ~{need}s of speech in ~{budget}s of room ({detail})")


def compose_dialogue_clause(dialogue, style=None, speaker=None):
    """Turn a bare line into the quoted clause Kling expects inside the prompt.

    Kling reads dialogue from the prompt in quotes and lip-syncs to it. Writing
    the line anywhere else (manifest only, a comment) produces no speech at all,
    which is the single easiest way to get a silent clip by accident.
    """
    text = (dialogue or "").strip()
    if len(text) >= 2 and text[0] in "\"'“‘「" and text[-1] in "\"'”’」":
        text = text[1:-1].strip()
    subject = (speaker or "she").strip()
    verb = (style or DEFAULT_DIALOGUE_STYLE).strip() or DEFAULT_DIALOGUE_STYLE
    return f"{subject} {verb}, '{text}'"


def check_input_ratio(src):
    """Warn when an omni-image source is not 16:9.

    omni-image forces aspect_ratio="auto", so the output ratio is inherited
    from the input still. A non-16:9 source silently produces a non-16:9 clip.
    Returns a warning string, or None when the source is 16:9 (or unreadable).
    """
    if not isinstance(src, str) or src.startswith("http") or not os.path.exists(src):
        return None
    try:
        from PIL import Image
        with Image.open(src) as im:
            w, h = im.size
    except Exception:  # noqa: BLE001 - never block generation on a probe
        return None
    ratio = w / h
    if abs(ratio - 16 / 9) <= 0.02:
        return None
    return (f"omni-image input {os.path.basename(src)} is {w}x{h} ({ratio:.2f}:1), "
            f"not 16:9 -> output will inherit this ratio. Run "
            f"normalize_16x9.py first to guarantee 16:9.")


def build_body(shot, image_urls, defaults, model_key="kling"):
    """Build the createTask body for the selected model.

    image_urls: already-resolved public URLs (local files were uploaded).
    """
    reg = MODEL_REGISTRY.get(model_key) or MODEL_REGISTRY["kling"]
    kind = reg["kind"]
    prompt = shot.get("prompt") or ""
    dur = shot.get("duration") or 5
    audio = bool(shot.get("audio", defaults.get("audio", False)))

    # Native dialogue: Kling speaks what it finds QUOTED IN THE PROMPT. A line
    # supplied only in the manifest would produce a silent clip, so it is
    # composed into the prompt here (idempotent: skipped when already present).
    dlg = (shot.get("dialogue") or "").strip()
    if dlg:
        if not audio:
            # Unambiguous intent: a line was written in order to be spoken.
            # Leaving audio off would silently yield a mute clip.
            audio = True
            sys.stderr.write("  NOTE dialogue supplied -> enabling native audio "
                             "(set \"audio\": false explicitly to keep it silent)\n")
        if dlg not in prompt:
            clause = compose_dialogue_clause(dlg, shot.get("dialogue_style"),
                                            shot.get("speaker"))
            prompt = (prompt.rstrip() + " " + clause + ".").strip() if prompt.strip() \
                else (clause[0].upper() + clause[1:] + ".")

    if kind == "omni":
        ar = shot.get("aspect_ratio") or defaults.get("aspect_ratio", "16:9")
        policy = reg.get("ar_policy", "explicit")
        if policy == "auto-only":
            # omni-image rejects every concrete ratio (422). Ratio is inherited
            # from the input image, so the caller must pre-normalise the still.
            ar = "auto"
        elif ar == "auto":
            # omni-reference does not accept "auto".
            ar = defaults.get("aspect_ratio", "16:9")
            if ar == "auto":
                ar = "16:9"
        inp = {
            "prompt": prompt,
            "image_urls": list(image_urls),
            "duration": clamp_duration(dur),
            # MUST be explicit: omni defaults to 720p.
            "resolution": shot.get("resolution") or defaults.get("resolution", "1080p"),
            "aspect_ratio": ar,
            "audio": audio,
            "customize_multi_shots": bool(shot.get("customize_multi_shots", False)),
            "elements": shot.get("_omni_elements") or [],
        }
        if inp["customize_multi_shots"] and shot.get("multi_prompt"):
            inp["multi_prompt"] = shot["multi_prompt"]
        return {"model": reg["model_id"], "input": inp}

    # kling-3.0/video
    inp = {
        "prompt": prompt,
        "image_urls": list(image_urls),
        "duration": clamp_duration(dur),
        "mode": shot.get("mode") or defaults.get("mode", "pro"),
        "sound": audio,
        "multi_shots": False,
    }
    # kling_elements carries image / video / AUDIO references. The audio slot is
    # how a specific voice timbre is pinned (5-30s sample). Max 3 elements, and
    # every @name in the prompt costs 37 characters.
    elems = shot.get("_kling_elements") or []
    if elems:
        inp["kling_elements"] = elems
    # Kling 3.0 supports negative_prompt; only send it when one is supplied
    # (per-shot overrides the manifest/CLI default).
    neg = shot.get("negative_prompt") or defaults.get("negative_prompt")
    if neg:
        inp["negative_prompt"] = neg
    # Kling auto-adapts aspect ratio from the image when one is supplied and
    # no aspect_ratio is explicitly requested.
    if shot.get("aspect_ratio"):
        inp["aspect_ratio"] = shot["aspect_ratio"]
    return {"model": reg["model_id"], "input": inp}


def poll_until_done(base, token, task_id, interval, max_poll):
    rec = None
    for _ in range(max_poll):
        time.sleep(interval)
        rec = http_json(f"{base}/api/v1/jobs/recordInfo?taskId={urllib.parse.quote(task_id)}",
                        token=token)
        d = rec.get("data", {})
        state = d.get("state") or d.get("status")
        if state == "success":
            return d, None
        if state == "fail":
            return d, d.get("failMsg") or rec.get("msg") or "unknown failure"
        # waiting / queuing / generating -> keep polling
    return rec.get("data", {}) if rec else {}, "TIMEOUT"


def extract_urls(parsed):
    if isinstance(parsed, dict):
        for key in ("resultUrls", "videos", "videoUrls", "urls"):
            v = parsed.get(key)
            if isinstance(v, list) and v and isinstance(v[0], str):
                return v
    return []


def download_clip(base, token, url, out_path):
    dl = http_json(f"{base}/api/v1/common/download-url", method="POST",
                   token=token, body={"url": url})
    dl_url = dl.get("data") if isinstance(dl, dict) else None
    if not dl_url:
        raise RuntimeError(f"no temp download url: {dl}")
    urllib.request.urlretrieve(dl_url, out_path)
    return out_path


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--manifest", help="JSON file: list of {image|images, prompt, duration, "
                                      "negative_prompt?, mode?, aspect_ratio?, audio?, resolution?}")
    p.add_argument("--model", default="kling",
                   choices=list(MODEL_REGISTRY.keys()),
                   help="image-to-video model: kling (kling-3.0/video), "
                        "omni-image (kling-3.0-omni/image-to-video, 1 first frame), "
                        "omni-reference (kling-3.0-omni/reference-to-video, multi refs)")
    p.add_argument("--image", action="append", default=[],
                   help="image path or URL (repeatable; order matches --prompt/--duration)")
    p.add_argument("--prompt", action="append", default=[],
                   help="motion prompt per image (repeatable)")
    p.add_argument("--duration", action="append", type=int, default=[],
                   help="seconds per image, 3-15 (repeatable). The emotion workflow "
                        "offers 5 / 8 / 10 / 15. Out-of-range values are reported, never "
                        "silently clamped (a requested 10 used to become 8 with no error)")
    p.add_argument("--aspect-ratio")
    p.add_argument("--resolution", help="omni only: 720p|1080p|4k (default 1080p; "
                                        "omni's own API default is 720p, so it is always sent)")
    p.add_argument("--negative-prompt", help="negative prompt (kling only; omni ignores it)")
    p.add_argument("--mode", help="kling only: std|pro|4K")
    p.add_argument("--audio", action="store_true",
                   help="generate the model's NATIVE audio track. This is not just ambience: "
                        "Kling 3.0 renders ambient, SFX, music AND lip-synced speech in the same "
                        "pass, so dialogue needs no post-production. Use --dialogue to supply a "
                        "line, or quote it directly in the prompt. Costs ~43-50%% more "
                        "(pro 18 -> 27 credits/sec)")
    p.add_argument("--dialogue", help="spoken line (single-shot / CLI mode). Composed into the "
                                      "prompt in quotes, which is where Kling reads it from. "
                                      "Length is checked against the clip's speaking room")
    p.add_argument("--dialogue-style", help=f"how it is said, e.g. whispers / says calmly "
                                           f"(default: \"{DEFAULT_DIALOGUE_STYLE}\")")
    p.add_argument("--voice-ref", help="5-30s audio file or URL used to pin the voice timbre "
                                       "(kling only; becomes kling_elements[].element_input_audio_urls)")
    p.add_argument("--element-name", help="element name for --voice-ref / --element-images; the "
                                          "prompt must contain @<name> (each @name costs 37 chars). "
                                          "Default: voice_ref")
    p.add_argument("--element-images", action="append", default=[],
                   help="2-4 images for an @element reference (kling only; can be combined with "
                        "--voice-ref). Repeat the flag once per image")
    p.add_argument("--allow-long-dialogue", action="store_true",
                   help="accept a dialogue that cannot be spoken inside the clip (it WILL desync)")
    p.add_argument("--output-dir", default=os.getcwd())
    p.add_argument("--dry-run", action="store_true",
                   help="resolve/upload images and print the exact createTask body for every "
                        "shot, then exit WITHOUT creating any job. Zero cost. Use it to "
                        "confirm the payload before spending credits.")
    p.add_argument("--allow-non-169", action="store_true",
                   help="omni-image only: override the hard 16:9 input check. Without this, a "
                        "non-16:9 first frame is refused, because omni-image forces "
                        "aspect_ratio=auto and would silently inherit that ratio.")
    p.add_argument("--allow-duration-clamp", action="store_true",
                   help="allow a requested duration outside 3-15s to be clamped and sent anyway. "
                        "Without this the shot is REFUSED, because silently returning a shorter "
                        "clip than the caller asked for is worse than failing.")
    p.add_argument("--poll-interval", type=int, default=5)
    p.add_argument("--max-poll", type=int, default=120)
    args = p.parse_args()

    cfg = load_config()
    models = cfg.get("MODELS", {})
    # API keys: prefer the key configured for the selected model, fall back to
    # the kling entry (the account-wide key). Never hardcode a key here.
    mconf = models.get(args.model) or {}
    if not mconf.get("api_key"):
        mconf = models.get("kling") or {}
    if not mconf or not mconf.get("api_key"):
        print(json.dumps({"error": "MISSING_API_KEY", "model": args.model}))
        sys.exit(2)
    base = cfg.get("KIE_API_BASE", DEFAULT_BASE).rstrip("/")
    upload_base = cfg.get("KIE_UPLOAD_BASE", DEFAULT_UPLOAD_BASE).rstrip("/")
    token = mconf["api_key"]
    defaults = cfg.get("DEFAULTS", {})

    # Build the shot list.
    shots = []
    if args.manifest:
        with open(args.manifest) as f:
            shots = json.load(f)
    elif args.model == "omni-reference" and len(args.image) > 1 and len(args.prompt) <= 1:
        # Sharp edge, guarded: for omni-reference the reason to pass several
        # images is "one shot, several references". The generic CLI loop below
        # would read repeated --image as N SEPARATE shots (N paid generations).
        # Collapse only when there is a single prompt, so the intent is
        # unambiguous; use --manifest for genuine multi-shot runs.
        shots.append({
            "images": list(args.image),
            "prompt": args.prompt[0] if args.prompt else "",
            "duration": args.duration[0] if args.duration else 5,
            "aspect_ratio": args.aspect_ratio,
            "resolution": args.resolution,
            "mode": args.mode,
            "audio": args.audio,
            "dialogue": args.dialogue,
            "dialogue_style": args.dialogue_style,
            "voice_ref": args.voice_ref,
            "element_name": args.element_name,
            "element_images": list(args.element_images),
        })
        sys.stderr.write(f"[omni-reference] {len(args.image)} --image values folded into ONE "
                         f"shot with {len(args.image)} references (not "
                         f"{len(args.image)} separate generations)\n")
    else:
        n = max(len(args.image), 1)
        for i in range(n):
            img = args.image[i] if i < len(args.image) else (args.image[0] if args.image else None)
            shots.append({
                "image": img,
                "prompt": args.prompt[i] if i < len(args.prompt) else (args.prompt[0] if args.prompt else ""),
                "duration": args.duration[i] if i < len(args.duration) else (args.duration[0] if args.duration else 5),
                "aspect_ratio": args.aspect_ratio,
                "resolution": args.resolution,
                "negative_prompt": args.negative_prompt,
                "mode": args.mode,
                "audio": args.audio,
                "dialogue": args.dialogue,
                "dialogue_style": args.dialogue_style,
                "voice_ref": args.voice_ref,
                "element_name": args.element_name,
                "element_images": list(args.element_images),
            })
    shots = [s for s in shots if _shot_images(s)]

    os.makedirs(args.output_dir, exist_ok=True)
    results = []
    for idx, shot in enumerate(shots, 1):
        src_images = _shot_images(shot)
        image_urls, err = [], None
        for src in src_images:
            sys.stderr.write(f"[shot {idx}] resolving image: {src}\n")
            u, e = resolve_image_url(src, upload_base, token)
            if e:
                err = e
                break
            image_urls.append(u)
        if err:
            results.append({"shot": idx, "image": src_images, "error": err})
            continue
        sys.stderr.write(f"[shot {idx}] image urls: {image_urls}\n")

        # --- reference elements (kling_elements) ------------------------------
        # The audio slot inside an element is how a specific voice timbre is
        # pinned. Documented for kling-3.0/video; omni exposes `elements`, not
        # `kling_elements`, and its audio-reference support is UNVERIFIED, so we
        # refuse to guess there rather than half-apply it.
        voice_ref = (shot.get("voice_ref") or "").strip()
        elem_imgs = [p for p in (shot.get("element_images") or []) if p]
        elem_name = (shot.get("element_name") or "").strip() or "voice_ref"
        if voice_ref or elem_imgs:
            if args.model != "kling":
                sys.stderr.write(f"[shot {idx}] NOTE --voice-ref/--element-images are documented for "
                                 f"kling only; ignored for {args.model} (omni audio-ref unverified)\n")
            else:
                if elem_imgs and not (2 <= len(elem_imgs) <= 4):
                    results.append({"shot": idx, "error": "ELEMENT_IMAGE_COUNT",
                                    "detail": f"{len(elem_imgs)} element images supplied; the API "
                                              f"requires 2-4 per element",
                                    "fix": "give 2-4 images, or drop --element-images"})
                    sys.stderr.write(f"[shot {idx}] BLOCKED: element images must be 2-4\n")
                    continue
                audio_urls, img_elems, aerr = [], [], None
                if voice_ref:
                    sys.stderr.write(f"[shot {idx}] resolving voice ref: {voice_ref}\n")
                    u, e = resolve_image_url(voice_ref, upload_base, token)  # any local file
                    if e:
                        aerr = f"voice ref failed: {e}"
                    else:
                        audio_urls.append(u)
                for pth in elem_imgs:
                    if aerr:
                        break
                    u, e = resolve_image_url(pth, upload_base, token)
                    if e:
                        aerr = f"element image failed: {e}"
                        break
                    img_elems.append(u)
                if aerr:
                    results.append({"shot": idx, "error": "ELEMENT_REF_FAILED", "detail": aerr})
                    sys.stderr.write(f"[shot {idx}] BLOCKED: {aerr}\n")
                    continue
                el = {"name": elem_name,
                      "description": shot.get("element_description") or elem_name}
                if img_elems:
                    el["element_input_urls"] = img_elems
                if audio_urls:
                    el["element_input_audio_urls"] = audio_urls
                shot["_kling_elements"] = [el]
                if f"@{elem_name}" not in (shot.get("prompt") or ""):
                    sys.stderr.write(
                        f"[shot {idx}] NOTE element \"{elem_name}\" defined but the prompt contains no "
                        f"@{elem_name} — the reference will likely be ignored "
                        f"(each @name also costs {ELEMENT_NAME_COST_CHARS} prompt chars)\n")

        # --- dialogue ---------------------------------------------------------
        # Checked before the body is built: a line that cannot be spoken inside
        # the clip desyncs visibly, and the drift lands on the first and last
        # words. Same reasoning as the duration gate -- fail loudly.
        dlg = (shot.get("dialogue") or "").strip()
        if dlg:
            if args.model != "kling":
                # VERIFICATION STATUS -- keep this accurate, do not over-claim:
                #   kling-3.0/video                -> lip-synced dialogue VERIFIED 2026-09-26
                #   kling-3.0-omni/image-to-video  -> lip-synced dialogue VERIFIED 2026-09-26
                #   kling-3.0-omni/reference-to-video -> lip-synced dialogue VERIFIED 2026-09-26
                #     (seine-bank-scene: Cantonese line, speech events at 4.4-5.6s and
                #      6.2-7.2s, mouth articulation confirmed frame-by-frame)
                # Still kling-only: the voice-timbre reference
                # (kling_elements[].element_input_audio_urls, 5-30s sample).
                sys.stderr.write(
                    f"[shot {idx}] NOTE voice-timbre reference is kling-only; omni has no "
                    f"verified equivalent. Lip-synced dialogue itself is confirmed on "
                    f"{args.model}\n")
            ok_d, msg_d = validate_dialogue(dlg, shot.get("duration") or 5)
            sys.stderr.write(f"[shot {idx}] DIALOGUE {msg_d}\n")
            if not ok_d:
                if not args.dry_run and not args.allow_long_dialogue:
                    results.append({"shot": idx, "error": "DIALOGUE_TOO_LONG", "detail": msg_d,
                                    "fix": "shorten the line, move it to a longer duration, or pass "
                                           "--allow-long-dialogue to accept the desync"})
                    sys.stderr.write(f"[shot {idx}] BLOCKED: {msg_d}\n")
                    continue
                sys.stderr.write(f"[shot {idx}] WARNING (overridden) {msg_d}\n")

        if args.model == "omni-image":
            offenders = []
            for src in src_images:
                warn = check_input_ratio(src)
                if warn:
                    offenders.append(warn)
            if offenders and not args.allow_non_169:
                # Hard gate: omni-image forces aspect_ratio="auto", so a non-16:9
                # first frame inevitably yields a non-16:9 clip. Refusing here is
                # the only way the 16:9 guarantee actually holds.
                results.append({"shot": idx, "image": src_images,
                                "error": "INPUT_NOT_16X9", "hints": offenders,
                                "fix": "run scripts/normalize_16x9.py on the source, or pass "
                                       "--allow-non-169 to accept the inherited ratio"})
                sys.stderr.write(f"[shot {idx}] BLOCKED: input not 16:9\n")
                for w in offenders:
                    sys.stderr.write(f"[shot {idx}]   {w}\n")
                continue
            for w in offenders:
                sys.stderr.write(f"[shot {idx}] WARNING (overridden) {w}\n")
        body = build_body(shot, image_urls, defaults, model_key=args.model)
        # Duration audit: the requested value must survive all the way into the
        # payload. A mismatch is reported loudly rather than passing unnoticed.
        duration_warn = None
        duration_block = None
        req = shot.get("duration") or defaults.get("duration")
        if req is not None:
            ok, msg = validate_duration(req)
            if not ok:
                duration_warn = msg
                sys.stderr.write(f"[shot {idx}] DURATION ERROR: {msg}\n")
            sent = body["input"].get("duration")
            if sent is not None and str(req) != str(sent):
                duration_warn = (f"requested {req}s but payload carries {sent}s")
                sys.stderr.write(f"[shot {idx}] DURATION MISMATCH: {duration_warn}\n")
                # Never ship a different length than the caller asked for.
                # A silently shortened clip is worse than a failed request.
                if not args.dry_run and not args.allow_duration_clamp:
                    duration_block = duration_warn
        if duration_block:
            results.append({"shot": idx, "image": src_images,
                            "error": "DURATION_OUT_OF_RANGE", "detail": duration_block,
                            "fix": f"use a duration in {DURATION_MIN}-{DURATION_MAX}s "
                                   f"(gears: {', '.join(map(str, DURATION_GEARS))}), or pass "
                                   f"--allow-duration-clamp to accept the shortened clip"})
            sys.stderr.write(f"[shot {idx}] BLOCKED: {duration_block}\n")
            continue
        if args.dry_run:
            # Stop right before the point of no return. Nothing is created.
            results.append({"shot": idx, "image": src_images, "image_urls": image_urls,
                            "dry_run": True, "body": body,
                            **({"duration_warn": duration_warn} if duration_warn else {})})
            sys.stderr.write(f"[shot {idx}] DRY-RUN body built, no task created\n")
            continue
        r = http_json(f"{base}/api/v1/jobs/createTask", method="POST", token=token, body=body)
        # taskId presence is the real success criterion; do not hard-fail on a
        # missing/non-200 `code` field.
        if not r or not r.get("data") or not r.get("data", {}).get("taskId"):
            results.append({"shot": idx, "image": src_images, "error": "CREATE_FAILED",
                            "sent_body": body, "response": r})
            continue
        task_id = r["data"]["taskId"]
        sys.stderr.write(f"[shot {idx}] taskId: {task_id}\n")

        data, fail = poll_until_done(base, token, task_id, args.poll_interval, args.max_poll)
        if fail:
            results.append({"shot": idx, "task_id": task_id, "error": fail})
            continue
        rj = data.get("resultJson")
        try:
            parsed = json.loads(rj) if isinstance(rj, str) else rj
        except Exception:
            parsed = {}
        urls = extract_urls(parsed)
        if not urls:
            results.append({"shot": idx, "task_id": task_id, "error": "NO_RESULT", "response": data})
            continue

        local_paths = []
        # Name the output by the shot number parsed from the source image
        # (e.g. frames/S05.png -> 05), falling back to the sequential index.
        # This keeps i2v.py consistent with batch_images.py (SXX.png frames)
        # and make_film.py (which parses mlty_kling_<NN>_*.mp4 back to S<NN>).
        stem = os.path.basename(src_images[0])
        m = re.search(r"(\d+)", stem)
        num = int(m.group(1)) if m else idx
        for j, u in enumerate(urls, 1):
            out_path = os.path.join(args.output_dir, f"mlty_{args.model}_{num:02d}_{j}.mp4")
            try:
                download_clip(base, token, u, out_path)
                local_paths.append(out_path)
            except Exception as e:  # noqa: BLE001
                local_paths.append({"url": u, "error": str(e)})
        results.append({"shot": idx, "task_id": task_id, "local_paths": local_paths,
                        "model_id": body["model"],
                        "duration": body["input"].get("duration"),
                        **({"duration_warn": duration_warn} if duration_warn else {})})

    n_err = sum(1 for r in results if r.get("error"))
    if args.dry_run:
        status = "dry-run"
    elif not results:
        status = "empty"
    elif n_err == 0:
        status = "ok"
    elif n_err == len(results):
        status = "failed"
    else:
        status = "partial"
    print(json.dumps({"status": status, "model": args.model,
                      "model_id": MODEL_REGISTRY[args.model]["model_id"],
                      "shots": len(results), "errors": n_err,
                      "results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
