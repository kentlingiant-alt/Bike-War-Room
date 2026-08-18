# 個人新聞儀表板

抓取多個 RSS 來源，產生一頁式的深色主題新聞儀表板，並支援把新聞加入左側「收藏」側邊欄（儲存在瀏覽器 localStorage）。

## 安裝

```bash
pip install -r requirements.txt
```

## 使用方式

最簡單的執行方式（使用預設的 `config.json` 與輸出到 `news.html`，完成後自動開瀏覽器）：

```bash
python news_dashboard.py
```

### 命令列參數

| 參數 | 說明 | 預設值 |
| --- | --- | --- |
| `-c`, `--config` | RSS 來源設定檔路徑 (JSON) | `config.json` |
| `-o`, `--output` | 輸出的 HTML 檔案路徑 | `news.html` |
| `-w`, `--workers` | 同時抓取的執行緒數量 | `12` |
| `--no-browser` | 產生後不要自動開啟瀏覽器 | 關閉自動開啟 |

範例：

```bash
python news_dashboard.py --output output/news.html --no-browser
```

## 自訂新聞來源

編輯 `config.json`，格式為「分類名稱 → 來源清單」：

```json
{
  "分類名稱": [
    {"name": "來源顯示名稱", "url": "RSS 網址"}
  ]
}
```

新增分類或來源都不需要改動 `news_dashboard.py`。

## 收藏功能

- 每則新聞旁的 ☆ 按鈕可以加入/移除收藏，收藏清單存在瀏覽器的 `localStorage`
- 收藏是「依瀏覽器」儲存的，換瀏覽器或換電腦不會同步，可用側邊欄的「匯出收藏 (JSON)」功能備份
- 因為每次執行都會重新產生 `news.html`，只要是同一個檔案路徑、同一個瀏覽器開啟，收藏紀錄就會延續

## 專案結構

```
news_dashboard/
├── news_dashboard.py       # 主程式（抓取 + 產生 HTML）
├── config.json              # RSS 來源設定
├── requirements.txt
├── templates/
│   └── dashboard.html.j2    # Jinja2 HTML 模板（含 CSS / JS）
└── README.md
```
