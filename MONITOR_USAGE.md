# Claude + Codex 用量監控

兩個來源在同一行顯示，不需要切頁，也不顯示用量條。深灰底搭配暖色 Claude／灰綠色 Codex 標籤。
提亮的莫蘭迪色系集中在 `claude_monitor.py` 頂端的 `COLORS`。每項百分比獨立換色：≤40% 鼠尾草綠 `#A8C2B5`、>40% 且 ≤80% 灰褐金 `#DDBE8F`、>80% 煙粉紅 `#D99393`。額度名稱（本次、每週、5h 等）採柔米淺黃 `#E8DFCF`；抓取失敗的舊資料統一淡化為 `#8C95A6`，成功更新後恢復門檻色。
每三分鐘各自刷新，手動更新不會重複啟動同一來源。
百分比一律代表「已使用」。更新失敗時保留上次成功資料並標示舊資料與更新時間。

## 啟動

目前使用專用環境 `.venv_claude_monitor`。唯一的 `claude_monitor.vbs` 同時提供每日監督、立即啟動與停止三種模式：

- 直接雙擊或傳入 `daily`：常駐監督，07:00–19:00 確保 HUD 執行，其餘時間停止 HUD。
- 傳入 `start`：立即啟動一次 HUD。
- 傳入 `stop`：停止 HUD，並通知正在執行的每日 supervisor 結束。

工作排程器使用：

```text
wscript.exe //B //Nologo "C:\path\to\claude_monitor\claude_monitor.vbs" daily
```

手動模式可在命令提示字元使用：

```text
wscript.exe "C:\path\to\claude_monitor\claude_monitor.vbs" start
wscript.exe "C:\path\to\claude_monitor\claude_monitor.vbs" stop
```

安裝依賴時使用同一環境：

```powershell
$Repo = 'C:\path\to\claude_monitor'
$Python = Join-Path (Split-Path $Repo -Parent) '.venv_claude_monitor\Scripts\python.exe'
& $Python -m pip install -r (Join-Path $Repo 'requirements.txt')
```

修改程式後須關閉舊版監控視窗，再重新啟動 VBS；已執行的 Python 不會自動載入修改。

## 登入與讀取方式

- Claude 使用目前使用者 `.local\bin\claude.exe`；safe mode 停用自訂 hooks/plugins 等設定，strict MCP 模式不載入設定中的 MCP。只輸入 `/usage`，不送出模型提示，也不自動確認信任或登入。
- 首次使用須在本專案目錄手動啟動 Claude，完成登入與信任畫面後退出。
- Codex 使用目前使用者 `AppData\Local\Programs\OpenAI\Codex\bin\codex.exe`，啟動本機 stdio `app-server`，只呼叫初始化與 `account/rateLimits/read`。不建立模型會話，也不啟動遠端開發 server。
- 可透過 `CLAUDE_MONITOR_CLAUDE_PATH`、`CLAUDE_MONITOR_CODEX_PATH` 環境變數指定其他原生執行檔位置。

Claude 需支援 `--safe-mode`、`--strict-mcp-config` 等參數。Codex 需支援 `account/rateLimits/read`。
訂閱額度要求對應的訂閱登入；只有 API Key 的帳號可能不提供這些額度。

## 畫面與時間

- Claude 顯示 CLI 實際提供的本次／每週／模型額度及用量點數狀態；重設時間保留 CLI 原文與時區，不推測日期。
- Codex 根據介面回傳的額度視窗長度標示，例如 5 小時與每週；使用 `usedPercent` 原值，重設時間從 Unix 秒轉換為本機時區。
- 缺少額度不是 0%；會顯示未提供、未啟用或明確錯誤。
- 預設固定在 `DISPLAY2` 工作區左下角，距邊緣 30 像素；螢幕不存在時退回主螢幕。
- 滑鼠停在來源或額度上可查看最後更新時間、重設時間及錯誤原因；百分比均為已用比例。
- 點「固定」解除後可拖曳；右鍵可回到第 2 螢幕左下角。採用 97% 不透明以提高小字可讀性，保持置頂。
- `↻` 更新兩個來源，`×` 關閉。黃色狀態點表示更新中，`!` 表示抓取失敗；舊資料會淡化，原因放在提示框。

## 診斷

```powershell
Set-Location 'C:\path\to\claude_monitor'
& '..\.venv_claude_monitor\Scripts\python.exe' claude_monitor.py --diagnose all
```

可以改成 `--diagnose claude` 或 `--diagnose codex`。診斷會實際查詢額度，輸出資料或錯誤，完成後關閉自己建立的 CLI。
GUI 的 `monitor_diagnostics.log` 僅記錄成功視窗數與錯誤分類，不記錄完整終端畫面、帳號或憑證。檔案最多 100 KB，保留一份輪替檔。

離線解析檢查（不連網、不啟動 CLI）：

```powershell
& '..\.venv_claude_monitor\Scripts\python.exe' -m unittest -v test_usage_sources
```

Codex 額度介面：[官方 App Server 文件](https://learn.chatgpt.com/docs/app-server)。
