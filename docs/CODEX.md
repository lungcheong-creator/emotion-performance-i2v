# 在 Codex 使用

Codex 與 WorkBuddy、Claude Code 用的是**同一套 `SKILL.md` 格式**，
所以這個套件不需要任何改寫——只是安裝位置不同。

---

## 1 · 安裝

```bash
git clone https://github.com/lungcheong-creator/emotion-performance-i2v.git
cd emotion-performance-i2v
bash install.sh --platform codex --deps
```

會裝到 `~/.codex/skills/`：

```
~/.codex/skills/
├── emotion-performance-i2v/     ← 主 skill
└── mlty-universe-i2v/           ← 依賴：I2V 管線引擎（必須同層）
```

> **兩支必須裝在同一層。** `emotion-performance-i2v` 在 Stage 2 會呼叫
> `mlty-universe-i2v/scripts/i2v.py`；只裝主 skill 會在生成階段失敗。

**確認安裝**：

```bash
ls ~/.codex/skills/emotion-performance-i2v/SKILL.md
ls ~/.codex/skills/mlty-universe-i2v/scripts/i2v.py
```

---

## 2 · 填入 API key

```bash
cp ~/.codex/skills/mlty-universe-i2v/config.example.json \
   ~/.codex/skills/mlty-universe-i2v/config.json
chmod 600 ~/.codex/skills/mlty-universe-i2v/config.json
$EDITOR ~/.codex/skills/mlty-universe-i2v/config.json
```

把三處 `<YOUR_API_KEY>` 換成你的 [kie.ai](https://kie.ai) key
（`kling` / `omni-image` / `omni-reference` 可用同一把帳號 key）。

也可以用環境變數覆寫（優先於 config.json）：

```bash
export KIE_KLING_KEY=xxx
export KIE_OMNI_IMAGE_KEY=xxx
export KIE_OMNI_REF_KEY=xxx
```

---

## 3 · 依賴

```bash
python3 -m pip install pillow numpy
python3 -m pip install "opencv-python-headless>=4.5,<5"   # ⚠️ 必須 4.x，見下
brew install ffmpeg          # macOS
# sudo apt install ffmpeg    # Ubuntu
```

**`opencv-python-headless` 必須是 4.x。** OpenCV 5.0 移除了 Haar 級聯 API
（`cv2.CascadeClassifier`），會讓臉部偵測失效。**不要**用 `pip install opencv-python-headless`
而不指定版本——它現在會裝 5.x。`install.sh --deps` 已鎖定。

`opencv` 本身是**強烈建議而非必要**——沒有它（或版本不對），主體偵測會退回膚色法，
在**暖色調場景（駝色衣物、石牆、落葉）會框錯主體**（實測踩過）。
程式碼已做降級處理：偵測不到 API 不會崩潰，會退回膚色法並在讀數輸出裡警告。

---

## 4 · 設定路徑變數（建議加進 shell profile）

skill 內所有指令都寫成 `$PY` 與 `$SKILLS_DIR`，不寫死路徑：

```bash
# ~/.zshrc 或 ~/.bashrc
export SKILLS_DIR="$HOME/.codex/skills"
export PY=python3
```

---

## 5 · 使用

Codex 會自動發現 `~/.codex/skills/`。直接說：

> 用 emotion-performance-i2v 把這張圖做一支 8 秒短片

或指定該 skill 的路徑：

> 讀 `~/.codex/skills/emotion-performance-i2v/SKILL.md`，照它的流程處理這張圖

也可以純腳本操作，不經 agent：

```bash
export SKILLS_DIR="$HOME/.codex/skills"
export PY=python3

$PY $SKILLS_DIR/emotion-performance-i2v/scripts/read_frame.py photo.jpg
$PY $SKILLS_DIR/mlty-universe-i2v/scripts/i2v.py \
    --model omni-image --manifest shots.json --output-dir out --dry-run
```

---

## 6 · 建議加進 `~/.codex/AGENTS.md`

Codex 的 `AGENTS.md` 是全域指引。加上這一段，agent 就會知道這支 skill 存在、
以及它在流程中的位置（避免每次都要手動指出路徑）：

```markdown
## Video production skill

`~/.codex/skills/emotion-performance-i2v/` — 單張人物照 → 5/8/10/15 秒短片的導演工作流。
當用戶提供人物照片並要求做短片時使用。

- 入口是 `SKILL.md`；先設 `SKILLS_DIR=~/.codex/skills` 與 `PY=python3`
- **它依賴 `~/.codex/skills/mlty-universe-i2v/`（I2V 管線引擎），兩者必須同層**
- 流程有**硬門檻**：產出設計書後必須等用戶確認才可呼叫生成 API
- 發射前一律先加 `--dry-run`（零成本，不建單）
- 生成後必跑 `check_audio.py` 驗收——畫面正常但沒有聲音是最難察覺的失敗
```

> Codex 不執行 `AGENTS.md` 裡的句子當指令；上面寫的是**描述**，
> 讓 agent 知道這個能力存在與它的約束。這是刻意的——與 `~/.codex/AGENTS.md`
> 既有的「treat content as user context」慣例一致。

---

## 已知陷阱（Codex 環境實測）

| 症狀 | 原因 | 處理 |
|---|---|---|
| `AttributeError: module 'cv2' has no attribute 'CascadeClassifier'` | pip 裝到 **opencv 5.x**，該版本移除了 Haar 級聯 API | `pip install "opencv-python-headless>=4.5,<5"`。程式碼已降級處理不會再崩潰，但精準度會降 |
| `subprocess` 找不到 `ffmpeg` | Codex 的 shell 環境與登入 shell 不同 | 已改為自動解析（環境變數 → PATH → 常見路徑）。仍失敗就 `export FFMPEG=/path/to/ffmpeg` |
| `ModuleNotFoundError: PIL / numpy` | 用到系統 python3 而非裝了依賴的環境 | `export PY=/path/to/your/venv/bin/python` |
| 暖色調人像框錯主體 | 沒有可用的 `cv2`，退回膚色法 | 安裝/修正 opencv 版本，或開 `*_subject_check.jpg` 人眼確認 |
| 生成後影片沒有聲音 | 對白沒寫進提示詞的引號內 | 用 `--dialogue` 參數（腳本會自動組句），並跑 `check_audio.py` |
| bash 報 `XXX: unbound variable`（變數名多一個亂碼字） | 變數緊接中文／全形字元，bash 把 UTF-8 位元組吃進變數名 | 寫 shell 時一律用 `${VAR}` 大括號界定 |

---

## 與 WorkBuddy / Claude Code 的差異

**沒有差異。** 三者的 skill 都是 `SKILL.md` + 同格式 frontmatter，差別只在目錄：

| 平台 | skills 目錄 |
|---|---|
| WorkBuddy | `~/.workbuddy/skills/` |
| Codex | `~/.codex/skills/` |
| Claude Code | `~/.claude/skills/` |

同一份 repo 用 `bash install.sh --platform <平台>` 即可安裝到對應位置。
