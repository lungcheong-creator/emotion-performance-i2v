# emotion-performance-i2v

**一張人物照片 → 一支有表演邏輯的短片。**

這不是提示詞填空工具。它是一套**導演工作流**：先讀圖、再訪談、設計書過審、才生成，最後驗收音軌與主體安全。
適用於 Kling 3.0（`kling` / `omni-image` / `omni-reference` 三條路徑），輸出 5／8／10／15 秒短片，含**原生對白與口型同步**。

```
你的照片  ──►  劇照讀解  ──►  選項式訪談  ──►  Gate 審批  ──►  生成  ──►  音軌驗收 + 21:9 後製  ──►  HTML + MP4
              (像素量測)      (15 情緒族)      (你點頭)                 (客觀檢查)
```

---

## 它跟「寫個提示詞丟給模型」差在哪

| | 一般做法 | 這個 skill |
|---|---|---|
| 讀圖 | 目視猜 | `read_frame.py` **量**出景別、光源方位、景深、色調、音場 |
| 表演 | 堆情緒形容詞 | **邏輯鏈四問**：目標／障礙／策略／調整，每一拍都要答得出 |
| 情緒 | 一種調性套全部 | **15 族**（負向 11 ＋ 正面 4），收尾允不允許帶笑寫在選項裡 |
| 生成前 | 直接跑 | **Gate 審批**（讀解＋設計書＋溯源表＋提示詞，八件） |
| 生成後 | 看感覺 | **`check_audio.py`** 驗音軌與對白落點；**主體安全**的 21:9 裁切 |
| 音軌 | 後期補 | **原生生成**——環境音、配樂、口型同步對白在同一次 pass 完成 |

---

## 安裝

需要 **Python 3.9+**、**ffmpeg**，以及一個 [kie.ai](https://kie.ai) API key。

**一行安裝（免 clone）**：

```bash
curl -fsSL https://raw.githubusercontent.com/lungcheong-creator/emotion-performance-i2v/main/install.sh \
  | bash -s -- --platform codex --deps
```

**或先 clone 再裝**（想看原始碼、想改）：

```bash
git clone https://github.com/lungcheong-creator/emotion-performance-i2v.git
cd emotion-performance-i2v
bash install.sh --deps
```

`install.sh` 會自動偵測你的 skills 目錄並安裝**兩支** skill
（`emotion-performance-i2v` 與它依賴的管線引擎 `mlty-universe-i2v`），並做環境檢查。

指定平台：

```bash
bash install.sh --platform workbuddy   # → ~/.workbuddy/skills
bash install.sh --platform codex       # → ~/.codex/skills
bash install.sh --platform claude      # → ~/.claude/skills
bash install.sh --dir /your/skills     # 自訂路徑
```

安裝後**填入 API key**：

```bash
$EDITOR ~/.codex/skills/mlty-universe-i2v/config.json    # 換成你的路徑
```

`config.json` 已被 `.gitignore` 排除，權限自動設為 `600`，不會進版控。

> **Codex 使用者**：格式完全相同（都是 `SKILL.md`），`--platform codex` 即可。詳見 [`docs/CODEX.md`](docs/CODEX.md)。

---

## 快速開始

裝好之後，在對話裡丟一張人物照片，說：

> 用 emotion-performance-i2v 做一支 8 秒短片

流程會依序問你幾輪（都是選項式），然後給你一份審批包。**確認之後才會生成。**

也可以直接呼叫腳本：

```bash
export SKILLS_DIR=~/.codex/skills     # 依你的安裝位置
export PY=python3

# 1) 素材體檢 + 畫面讀數（會自動產出主體偵測複核圖）
$PY $SKILLS_DIR/emotion-performance-i2v/scripts/read_frame.py photo.jpg

# 2) 先乾跑看要送出的 payload（零成本，不建單）
$PY $SKILLS_DIR/mlty-universe-i2v/scripts/i2v.py \
    --model kling --manifest shots.json --output-dir out --dry-run

# 3) 確認無誤再正式生成
$PY $SKILLS_DIR/mlty-universe-i2v/scripts/i2v.py \
    --model kling --manifest shots.json --output-dir out

# 4) 驗收音軌與對白落點
$PY $SKILLS_DIR/emotion-performance-i2v/scripts/check_audio.py \
    out/*.mp4 --expect 3.5 6.9

# 5) 21:9 後製（主體安全裁切）
$PY $SKILLS_DIR/emotion-performance-i2v/scripts/finish_219.py out/*.mp4 --out-dir out
```

---

## 三個模型路徑（都已實測）

| `--model` | 模型 | 首幀鎖定 | 負面提示詞 | 對白＋口型 | 5s 耗時 |
|---|---|---|---|---|---|
| `kling` | `kling-3.0/video` | ✅ | ✅ | ✅ | 2m56s |
| `omni-image` | `kling-3.0-omni/image-to-video` | ✅ | ❌ | ✅ | **1m52s** |
| `omni-reference` | `kling-3.0-omni/reference-to-video` | ❌ 構圖會重構 | ❌ | ✅ | 8s 用 13m49s |

**怎麼選**：
- 要**鎖背景／排除物件** → `kling`（唯一有 `negative_prompt`）
- 要**鎖姿勢與構圖** → `kling` 或 `omni-image`
- 要**鎖音色** → `kling`（唯一有音訊參考槽位）
- 只是要快 → `omni-image`

時長 **3–15 秒**（建議檔位 5／8／10／15）。超出範圍會**硬擋**，不會靜默壓縮。

---

## 內建的安全機制（這是它最容易踩雷的地方，所以寫了防護）

| 機制 | 擋掉什麼 |
|---|---|
| **對白字數硬限** | 中文 5s ≈8 字／8s ≈12／10s ≈15／15s ≈21。超過直接拒絕建單——實測模型語速 5.7 字/秒塞得下，但聽起來像念稿 |
| **時長範圍硬擋** | 3–15 秒，超範圍報錯而非默默改掉 |
| **`--dry-run`** | 上傳圖片、組好 payload、**不建單**。發射前一律先跑 |
| **輸入比例檢查** | `omni-image` 非 16:9 直接擋（要 16:9 必須先正規化首幀） |
| **主體偵測 + 人眼複核圖** | 每次讀數自動產出 `*_subject_check.jpg`（紅=主體／綠=頭／黃=臉） |
| **音軌驗收** | 畫面正常但沒有聲音是最難察覺的失敗——生成後必跑 |

---

## 情緒光譜：15 族

| 極性 | 族 | 收尾 |
|---|---|---|
| 負向／內斂 | 壓抑 · 期待 · 釋然 · 警覺 · 好奇 · 內斂憤怒 · 疲憊 · 溫柔 · 決心 · 挑釁 · 追憶 | 不帶笑（不給出路） |
| **正面** | **喜悅 · 得意 · 安心 · 玩心** | **帶笑且不收** |

正面族有三個專屬寫法：**眼睛先笑、收尾不收、環境同向流動**。
刻意排除：大喜大悲的外放、喜劇肢體、群戲（模型不可靠或滑向狗血）。

---

## 檔案結構

```
emotion-performance-i2v/          ← 這個 repo
├── install.sh                    ← 一鍵安裝
├── skills/
│   ├── emotion-performance-i2v/  ← 主 skill（導演工作流）
│   │   ├── SKILL.md              ← 入口，八階段流程
│   │   ├── references/           ← 7 份協議
│   │   │   ├── visual-analysis.md      劇照讀解與動態推理
│   │   │   ├── acting-logic.md         邏輯鏈 + 15 情緒族
│   │   │   ├── performance-lexicon.md  FACS AU / Laban / 節拍器
│   │   │   ├── prompt-templates.md     六段式模板 + 實例
│   │   │   ├── sound-design.md         三層聲音 + 對白
│   │   │   ├── models.md               三模型參數與邊界
│   │   │   └── question-bank.md        四輪題庫
│   │   └── scripts/              ← 6 支工具
│   │       ├── read_frame.py          畫面讀數（+ 人眼複核圖）
│   │       ├── inspect_image.py       素材體檢
│   │       ├── subject_detect.py      主體偵測（臉部優先）
│   │       ├── normalize_16x9.py      首幀正規化
│   │       ├── finish_219.py          21:9 主體安全裁切
│   │       └── check_audio.py         音軌驗收
│   └── mlty-universe-i2v/        ← 依賴：I2V 管線引擎
│       ├── SKILL.md
│       ├── config.example.json   ← 複製成 config.json 填 key
│       └── scripts/i2v.py
└── docs/
    └── CODEX.md                  ← Codex 安裝與 AGENTS.md 說明
```

---

## 已知邊界（誠實列出）

- **音色鎖定**（`--voice-ref`，5–30 秒樣本）**只有 `kling` 有**，且尚未實跑驗證
- **四支正面族尚未實跑驗證**——「眼睛先笑」的寫法是從 FACS 推理出來的，不是實測結果
- **口型精度**：來源標註 ≈90% 幀對齊，**首字與尾字最易飄**；台詞越短越穩
- **`omni-reference` 不吃首幀**，同一支片內構圖會重構（實測臉部位置可漂移 ±25px）
- **《粵語》**：實測確認模型照粵語台詞產出對應音節數的語音，但**口音純度需要人耳判斷**，自動化量測證明不了
- **主體偵測**在暖色調場景（駝色衣物、石牆、落葉）若無 `opencv` 會框錯主體——已加臉部偵測優先，但無 cv2 時會退回不可靠的膚色法

---

## 授權

MIT — 見 [LICENSE](LICENSE)。

生成內容的商用授權取決於 [kie.ai](https://kie.ai) 與其上游模型供應商的條款，請自行確認。
