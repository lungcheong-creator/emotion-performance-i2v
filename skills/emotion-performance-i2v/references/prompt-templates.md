# 提示詞組裝模板與已驗證案例

## 1 · 英文正向提示詞骨架（實際送入模型）

六段式結構，**順序固定**，一段不漏：

```
[風格與鏡頭約定] Cinematic realistic style, single continuous take, no cuts.
[主體錨定]       A {年齡/裔} {性別} with {髮型髮色}, wearing {服裝描述}, {姿態/所在位置}
[前情交代＋目標]   {剛發生什麼} — given circumstances; she wants to {objective}
[表演設計]       {由 §1.1 依 節拍 × 情緒族 × 幅度 生成}
                 ＋（有對白時）around the {N}th second she says quietly, '{台詞}'
[環境敘事]       {環境元素 + 動態}, {色調}, {光線}
[收斂約定]       {依幅度檔位，見 §1.2}
```

寫作鐵律：
- 主體錨定必須覆蓋參考圖的關鍵識別特徵（髮型、髮色、服裝、材質），這是鎖肖像的第一道防線。
- **前情交代段必須帶上目標（Objective）**。「剛掛斷電話」只是情境；「剛掛斷電話，她想讓自己相信這件事已經結束」才是演員能用的。
- 表演設計段落裡，**動作動詞優先於情緒形容詞**。
- 情緒形容詞最多出現一次，作為整段的收尾定調。
- **對白寫在 `[表演設計]` 段落內，帶時間戳、語氣與引號**——那是 Kling 唯一會讀對白的地方。
  例：`around the third second she says quietly, '沒事了。'`
  用 `i2v.py --dialogue` 會自動組好，避免漏引號（漏了 → 啞片）。

> **⚠️ 舊版陷阱已修（第二處）**：舊骨架把 `no exaggerated crying, no big movements` 與
> `restrained and quiet` **寫死在提示詞裡**。那等於把「克制」這條舊硬規則偷偷留在系統中——
> 用戶選了「強烈」檔，提示詞卻在後面喊「不要大動作」，兩條指令互相打架。
> 這兩個位置現在都**依幅度檔位變化**（見 §1.2）。

### 1.1 · 表演設計段落：三層組裝，不是填空

`[表演設計]` 是唯一逐案生成的段落。其餘五段照抄（收斂約定依幅度微調）。

組裝算法（**照順序走，每一步都不可跳**）：

```
第 1 層 · 取骨架   →  performance-lexicon.md §7.1 六段節拍，依時長摺疊（§7.3）
第 2 層 · 填情緒   →  acting-logic.md §4 該族的六段填充
第 3 層 · 掛邏輯   →  acting-logic.md §3：每一拍答出它在邏輯鏈上的位置
                     （入戲=目標已在場 / 浮起=策略洩漏 / 停頓=障礙 / 轉折=換招 / 回收=後果 / 懸置=未達成）
```

然後寫成一段英文。**時間戳必寫**，寫法是 `around the Nth second`。

**實例一：壓抑系 × 四檔時長**（同一族的骨架摺疊，可直接抄）

5s — 1 拍（無停頓、無回收；敘事性來自「換招」）
```
Her gaze rests low and slightly to one side; over the first two seconds a quiet, suppressed
sadness surfaces in her eyes; around the third second she lifts her gaze and turns her head
a few degrees toward the window; she blinks once, slowly, and settles there.
```

8s — 2 拍（＋停頓）
```
At the start her gaze rests low. Around the second second a quiet, suppressed sadness
surfaces in her eyes. From around the third to the fourth second she barely moves at all —
only the rise and fall of her breathing and one slow blink, every other part of her body
still. Around the fifth second she lifts her gaze and turns her head a few degrees toward
the window, once, and settles there.
```

10s — 3 拍（＋停頓＋回收）
```
At the start her gaze rests low. Around the second second a quiet, suppressed sadness
surfaces in her eyes. From around the fourth to the fifth second she barely moves at all —
only the rise and fall of her breathing and one slow blink, every other part of her body
still. Around the sixth second she lifts her gaze and turns her head a few degrees toward
the window, once. Around the eighth second her gaze recedes and her shoulders drop a
fraction, the movement settling back to stillness without resolving.
```

15s — 4 拍（＋長停頓＋回收）
```
At the start her gaze rests low. Around the third second a quiet, suppressed sadness
surfaces in her eyes. From around the fifth to the eighth second she barely moves at all —
only the rise and fall of her breathing and one slow blink, every other part of her body
still. Around the ninth second she lifts her gaze and turns her head a few degrees toward
the window, once. Around the twelfth second her gaze recedes, her shoulders drop a fraction
and her posture softens, the movement settling back to stillness without resolving.
```

> **⚠️ 骨架不寫嘴部狀態（2026-09-26 修正）**
> 舊版這三段全部以 `lips softly closed` 開場，**五處範例無一例外**——結果是
> 「閉唇」變成示範性默認，agent 照抄，**連正向族也被寫成不笑**。
> 現在骨架只寫**視線**與**身體**，**嘴部一律由情緒族與音軌狀態決定**：
>
> | 情況 | 嘴部寫法 | 出處 |
> |---|---|---|
> | 負向族、無對白 | `lips softly closed`（可寫，但不強制） | `sound-design.md` §9.2 |
> | 正向族（F-12~F-15） | 依族的節拍填充寫——**浮起拍常是「抿住」、轉折拍才是「笑開」** | `acting-logic.md` §4 |
> | 有對白（V3） | 只在该拍寫開口，其餘拍寫 `every other part of her body still` | `sound-design.md` §9.2 |

**實例二：換一族，證明骨架不變、戲全變**

同一個 8s 骨架（入戲→浮起→停頓→轉折→懸置），換成**警覺系**、**決心系**與**喜悅系**：

警覺系 × 8s（負向族）
```
At the start she is mid-motion, one hand partway through a gesture. Around the second
second the movement simply stops — the hand freezes where it is, her head tilts a few
degrees and her eyes scan toward something off to the left, out of frame. From around the
third to the fourth second she does not move at all, only listening, her breath held
shallow. Around the fifth second she blinks once and her shoulders lower a fraction, but
she never returns to the loose posture she started with.
```

決心系 × 8s（負向族）
```
At the start her face is still. Around the second second her breathing deepens noticeably.
From around the third to the fourth second her eyes close and she stays completely still —
only the rise of her chest. Around the fifth second her eyes open, her chin lifts slightly,
and the weight of her body shifts forward as if she has already decided; she holds there
without moving further.
```

**喜悅系 × 8s（正向族 —— 注意它與上面兩段的差別）**
```
At the start her lips are pressed together as if holding something back, and the skin
around her eyes is already crinkling. Around the second second the crinkling deepens but
her mouth stays closed — she is keeping it in. From around the third to the fourth second
she barely moves at all, only the rise and fall of her breathing, every other part of her
body still. Around the fifth second the smile finally arrives and both corners of her mouth
lift, the head tilting a few degrees as it lands. She holds there, the smile staying on her
face, not fading.
```

**差別在哪裡**：
- **前三段的浮起拍全在眼睛**（悲傷、警戒、決心、喜悅的浮起都是眼睛先動），
  **但轉折拍只有喜悅系是「笑開」**——負向族的轉折是「把神收回來」或「重心前移」。
- **收尾方向相反**：警覺與決心是「不回到起點的鬆」，**喜悅是「笑意不收」**。
- **嘴部只有喜悅系寫**（`lips pressed together` → `both corners lift`），
  另兩族根本沒提嘴——因為它們的戲不在嘴。
- 「停頓」一拍四族四樣：悲傷是**忍**、警覺是**聽**、決心是**閉眼**、喜悅是**壓住笑意**。
  **骨架相同，表演完全不同。**

### 1.2 · 收斂約定與禁令：依幅度檔位變化

舊版把克制寫死。現在三檔各有一組，**不要混用**：

| 幅度 | `[表演設計]` 尾段禁令 | `[收斂約定]` |
|---|---|---|
| **A 克制** | `no exaggerated crying, no big movements` | `restrained and quiet, natural realistic color grading, no subtitles, no on-screen text` |
| **B 標準** | `no exaggerated crying, no melodrama` | `restrained but readable, natural realistic color grading, no subtitles, no on-screen text` |
| **C 強烈** | `no melodrama, no theatrics` | `emotionally charged but still one continuous take, natural realistic color grading, no subtitles, no on-screen text` |

**注意**：三檔都**不寫** `no big movements` 以外的負面詞去壓表情——
C 檔若還留著 `restrained`，模型的兩條指令會互相抵銷，結果是「想動又不敢動」的僵硬。

> **⚠️ 舊版第二個地雷：`no narration` 與 `voiceover`**
> 舊版收斂約定一律寫 `no narration`，基礎負面詞包裡也放著 `voiceover`。
> **要旁白／對白時，這兩個會直接把你要的人聲壓掉。**
> 規則：`no narration` 與 `voiceover` **只在確定不要 L3 人聲時才留**；
> 一旦選了畫外旁白或畫內對白，兩者都要移除。

### 1.3 · 三個必寫的防護句（時長 ≥10s 時一定要有）

| 病症 | 防護句 |
|---|---|
| 自行加戲 | `every other part of her body still`（緊跟在停頓拍描述後） |
| 拖慢 | 全篇用 `steady, unhurried`；**禁用 `slow motion`** |
| 循環 | `once`（眨眼、轉頭各寫一次）；`she lifts her gaze ... once` |

**有對白時**：防護句仍要留，但**停頓拍的 `every other part of her body still` 不要蓋到開口的那一拍**——
嘴是例外。


## 2 · 負面提示詞基礎包（每次都帶）

```
face swap, changed facial features, identity drift, deformed face, deformed hands,
extra fingers, jump cuts, scene cuts, whip pan, cartoon, anime, 3D render,
oversaturated, exaggerated crying, sobbing, laughing, fast camera movement,
chaotic shaking, white band, letterbox, subtitles, captions, text overlay,
watermark, logo
```

> **`laughing` 的邊界（2026-09-26 補充）**
> 基礎包裡的 `laughing` 堵的是**大笑**（模型呈現不可靠、會滑向綜藝表情），
> **不是堵微笑**。正向族（F-12~F-15）寫的笑完全不受它影響——
> 因為那些族寫的是 `the skin around her eyes crinkles` 與 `one corner of the mouth lifts`，
> 與 `laughing` 不是同一個詞彙層級。
>
> **唯一要注意的是**：不要為了「保險」而加 `smiling` 負面詞。那會直接把正向族殺掉。

> **⚠️ `voiceover` 已從基礎包移除**（2026-09-26）。它原本每次都帶，
> 但那會**壓制畫外旁白與對白**。現在只在**確定不要人聲**時才追加。

針對性追加（依情境選用）：
- 有背景人物 → `crowd touching her, people colliding with her, clearly visible faces of background people`
- 有環繞 → `face warping during rotation, fast orbit rotation`
- **首幀有笑意，但目標族是負向** → `smiling, looking at camera smiling`
  （**僅在已把笑意寫成「褪去」節拍時才加**。若結尾就是含笑收尾，**不要加**——見 `SKILL.md` 常見坑）
- **目標族是正向（F-12~F-15）** → **不加任何 smile 相關負面詞**。
  正向族要的是「笑留下來」，加了等於自我抵銷。
  若首幀是中性／嚴肅而要建立笑，**不靠負面詞，靠節拍**：
  浮起拍寫 `the skin around her eyes begins to crinkle`（**眼睛先**），轉折拍才是 `both corners of her mouth lift`
- 有特定道具 → 該道具的錯誤狀態，如 `phone floating, extra phone`
- **不要人聲時** → `voiceover, narration, speaking, talking, mouth moving continuously, lip sync`
- **有對白時** → **只加** `talking throughout`（避免模型讓嘴從頭動到尾），
  **絕對不加** `lip sync`／`speaking`／`voiceover`——那會把對白壓掉

## 3 · 正誤示範

**錯誤**（只有狀態，沒有指令）：
> A sad woman stands in the rain, looking very melancholic and emotional, cinematic.

問題：sad / melancholic 是結果不是動作；沒有前情；沒有可執行的身體行為；模型只能猜。

**正確**：
> Cinematic realistic style, single continuous take. A young woman with long dark hair, wearing a soaked grey coat, stands alone on an empty street corner, just after ending a phone call. She does not move except for the slow rise of her breath; the inner corners of her brows lift slightly, and her gaze drops from the lens to the wet asphalt. Rain streaks past in the foreground, out of focus. Cool desaturated blue-gray grading, single continuous take, restrained and quiet, no subtitles, no narration.

差別：每一個情緒都被翻成可拍的行為。

**正向示範（2026-09-26 新增）**：

**錯誤**（正向內容最常見的寫法）：
> A happy woman smiling warmly at the camera, joyful and cheerful, bright and uplifting.

問題：`smiling warmly` / `happy` / `joyful` 全是結果詞，而且**跳過了「忍」的階段**。
模型只會給一個嘴角上揚但眼睛沒動的假笑——這是正向片最常見的失敗。

**正確**：
> Cinematic realistic style, single continuous take. A young woman in a camel coat stands on
> a stone quay, just told something she has been waiting to hear. Her lips are pressed
> together as if holding it in, and the skin around her eyes is already crinkling. She does
> not move except for the rise of her breath. Then the smile arrives — both corners of her
> mouth lift and her head tilts a few degrees, once. She holds there, the smile staying on
> her face without fading. Warm autumn light, soft and low-contrast, one continuous take,
> steady and unhurried, no subtitles, no on-screen text.

差別：**先忍、再放**；笑由**眼睛**開始；收尾**不收**。三個都是可拍的行為，不是形容詞。

## 4 · 已驗證案例（2026-09-24 實測，同一情感系列）

### 案例 1 · 車內情緒鏡頭（`kling-car-scene`）

節拍：她獨坐車內，剛與前任通完電話 → 手機垂放腿上，視線低垂 → 窗外街上一對情侶擁吻成剪影（對比）。

關鍵寫法：
> Just after a phone call with her ex, she loosely holds her phone in one hand resting on her lap, eyes cast downward, lips gently pressed, expression subdued and lost in thought, quietly holding back sadness — no exaggerated crying, no big movements. The camera slowly pushes in from inside the car, drifting past her profile toward the window; focus settles on the street outside: the backlit silhouette of a couple embracing and kissing on the sidewalk, their faces indistinct in distance and shallow depth of field.

要點：把「前任」寫進前情；用剪影保護背景人物五官（同時是美感與技術雙贏）。

### 案例 2 · 曠場女子（`kling-plaza-scene`）

節拍：靜立 → 緩慢推近 → 環境流動（風、雲影、遠處行人）→ 定格。

關鍵寫法：
> She gazes quietly into the camera with a subdued, melancholic expression, lips softly closed, eyes holding a quiet sadness. The camera pushes in very slowly toward her face with a gentle breathing handheld tremor; a cold breeze stirs loose strands of her hair and the hem of her scarf. Soft overcast daylight; slow-moving clouds make light and shadow drift subtly across the concrete around her; faint dust particles float in the cold air.

要點：情緒交給風、雲影、塵埃；人物本身幾乎不動。

### 案例 3 · 慢動作環繞 × 加速人流（`kling-orbit-scene`）

節拍：**淺笑緩緩褪去** → 一次緩慢眨眼 → 環繞 90–120° → 人群加速流過 → 收停。

關鍵寫法：
> Her faint smile slowly fades into a subdued, melancholic look — brows faintly knit, lips softly closed, helplessness and quiet resignation carried only in micro-movements: one slow blink, the faint rise of a breath. The camera performs a very slow, steady orbital arc of about a quarter turn around her, gliding like a dolly on a rail, no shaking. In stark contrast, the surrounding pedestrians rush past at visibly accelerated speed, streaking into long motion-blur trails as if time-lapsed — the whole world races by, only she moves in slow motion.

要點：**首幀表情衝突的標準解法**——不硬壓，寫成轉折 beat；雙速對比用 "in stark contrast" 明示。

## 5 · 交付 HTML 樣板結構

單一 HTML，深色主題，內部錨點式排列，區塊順序：

1. Header：標題 + 一句話情境 + 標籤列（模型 / 時長 / **幅度** / 比例 / 運鏡）
2. 參考圖（`<img src="ref_shot01.jpg">`）+ 保留清單
3. **劇照讀解**（新增，見 `visual-analysis.md` §7）：讀數摘要 + 六欄讀解表 + 「這張圖正在發生的動詞」+ 動態推理六條 + 風險
4. 播放器：`<video controls playsinline poster="output/*_thumb.jpg" src="output/*_21x9.mp4">` + 下載連結（21:9 與母版）
5. 表演設計書：**情緒方向族 / 前情 / 幅度檔** + 節拍表（**多一欄「邏輯鏈位置」**）+ 時間軸色塊
6. **溯源表**（新增，見 §6）：用戶原話 → 落在哪個節拍／動作／環境元素
7. **聲音設計表**（三層 L1／L2／L3，每層附畫面依據 + 對白內容/字數/落點 + 後期交付清單，模板見 `sound-design.md` §10）
8. 提示詞區：英文 / 中文 / 負面，各帶 `data-target` 複製按鈕
9. 呼叫參數表
10. 驗收重點與備案
11. footer：taskId、生成時間、系列索引

**節拍表的 HTML 欄位（比舊版多一欄）**：

```html
<table>
  <thead><tr><th>時段</th><th>節拍段</th><th>邏輯鏈位置</th><th>表演內容</th></tr></thead>
  <tbody>
    <tr><td>0–1.5s</td><td>入戲</td><td>目標已在場</td><td>視線低，唇輕閉</td></tr>
    <tr><td>1.5–3.0s</td><td>浮起</td><td>策略洩漏</td><td>眉內側微抬</td></tr>
    <tr><td>3.0–4.5s</td><td>停頓</td><td>障礙壓住策略</td><td>只有呼吸＋一次眨眼</td></tr>
    <tr><td>4.5–6.0s</td><td>轉折</td><td>換招</td><td>抬眼望向車窗外畫外</td></tr>
    <tr><td>6.0–8.0s</td><td>懸置</td><td>目標未達成</td><td>定住，不解決</td></tr>
  </tbody>
</table>
```

---

## 6 · 溯源表模板（貼合用戶最初的靈感）

**這是交付物的必要區塊，不是可選項。**用途：讓用戶能一眼驗證「我當初說的東西有沒有被做出來」。

```markdown
### 溯源表

用戶原話（每張圖單獨採集）：
> 「一直覺得掛掉電話那一秒是最安靜的時刻。」

| # | 用戶原話 | 落在哪裡 | 具體實現 |
|---|---|---|---|
| 1 | 「掛掉電話那一秒」 | 前情交代段 + 入戲拍 | `just after a phone call ends`；0–1.5s 全靜，只有呼吸 |
| 2 | 「最安靜的時刻」 | 整體節奏 + 停頓拍 + 音軌方向 | 停頓拍拉到 1.5s；音軌走 G2 只有環境音，不加配樂 |
| 3 | （讀解推定）「她還沒辦法把視線移開」 | 轉折拍 | `around the fifth second she lifts her gaze and turns her head toward the window, once` |

**未採用的原話**
| 原話 | 未採用原因 |
|---|---|
| 「想加一點雨」 | 讀數顯示背景無雨滴痕跡，加雨會與源素材的光線邏輯衝突；建議另拍一支雨景素材 |
```

**硬規則**：來源必須來自用戶原話或讀解的推定，不可自己編一句再假裝是用戶說的。
沒有原話輸入（N4 選項）時，溯源表退化為「讀解推定 → 落在哪裡」，並在表頭註明「無額外意圖輸入」。

複製按鈕腳本（無外部依賴）：

```html
<script>
document.querySelectorAll('button.copy').forEach(b=>{
  b.addEventListener('click',()=>{
    const el=document.getElementById(b.dataset.target);
    const done=()=>{const o=b.textContent;b.textContent='已複製 ✓';
      setTimeout(()=>b.textContent=o,1400)};
    if(navigator.clipboard&&navigator.clipboard.writeText){
      navigator.clipboard.writeText(el.innerText).then(done).catch(()=>{});
    }else{
      const ta=document.createElement('textarea');ta.value=el.innerText;
      document.body.appendChild(ta);ta.select();document.execCommand('copy');
      ta.remove();done();
    }
  });
});
</script>
```

## 7 · manifest 模板（`shots.json`）

> `duration` 填**用戶選定的秒數**（5 / 8 / 10 / 15）。三個模型都支援 3–15；
> 超出範圍會**直接報錯，不會被靜默縮短**（先前 10 被壓成 8 的問題已修）。
> 提示詞的 `[表演設計]` 段落必須與此處的秒數**配同一套節拍**（見 §1.1）。

`kling-3.0/video`：

```json
[
  {
    "image": "<絕對路徑>/ref_shot01.jpg",
    "prompt": "<英文提示詞>",
    "negative_prompt": "<負面提示詞>",
    "duration": 8,
    "aspect_ratio": "16:9",
    "mode": "pro",
    "audio": false
  }
]
```

`kling-3.0-omni/image-to-video`（單首幀）或 `kling-3.0-omni/reference-to-video`（多參考圖）：

```json
[
  {
    "images": [
      "<絕對路徑>/ref_person.jpg",
      "<絕對路徑>/ref_outfit.jpg",
      "<絕對路徑>/ref_scene.jpg"
    ],
    "prompt": "<英文提示詞>",
    "duration": 8,
    "resolution": "1080p",
    "aspect_ratio": "16:9",
    "audio": false
  }
]
```

呼叫時用 `--model kling | omni-image | omni-reference` 對應。omni 不吃 `negative_prompt` 與 `mode`；
單首幀模式（omni-image）的 `images` 只能放一張。參考圖上限 7 張。

**音軌欄位（依 `sound-design.md` §0 選定的路徑）**

| 路徑 | kling | omni | 說明 |
|---|---|---|---|
| **原生（推薦）** | `"sound": true` | `"audio": true` | 同一支生成即含環境音／配樂／**口型同步的對白** |
| 後期全鋪 | `"sound": false` | `"audio": false` | 模型不出音，聲音全部後期 |

**原生路徑有對白時，manifest 直接寫 `dialogue`**，`i2v.py` 會把它組進提示詞的引號內
（手寫 prompt 很容易漏引號，漏了就變啞片）：

```json
{
  "image": "/abs/ref_shot01.jpg",
  "prompt": "<英文提示詞，不含對白>",
  "duration": 8,
  "mode": "pro",
  "audio": true,
  "dialogue": "沒事了。",
  "dialogue_style": "says quietly"
}
```

可選：`voice_ref`（5–30 秒音訊樣本，鎖音色；**kling 專屬**，`i2v.py --voice-ref`）、
`element_images`（2–4 張，配 `element_name` 用 `@名稱` 引用，**kling 專屬**）。

**嘴部詞要依情況選，不可一律套用：**

| 情況 | 正向詞 | 負面詞追加 |
|---|---|---|
| 完全無對白（V1） | `her lips stay softly closed throughout` | `voiceover, narration, speaking, talking, lip sync, mouth moving continuously` |
| 有對白（V3） | 該拍寫時間戳＋對白；**其他拍才寫** `every other part of her body still` | 只加 `talking throughout`。**不可加 `lip sync`／`speaking`／`voiceover`** |
| 畫外旁白（V2） | `she does not speak at any point; her lips stay closed` | `visible lip sync, mouth forming words`。**移除 `voiceover`** |

**manifest 裡的 `aspect_ratio` 寫什麼，取決於模型：**

| `--model` | manifest 寫 | 說明 |
|---|---|---|
| `omni-reference` | `"aspect_ratio": "16:9"` | 必須具體比例，`auto` 會 500 |
| `omni-image` | 可略過（腳本強制改成 `auto`） | **要 16:9 得先把輸入圖跑 `normalize_16x9.py`**，請求參數幫不上忙 |
| `kling` | 可略過 | 有圖時自動適配 |

