# Claude Monitor

Windows 桌面用量 HUD，在同一行顯示 **Claude Code** 與 **Codex** 的訂閱額度。程式只讀取兩個 CLI 已登入帳號所提供的用量資訊，不會送出模型提示。

介面採單列、無進度條設計；百分比一律代表「已使用」。重設時間、最後更新時間與錯誤原因放在滑鼠提示中，避免佔用桌面空間。

完整的啟動、登入與診斷說明請見 [MONITOR_USAGE.md](MONITOR_USAGE.md)。

## 主要功能

- Claude 與 Codex 同時顯示，兩個來源各自抓取，單一來源失敗不會阻塞另一個。
- 每 3 分鐘自動刷新；手動刷新時不會重複啟動仍在執行的同來源工作。
- 更新失敗時保留上次成功資料、淡化顯示，並在提示框提供原因。
- 用量顏色依每個百分比獨立判斷：
  - `≤ 40%`：鼠尾草綠 `#A8C2B5`
  - `> 40%` 且 `≤ 80%`：灰褐金 `#DDBE8F`
  - `> 80%`：煙粉紅 `#D99393`
- 預設固定在第 2 螢幕工作區左下角；找不到第 2 螢幕時退回主螢幕。
- 無邊框、圓角、保持置頂，支援解除固定後拖曳及右鍵復位。
- 單一 `claude_monitor.vbs` 提供每日監督、立即啟動與停止模式。
- 診斷紀錄會輪替，僅保留成功視窗數與錯誤分類，不記錄完整終端輸出、帳號或憑證。

## 環境需求

- Windows 10 或 Windows 11
- 已登入的 Claude Code CLI
- 已以 ChatGPT 帳號登入的 Codex CLI
- 專用 Python 環境：放在專案上一層的 `.venv_claude_monitor`
- Python 套件：`pywinpty`、`pyte`

安裝依賴：

```powershell
$Repo = 'C:\path\to\claude_monitor'
$Python = Join-Path (Split-Path $Repo -Parent) '.venv_claude_monitor\Scripts\python.exe'
& $Python -m pip install -r (Join-Path $Repo 'requirements.txt')
```

如 CLI 不在預設位置，可設定：

- `CLAUDE_MONITOR_CLAUDE_PATH`
- `CLAUDE_MONITOR_CODEX_PATH`

## 啟動與停止

直接雙擊 `claude_monitor.vbs` 等同 `daily` 模式：

- `daily`：常駐監督，在每天 07:00–19:00 確保 HUD 執行，其餘時間停止 HUD。
- `start`：立即啟動一次 HUD。
- `stop`：停止 HUD，並通知正在執行的每日 supervisor 結束。

```text
wscript.exe "C:\path\to\claude_monitor\claude_monitor.vbs" start
wscript.exe "C:\path\to\claude_monitor\claude_monitor.vbs" stop
```

排程在早上 07:00 啟動時，工作排程器的動作可設為：

```text
wscript.exe //B //Nologo "C:\path\to\claude_monitor\claude_monitor.vbs" daily
```

HUD 是互動式桌面程式。排程應使用「僅限使用者登入時執行」；已登入但鎖定的工作階段仍可啟動，完全登出時則無法在使用者桌面顯示。

## 操作方式

| 操作 | 功能 |
|---|---|
| 滑鼠停在 Claude 或 Codex 區域 | 查看最後更新、重設時間及錯誤原因 |
| 點擊「固定」 | 解除固定，允許拖曳；再次點擊可重新固定 |
| 點擊 `↻` | 更新 Claude 與 Codex |
| 點擊 `×` | 關閉目前 HUD |
| 右鍵 | 更新、回到第 2 螢幕左下角或退出 |

## 資料來源

### Claude

程式透過 ConPTY 在隔離模式啟動 Claude Code，送出 `/usage` 後解析 CLI 顯示的本次、每週、模型額度與用量點數狀態。它不會自動處理登入、信任確認或送出一般模型提示。

Claude 的終端畫面會分多次重繪，因此程式會等待額度畫面完整且內容穩定後才採用結果，避免讀到過渡中的錯誤百分比。

### Codex

程式啟動 Codex 本機 stdio `app-server`，呼叫 `account/rateLimits/read` 取得額度視窗。`usedPercent` 會直接視為已使用比例，不做反向換算。

缺少額度不會被當成 `0%`；畫面會顯示未提供、未啟用或明確錯誤。

## 診斷

在 PowerShell 執行：

```powershell
Set-Location 'C:\path\to\claude_monitor'
& '..\.venv_claude_monitor\Scripts\python.exe' claude_monitor.py --diagnose all
```

也可將 `all` 改成 `claude` 或 `codex`。診斷會實際查詢額度，完成後關閉由它建立的 CLI 程序。

GUI 記錄位於 `monitor_diagnostics.log`，上限 100 KB，另保留一份輪替檔。

## 專案檔案

```text
claude_monitor.py      HUD、刷新排程、顯示與診斷入口
usage_sources.py       Claude／Codex 額度抓取與解析
monitor_dock.py        Windows 多螢幕工作區定位與置頂監督
claude_monitor.vbs     daily／start／stop 靜默啟動器
requirements.txt       Python 依賴
MONITOR_USAGE.md       詳細操作與疑難排解
test_usage_sources.py  離線解析檢查
```

## 常見問題

### Claude 或 Codex 顯示 `!`

將滑鼠停在對應來源查看原因，再執行該來源的診斷模式。常見原因是 CLI 尚未登入、登入已過期、CLI 版本不支援所需介面，或訂閱帳號沒有提供對應額度。

### 更新失敗後數字沒有消失

這是預期行為。HUD 會保留最後一次成功資料並淡化顯示，避免短暫錯誤把可用資訊清空；提示框會標示資料時間與失敗原因。

### 修改程式後畫面仍是舊版

已執行的 Python 不會自動載入檔案變更。請先使用 `stop` 停止舊程序，再使用 `start` 或 `daily` 重新啟動。
