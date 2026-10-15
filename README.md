# wsl_usb_auto

在 WSL 裡執行的 USB 自動掛載管理工具。用 VID:PID 設定「插上就自動掛進 WSL」的規則，並把規則記錄下來，之後可以列出、刪除或一鍵清除。

底層使用 [usbipd-win](https://github.com/dorssel/usbipd-win) 的 `attach --auto-attach`，本工具負責：

- 綁定裝置（`usbipd bind`，會跳出 Windows 的系統管理員確認視窗）
- 為每條規則啟動一個背景監聽程序，裝置插上時自動 attach 進 WSL
- 在 `~/.bashrc` 加一段標記區塊，開啟 WSL 終端機時自動啟動所有監聽
- 把規則記錄在 `~/.config/wsl-usb-auto/rules.json`

## 需求

- Windows 10/11 + WSL2
- Windows 端已安裝 usbipd-win（PowerShell 執行 `winget install usbipd`，裝完重開 WSL）
- WSL 內有 Python 3（不需額外套件）

## 安裝

把腳本放到任意位置即可，例如：

```bash
git clone git@github.com:alianlbj23/wsl_usb_auto.git
cd wsl_usb_auto
python3 wsl_usb_auto.py
```

腳本會記住自己的路徑並寫進 `.bashrc`，之後搬動位置請重新執行一次 `add`，或透過選單重新設定。

## 使用方式

### 互動選單（最簡單）

不加參數直接執行就會出現選單：

```bash
python3 wsl_usb_auto.py
```

```
========== WSL USB 自動掛載 ==========
目前規則：1 條（ESP32）
快捷指令：usb
  1. 新增自動掛載裝置
  2. 刪除規則
  3. 查看所有規則
  4. 查看目前插著的 USB 裝置
  5. 啟動監聽
  6. 停止監聽
  7. 設定快捷指令（不用打完整路徑就能開選單）
  8. 移除快捷指令
  9. 全部清除
  0. 離開
```

選單裡的「設定快捷指令」會在 `.bashrc` 加一個 alias（預設 `usb`），之後打 `usb` 就能開啟選單。

### 指令模式

| 指令 | 說明 |
|------|------|
| `python3 wsl_usb_auto.py scan` | 列出 Windows 目前偵測到的 USB 裝置（找 VID:PID 用） |
| `python3 wsl_usb_auto.py add 10c4:ea60 -n ESP32` | 新增規則。裝置要先插著，會跳出系統管理員確認視窗 |
| `python3 wsl_usb_auto.py list` | 列出所有規則、監聽狀態、裝置是否插著 |
| `python3 wsl_usb_auto.py remove 10c4:ea60` | 刪除規則（停止監聽、detach、unbind） |
| `python3 wsl_usb_auto.py start` / `stop` | 手動啟動 / 停止所有監聽 |
| `python3 wsl_usb_auto.py uninstall` | 刪除全部規則並移除 `.bashrc` 設定區塊 |

## 運作方式

1. `add` 時先用 `usbipd list` 確認裝置目前有插著，再以系統管理員權限執行 `usbipd bind --hardware-id VID:PID`。
2. 規則寫入 `~/.config/wsl-usb-auto/rules.json`，並在 `~/.bashrc` 加入以下區塊：

   ```bash
   # >>> wsl-usb-auto >>>
   if [ -f "/path/to/wsl_usb_auto.py" ]; then
       python3 "/path/to/wsl_usb_auto.py" start --quiet >/dev/null 2>&1
   fi
   # <<< wsl-usb-auto <<<
   ```

3. 每次開 WSL 終端機時，`start --quiet` 會為每條規則啟動一個背景程序：
   `usbipd.exe attach --wsl --hardware-id VID:PID --auto-attach`。已在執行的不會重複啟動。
4. 監聽程序的輸出寫在 `~/.config/wsl-usb-auto/watcher.log`。

## 相關檔案

| 路徑 | 用途 |
|------|------|
| `~/.config/wsl-usb-auto/rules.json` | 規則記錄 |
| `~/.config/wsl-usb-auto/watcher.log` | 監聽程序 log |
| `~/.bashrc` | 自動啟動區塊與 alias（皆以標記包起來，`uninstall` 可乾淨移除） |

## 常見問題

- **找不到 usbipd.exe**：Windows 端尚未安裝 usbipd-win，或安裝後還沒重開 WSL。
- **綁定失敗**：UAC 視窗按了「否」，重新執行一次即可。
- **裝置狀態顯示「未插入」**：規則仍在，只是裝置目前沒接上；插上後監聽程序會自動 attach。
- **解除綁定失敗**：可在系統管理員 PowerShell 手動執行 `usbipd unbind --hardware-id VID:PID`，或 `usbipd unbind --all`。
