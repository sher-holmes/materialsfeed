# 材料前線

自動彙整材料科學相關期刊 RSS（表面塗層/噴塗/浸塗、絕緣、散熱、綜合材料），以類似 Threads 的動態牆呈現。全站為靜態網頁 + GitHub Actions，免主機、免資料庫。

## 架構

- `index.html` — 前端頁面，讀取 `data/feed.json` 並渲染成動態牆
- `config/sources.json` — 期刊 RSS 來源清單（可自行增減）
- `scripts/fetch_feeds.py` — 抓取所有 RSS，輸出 `data/feed.json`
- `.github/workflows/update-feed.yml` — 每天 UTC 00:00（台灣 08:00）自動執行抓取並提交更新

## 部署步驟

1. 在 GitHub 建立一個新的 repository（例如 `materials-feed`），把這個資料夾的內容全部推上去：
   ```bash
   git init
   git add .
   git commit -m "init"
   git branch -M main
   git remote add origin https://github.com/<你的帳號>/materials-feed.git
   git push -u origin main
   ```

2. 開啟 repo 的 **Settings → Actions → General → Workflow permissions**，選擇
   **「Read and write permissions」** 並儲存。
   （這一步是必要的，否則 Action 無法把抓到的新資料 commit 回 repo。）

3. 開啟 repo 的 **Settings → Pages**，Source 選擇 `main` branch、根目錄 `/`，儲存後會拿到一個
   `https://<你的帳號>.github.io/materials-feed/` 的網址。

4. 到 **Actions** 分頁，手動觸發一次 `Update materials feed`（workflow_dispatch），
   讓 `data/feed.json` 產生第一批資料。之後就會照排程每天自動跑。

## 自行調整來源

打開 `config/sources.json`，每個項目是：
```json
{ "name": "顯示名稱", "url": "RSS 網址", "category": "coating | insulation | thermal | general" }
```
新增/刪除期刊、改分類，存檔後下次 Action 執行就會生效，也可以手動觸發立即套用。

## 已知限制

- 微信公眾號沒有官方 RSS/API，這版先不處理；之後若想加，可考慮自建 RSSHub 實例，但穩定性和帳號風險要自行評估。
- 少數期刊網站（例如 RSC、部分 IEEE/ACS 期刊）RSS 網址格式較特殊或會變動，若 Action log 顯示某個來源持續失敗（`[warn]`/`[error]`），到該期刊官網的 "RSS/Alert" 頁面重新確認正確網址，更新 `sources.json` 即可。
- Elsevier/Wiley 的 RSS 有時只保留最近一段時間的文章，抓取程式會把歷史資料和新資料合併去重，不會因為單次抓取失敗就掉資料。
