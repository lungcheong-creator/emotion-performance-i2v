# 生視頻模型規格（Kie.ai）

實測日期：2026-09-25；**原生音訊與對白**一節查證於 2026-09-26。全部走 `POST {KIE_API_BASE}/api/v1/jobs/createTask`。

> **⚠️ 2026-09-26 重大更正：Kling 3.0 原生就有對白與口型同步。**
> 本 skill 先前寫「對白一律後期配、口型同步不可靠」——**那是錯的**，已作廢。
> 正確事實見 §4。設計流程（`sound-design.md`）已依此翻轉：**原生音訊是主路徑，後期鋪是備援。**

---

## 選哪一個？

| 情境 | 建議模型 |
|---|---|
| 單張靜圖 → 動態，已在用、最穩 | `kling`（kling-3.0/video） |
| **要對白／旁白／環境音／配樂（含口型同步）** | **三個模型都可以**（均已實跑驗證）。差別：`kling` 多一個**音色參考**槽位、**有負面提示詞**、首幀即構圖；兩個 omni 沒有這些 |
| 靜圖 → 動態，需要 1080p/4K 原生輸出、或多鏡頭 | `omni-image`（⚠️ 輸出比例**繼承輸入圖**，要 16:9 必須先跑 `normalize_16x9.py`） |
| 多張參考圖鎖人物/服裝/場景，或帶參考影片 | `omni-reference`（⚠️ 需專用 key，已配置；比例送 `16:9`；**不吃首幀**，動作可離開源圖構圖） |

**選模型的三個判準：**
1. **要鎖背景／排除物件** → `kling`（唯一有 `negative_prompt`）
2. **要鎖姿勢與構圖** → `kling` 或 `omni-image`（首幀即構圖）。**`omni-reference` 不吃首幀**，構圖會重構
3. **要鎖音色** → `kling`（唯一有 `kling_elements` 音訊槽位）

**實測速度（同為 5s 單圖）**：`omni-image` 1m52s ＜ `kling` 2m56s ＜＜ `omni-reference` 8s 花 **13m49s**。
**`omni-reference` 明顯慢得多**，非必要不要用它跑單純的單圖任務。

---

## 1 · kling-3.0/video（`--model kling`，預設）

已驗證可用。input 欄位：

| 欄位 | 必填 | 值 |
|---|---|---|
| prompt | ✅ | ≤ 3072 字元 |
| image_urls | ✅ | 陣列，通常 1 張首幀 |
| duration | 選填 | `"3"`–`"15"`（**三個模型同一個範圍**，2026-09-25 實測）。超出範圍會報錯，不再靜默 clamp |
| mode | 選填 | `std` / `pro` / `4K`。**"2K" 會被拒** |
| sound | 選填 | true / false。**不是只有音效——原生音訊一次過渲染環境音、音效、配樂與口型同步的語音**，見 §4 |
| multi_shots | 選填 | 情感敘事一律 false |
| negative_prompt | 選填 | **本模型支援** |
| aspect_ratio | 選填 | 16:9 / 9:16 / 1:1（有圖時可省略，會自動適配） |

實測輸出：pro 模式 = 1920×1080 / 24fps。

## 2 · kling-3.0-omni/image-to-video（`--model omni-image`）

input 欄位（欄位名已用「故意錯參數」探針驗證全數通過）：

| 欄位 | 必填 | 值 / 注意 |
|---|---|---|
| prompt | ✅ | ≤ 3072 字元 |
| image_urls | ✅ | **必須且只能 1 張**（首幀）。另有「首幀＋尾幀」變體 |
| duration | 選填 | 整數 3–15，預設 5 |
| resolution | 選填 | `720p` / `1080p` / `4k`。**API 預設是 720p，腳本一律顯式送 1080p** |
| aspect_ratio | 選填 | 合法值 `16:9` / `9:16` / `1:1` / `auto`，但**單鏡頭下只接受 `auto`**。送 `16:9` 會被 422 拒絕（見下） |
| audio | 選填 | true / false（欄位名是 `audio`，不是 `sound`）。開 = 產出音軌；**omni 沒有音訊參考（鎖音色）**，見 §4 |
| customize_multi_shots | 選填 | 情感敘事一律 false |
| multi_prompt | 條件必填 | 多鏡頭時必填，最多 6 鏡，各含 prompt + duration |
| elements | 選填 | 主體素材，最多 3 個 |

**本模型不支援 `mode` 與 `negative_prompt`** —— 送了會 500。負面控制只能靠正向提示詞寫法。

### ⚠️ 實測更正：omni-image 單鏡頭下 `aspect_ratio` 只能送 `auto`

先前本文件寫「omni 一律顯式送 `16:9`」——**那是錯的，已作廢**。2026-09-25 用同樣的零成本探針複測，三個錯誤訊息把規則講清楚了：

```
aspect_ratio = "99:1"  -> 500 aspect_ratio is not within the range of allowed options   # 列舉值校驗
duration     = 2       -> 500 duration cannot be less than 3                            # 範圍校驗
duration     = 15 + ar=16:9 -> 422 aspect_ratio must be auto for image-to-video without custom multi-shot
aspect_ratio = "auto"  -> 200 success（真的建立任務）
```

教訓：`500 not within range` 只證明「這個欄位被讀取」，**不證明「16:9 會被接受」**。
當時用 `21:9` 探測，21:9 本來就不在列舉值內，所以那個錯誤訊息對「16:9 能不能用」完全沒有證明力——
是我當時推論過頭了。真正有證明力的是拿合法值去撞語意規則。

**規則（腳本已按此實作，見 `i2v.py` 的 `ar_policy`）：**

| 模型 | aspect_ratio | 原因 |
|---|---|---|
| `omni-image` | **只能 `auto`** | 單鏡頭下 422 硬擋；輸出比例**跟隨輸入圖** |
| `omni-reference` | **必須是具體比例**（送 `16:9`） | `"auto"` 不在列舉值內，回 500 |
| `kling` | 選填，省略則自動適配 | — |

**所以 omni-image 要守住 16:9，只有一條路：上傳前把參考圖正規化成 16:9。**
`scripts/normalize_16x9.py`（主體安全：主體塞得下就裁切，塞不下就模糊補邊）因此從「雙保險」升格為
**omni-image 的必要前置步驟**。`i2v.py` 對 `omni-image` 會自動檢查輸入圖比例，非 16:9 會**直接拒絕建單**（`INPUT_NOT_16X9`）。

✅ **已實拍驗證（2026-09-25 `car-omni-image-scene`）**：首幀預先正規化成 1660×934（比例 1.7773），
送 `aspect_ratio: "auto"`，輸出 **1920×1080 / 24fps / 5.04s，比例 1.7778 = 精確 16:9**。
`omni-image` 的 16:9 保證至此由推論升格為實測結論。

## 3 · kling-3.0-omni/reference-to-video（`--model omni-reference`）

✅ **已配置專用 API key 並驗證授權**（`config.json` → `MODELS.omni-reference.api_key`，chmod 600；
亦可用環境變數 `KIE_OMNI_REF_KEY` 覆寫）。
更換 key 前，若呼叫回 `401 The API key is not authorized to use this model`，代表該 key 未開通此模型。

input 欄位：與 omni-image 同構，但**語意規則不同、不可套用同一套參數**（`aspect_ratio` 這裡必須送具體比例
`16:9`，送 `auto` 會 500）。差別在於：

| 欄位 | 注意 |
|---|---|
| image_urls | **可多張參考圖，上限 7 張**（實測：8 張回 `image_urls supports at most 7 files`）。建議 1–4 張：人物 / 服裝 / 場景 |
| video_urls | 亦可傳參考影片（錯誤訊息：`requires image_urls, video_urls, or elements`） |
| elements | 或改用主體素材（最多 3 個） |
| customize_multi_shots + multi_prompt | 支持多鏡序列（與 omni-image 同構）。**`multi_prompt` 單鏡 prompt ≤500 字元**——2026-09-25 實測 700–900 字元被 500 拒（`multi_prompt[0].prompt cannot exceed 500 characters`；頂層 prompt 上限仍是 3072）。壓縮寫法：身份錨用短語（`cream-white chunky cardigan over grey turtleneck`），風格句尾置一條 |

**動作序列的正確打開方式（2026-09-25 rainy-street-scene 實測）**：用戶要「收傘、掏手機、貼耳」等
多步動作時，不要用單鏡硬塞——開 `customize_multi_shots`，**每鏡只做一件事**（3s 一鏡），
每鏡 prompt 自帶身份錨。reference 模式**不吃首幀**：參考圖是身份/場景錨，動作可以離開源圖構圖。
實測四鏡序列（出神→收傘→回神→手機貼耳）一次通過，四鏡身份一致。

多圖分工建議：**人物圖為事實必備**（鎖臉），服裝與場景圖各一張補足細節；同一張圖重複多次沒有意義。

## 4 · 原生音訊與對白（**2026-09-26 查證，本 skill 先前的結論是錯的**）

### 4.0 更正紀錄

先前本 skill 寫：「Kling 的 `sound`/`audio` 只做環境音底噪，口型同步不可靠，所以對白一律後期配。」
**這是錯的。** 正確事實：**Kling 3.0 在同一次生成裡就渲染出環境音、音效、配樂與
口型同步的語音**，對白不需要後期。

當時的錯誤成因值得記下來：`models.md` 只登記了 `sound` / `audio` 是布林值，
**沒有寫它們到底做什麼**；而 `performance-lexicon.md` §8 的能力邊界表裡
「對白口型」被列在「完全不建議」——那條是針對**舊模型與獨立唇形同步工具**的印象，
沒有隨 Kling 3.0 更新。**兩處都只記了「能不能送」，沒記「送了會怎樣」，
於是把一個已存在的能力當成了不可能。** 教訓：能力邊界表必須標註適用的模型版本。

### 4.1 怎麼驅動

| 項目 | 做法 |
|---|---|
| 開關 | `kling-3.0/video` → `sound: true`；omni 兩個端點 → `audio: true` |
| **對白** | **寫在提示詞裡，用引號包住**：`she says quietly, 'I thought hanging up would be enough.'` |
| 語氣 | 同一句裡給動詞短語：`whispers` / `shouts` / `says calmly` / `speaks nervously` |
| 說話者 | 用主詞明示：`she says ...`、`the man in the grey coat says ...` |
| **音色** | `kling_elements[].element_input_audio_urls`（**5–30 秒**音訊樣本）。**只有 `kling-3.0/video` 有這個槽位**；omni 沒有對應欄位（未驗證） |
| **對白是否可用** | **三個模型都可用**：kling、omni-image、omni-reference 均已實跑驗證含口型同步（見 `sound-design.md` §0.2–0.4） |
| 環境音 | 依場景描述**自動生成**。想更明顯就把聲音寫進提示詞（`the rain hammers the tin roof`）；想壓低就加 `quiet` / `silent` / `hushed` |
| 配樂 | 場景暗示音樂時自動生成；可指定風格。**不能指定具體曲目、BPM 或調性** |

**對白一定要寫進提示詞。**只放在 manifest 或註記裡會產出**完全沒聲音**的片子——
這是搞錯最容易、也最難察覺的一種失敗（畫面一切正常，只是啞的）。
`i2v.py` 已把 `dialogue` 欄位自動組進提示詞引號內，避免這個坑。

### 4.2 實務限制（來源標註）

| 限制 | 內容 | 出處 |
|---|---|---|
| 對白長度 | **每段 <20 詞**為宜；越長越容易失準 | ponpon.ai Kling 3.0 audio guide |
| 口型精度 | **約 90% 幀對齊**；**首字與尾字最容易飄** | 同上 |
| 說話者 | **單人最佳**；兩人以上口型精度明顯下降 | 同上 |
| 語言 | **英文最可靠**；中文／日文／韓文／西班牙文支援，精度略低 | 同上／kie.ai 產品頁 |
| 多角色指派 | Kling 3.0 可在提示詞裡**直接指派角色台詞**（3 人以上仍清楚，比 2.6 好） | kie.ai 產品頁 |
| 環境音密度 | 依場景描述自動；加 `quiet`／`silent`／`hushed` 可壓低 | ponpon.ai |
| 音效時序 | 約 85% 對得上；腳步聲最準，衣料摩擦等細音常缺 | 同上 |
| 配樂 | 原創、無版權問題；**但也不能指定曲目/BPM/調性**，品質適合當背景 | 同上 |
| 多鏡頭模式 | `multi_shots: true` 時**音效預設開啟** | docs.kie.ai |

### 4.3 成本（kie.ai 官方計價，credits / 秒）

| 模式 | 無音訊 | **有音訊** | 差幅 |
|---|---|---|---|
| std | 14（$0.07） | **20（$0.10）** | +43% |
| pro | 18（$0.09） | **27（$0.135）** | +50% |
| 4K | 67 | 67（$0.335） | — |

**所以「加聲音」約貴 43–50%。** 5s pro 有音訊 ≈ 135 credits ≈ $0.68。
**這條要寫進報價說明**，不要讓用戶以為聲音是免費附贈。

### 4.4 `kling_elements` 的完整規格

| 種類 | 規格 |
|---|---|
| 圖片元素 | 每個元素 **2–4 張** JPG/PNG，單檔 ≤10MB |
| 影片元素 | 每個元素最多 1 支 MP4/MOV，片長 ≥3s，**有效片段 3–8s** |
| **音訊元素** | 每個元素最多 1 個音訊檔，**時長 5–30 秒** |
| 數量上限 | **單一任務最多 3 個元素** |
| 提示詞成本 | **每個 `@元素名` 佔用 37 個字元**的提示詞額度 |
| 取名 | 提示詞裡的 `@name` 必須與 `kling_elements[].name` 一致（不含 `@`） |
| 時間裁切 | 影片元素可用 `start_time` / `end_time`（毫秒） |

**音訊元素是用來鎖音色的**：先給一段 5–30 秒的乾淨人聲，
模型會沿用那個音色來說提示詞裡的對白。這是「同一支片裡多鏡頭維持同一個聲音」的關鍵手段。

### 4.5 來源

- `https://docs.kie.ai/market/kling/kling-3-0`（欄位、`kling_elements`、`sound`）
- `https://docs.kie.ai/market/kling/v3-omni-image-to-video`（`audio`、`elements`）
- `https://kie.ai/kling-3-0`（多語言、多角色對白指派、官方計價）
- `https://ponpon.ai/blog/kling-3-audio-guide`（對白寫法、90% 口型、<20 詞、語言可靠度）
- `https://www.atlascloud.ai/blog/guides/advanced-kling-3-cinematic-video-tutorial`（原生音訊單次過、音訊參考工作流）

---

## 5 · 三者的共同限制

- 圖片需先上傳到 Kie.ai 取得 URL（`POST https://kieai.redpandaai.co/api/file-stream-upload`）。JPG / JPEG / PNG，單檔 ≤50MB，寬高 ≥300px，寬高比 0.4–2.5。
- 結果 URL 24 小時內有效，生成後立即下載。
- 查任務：`GET /api/v1/jobs/recordInfo?taskId=`；下載前先換 `POST /api/v1/common/download-url`。

## 6 · 探針技巧（驗證參數而不觸發生成）

要確認欄位名是否被接受，用**必然失敗**的參數去撞，讀錯誤訊息：

```python
# 1) 確認 model id 存在：丟非法 resolution
{"resolution": "999p"}  -> "resolution is not within the range of allowed options"

# 2) 確認其餘欄位全數通過：開多鏡頭但不給 multi_prompt
{"customize_multi_shots": True}  -> "multi_prompt is required when customize_multi_shots is true..."
#    出現這個錯誤 = prompt / image_urls / duration / resolution / audio / elements 全部被接受

# 3) 確認權限：正常參數 -> 401 "not authorized to use this model" = 模型權限未開通
```

錯誤訊息的**歸屬欄位**就是關鍵：它指到哪個欄位，代表前面所有欄位都已通過驗證。
