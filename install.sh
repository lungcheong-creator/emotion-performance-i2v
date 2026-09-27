#!/usr/bin/env bash
#
# emotion-performance-i2v — 一鍵安裝
#
#   bash install.sh                 # 自動偵測 skills 目錄
#   bash install.sh --platform codex
#   bash install.sh --platform workbuddy
#   bash install.sh --platform claude
#   bash install.sh --dir /custom/skills/path
#   bash install.sh --deps          # 同時安裝 Python 依賴
#
# 會安裝兩支 skill（emotion-performance-i2v 與其依賴 mlty-universe-i2v）。
# 已存在時預設不覆蓋；加 --force 才覆蓋。

set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/lungcheong-creator/emotion-performance-i2v.git}"

# 決定來源：本地 repo 目錄，或（被 curl | bash 呼叫時）先 clone 到暫存
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || echo .)/skills"
TMP_CLONE=""
if [[ ! -d "$SRC_DIR" ]]; then
  echo "找不到本地 skills/（可能透過 curl 執行）→ 從 GitHub 取得…"
  TMP_CLONE="$(mktemp -d)"
  if ! git clone --depth 1 "$REPO_URL" "$TMP_CLONE/repo" >/dev/null 2>&1; then
    echo "✗ git clone 失敗：$REPO_URL" >&2
    echo "  請確認已安裝 git 且網路可達。" >&2
    rm -rf "$TMP_CLONE"
    exit 1
  fi
  SRC_DIR="$TMP_CLONE/repo/skills"
  echo "✓ 已取得（暫存於 ${TMP_CLONE}，安裝後自動清除）"
fi
cleanup() { [[ -n "$TMP_CLONE" ]] && rm -rf "$TMP_CLONE"; }
trap cleanup EXIT

PLATFORM=""
DEST=""
FORCE=0
INSTALL_DEPS=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --platform) PLATFORM="$2"; shift 2 ;;
    --dir)      DEST="$2"; shift 2 ;;
    --force)    FORCE=1; shift ;;
    --deps)     INSTALL_DEPS=1; shift ;;
    -h|--help)  sed -n '3,15p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "未知參數：$1（用 --help 看用法）" >&2; exit 2 ;;
  esac
done

# ---- 決定目標目錄 ----
if [[ -z "$DEST" ]]; then
  case "$PLATFORM" in
    workbuddy) DEST="$HOME/.workbuddy/skills" ;;
    codex)     DEST="$HOME/.codex/skills" ;;
    claude)    DEST="$HOME/.claude/skills" ;;
    "")
      for cand in "$HOME/.workbuddy/skills" "$HOME/.codex/skills" "$HOME/.claude/skills"; do
        [[ -d "$cand" ]] && { DEST="$cand"; break; }
      done
      if [[ -z "$DEST" ]]; then
        echo "找不到已知的 skills 目錄。請用 --platform 或 --dir 指定。" >&2
        echo "  已知慣例：~/.workbuddy/skills  ~/.codex/skills  ~/.claude/skills" >&2
        exit 1
      fi
      ;;
    *) echo "--platform 只接受 workbuddy / codex / claude" >&2; exit 2 ;;
  esac
fi

echo "來源：$SRC_DIR"
echo "目標：$DEST"
echo

mkdir -p "$DEST"

# ---- 複製 ----
for s in emotion-performance-i2v mlty-universe-i2v; do
  if [[ ! -d "$SRC_DIR/$s" ]]; then
    echo "✗ 來源缺少 ${s}，跳過" >&2
    continue
  fi
  if [[ -e "$DEST/$s" && $FORCE -eq 0 ]]; then
    echo "• $s 已存在 → 略過（要覆蓋請加 --force）"
    continue
  fi
  rm -rf "$DEST/$s"
  cp -R "$SRC_DIR/$s" "$DEST/$s"
  echo "✓ 已安裝 $s"
done

# ---- 保護設定檔 ----
if [[ -f "$DEST/mlty-universe-i2v/config.example.json" && ! -f "$DEST/mlty-universe-i2v/config.json" ]]; then
  cp "$DEST/mlty-universe-i2v/config.example.json" "$DEST/mlty-universe-i2v/config.json"
  chmod 600 "$DEST/mlty-universe-i2v/config.json"
  echo "✓ 已建立 config.json（請填入 API key，權限已設為 600）"
fi

# ---- 依賴 ----
if [[ $INSTALL_DEPS -eq 1 ]]; then
  echo
  echo "安裝 Python 依賴…"
  PY="${PY:-python3}"
  "$PY" -m pip install --quiet --upgrade pillow numpy
  # ⚠️ opencv 必須鎖在 4.x：OpenCV 5.0 移除了 Haar 級聯 API（cv2.CascadeClassifier），
  #    會讓 subject_detect.py 的臉部偵測失效。實測 2026-09-27 踩過——
  #    升級到 5.0.0 後讀數流程直接 AttributeError 崩潰。
  "$PY" -m pip install --quiet --upgrade "opencv-python-headless>=4.5,<5"
  echo "✓ 依賴安裝完成（opencv 鎖定 4.x）"
fi

# ---- 環境檢查 ----
echo
echo "──── 環境檢查 ────"
PY="${PY:-python3}"
if "$PY" -c "import PIL, numpy" 2>/dev/null; then
  echo "✓ Pillow + numpy"
else
  echo "✗ 缺 Pillow / numpy  →  $PY -m pip install pillow numpy"
fi
if "$PY" -c "import cv2" 2>/dev/null; then
  cvver="$("$PY" -c "import cv2; print(getattr(cv2,'__version__','?'))" 2>/dev/null)"
  if "$PY" -c "import cv2; assert hasattr(cv2,'CascadeClassifier')" 2>/dev/null; then
    echo "✓ opencv ${cvver}（臉部偵測可用）"
  else
    echo "✗ opencv ${cvver} 沒有 CascadeClassifier → 臉部偵測失效"
    echo "   原因：OpenCV 5.0 移除了 Haar 級聯 API"
    echo "   修正：$PY -m pip install 'opencv-python-headless>=4.5,<5'"
  fi
else
  echo "• 無 opencv → 主體偵測會退回膚色法，暖色調場景可能框錯主體"
  echo "   建議：$PY -m pip install 'opencv-python-headless>=4.5,<5'"
fi
if command -v ffmpeg >/dev/null 2>&1; then
  echo "✓ ffmpeg  $(ffmpeg -version | head -1 | cut -d' ' -f1-3)"
else
  echo "✗ 缺 ffmpeg  →  macOS: brew install ffmpeg  |  Ubuntu: sudo apt install ffmpeg"
fi

echo
echo "完成。下次對話中提供一張人物照片，說「用 emotion-performance-i2v 做一支 N 秒短片」即可觸發。"
