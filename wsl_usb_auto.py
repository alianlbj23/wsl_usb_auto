#!/usr/bin/env python3
"""
WSL USB 自動掛載管理工具（在 WSL 裡執行）

用 VID:PID 設定「插上就自動掛進 WSL」的規則，並記錄所有規則，日後可以列出或刪除。

最簡單的用法：直接執行，會出現選單
  python3 wsl_usb_auto.py

進階（指令模式，也可以不用記）：
  python3 wsl_usb_auto.py scan                        列出 Windows 目前偵測到的 USB 裝置（找 VID:PID 用）
  python3 wsl_usb_auto.py add 10c4:ea60 -n ESP32      新增規則（裝置要先插著；會跳出 Windows 系統管理員確認）
  python3 wsl_usb_auto.py list                        列出所有規則、監聽狀態、是否已掛載
  python3 wsl_usb_auto.py remove 10c4:ea60            刪除規則（停止監聽、卸載、解除綁定）
  python3 wsl_usb_auto.py start | stop                手動啟動 / 停止所有監聽
  python3 wsl_usb_auto.py uninstall                   刪除全部規則並移除 .bashrc 設定

規則記錄在 ~/.config/wsl-usb-auto/rules.json
.bashrc 只會加一段標記區塊，打開 WSL 終端機時自動啟動所有規則的監聽。
"""
import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
from datetime import datetime
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "wsl-usb-auto"
RULES_FILE = CONFIG_DIR / "rules.json"
LOG_FILE = CONFIG_DIR / "watcher.log"
BASHRC = Path.home() / ".bashrc"
SCRIPT = Path(__file__).resolve()

BEGIN = "# >>> wsl-usb-auto >>>"
END = "# <<< wsl-usb-auto <<<"
ID_RE = re.compile(r"^[0-9a-f]{4}:[0-9a-f]{4}$")
LIST_RE = re.compile(r"^\s*(\d+-\d+)\s+([0-9a-fA-F]{4}:[0-9a-fA-F]{4})\s+(.+?)\s{2,}(\S.*?)\s*$")


# ---------- 小工具 ----------
class UserError(Exception):
    pass


def die(msg):
    raise UserError(msg)


def norm_id(s):
    s = s.strip().lower()
    if not ID_RE.match(s):
        die(f"格式錯誤：{s}，應為 VID:PID，例如 10c4:ea60")
    return s


def usbipd():
    exe = shutil.which("usbipd.exe")
    if not exe:
        die("找不到 usbipd.exe。請先在 Windows 安裝 usbipd-win（PowerShell 執行 winget install usbipd），再重開 WSL。")
    return exe


# ---------- 規則記錄 ----------
def load_rules():
    if RULES_FILE.exists():
        return json.loads(RULES_FILE.read_text(encoding="utf-8"))
    return {}


def save_rules(rules):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    RULES_FILE.write_text(json.dumps(rules, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------- Windows 端 ----------
def scan_devices():
    r = subprocess.run([usbipd(), "list"], capture_output=True, text=True, errors="replace")
    devs = []
    for line in r.stdout.splitlines():
        if line.strip().lower().startswith("persisted"):
            break  # 後面是已拔除的裝置清單
        m = LIST_RE.match(line)
        if m:
            devs.append({"busid": m.group(1), "id": m.group(2).lower(),
                         "name": m.group(3).strip(), "state": m.group(4).strip()})
    return devs, r.stdout


def run_admin(commands):
    """用系統管理員權限在 Windows 執行 usbipd 指令（會跳出 UAC 確認視窗）。"""
    inner = "; ".join(commands) + "; exit $LASTEXITCODE"
    ps = (f"$p = Start-Process -FilePath powershell -ArgumentList '-NoProfile','-Command','{inner}' "
          f"-Verb RunAs -WindowStyle Hidden -Wait -PassThru; exit $p.ExitCode")
    print("→ Windows 會跳出系統管理員確認視窗，請按「是」…")
    r = subprocess.run(["powershell.exe", "-NoProfile", "-Command", ps],
                       capture_output=True, text=True, errors="replace")
    return r.returncode == 0


# ---------- 監聽程式 ----------
def watcher_pids(dev_id):
    r = subprocess.run(["pgrep", "-f", f"usbipd.exe attach --wsl --hardware-id {dev_id}"],
                       capture_output=True, text=True)
    return [int(p) for p in r.stdout.split()]


def start_watcher(dev_id):
    if watcher_pids(dev_id):
        return False
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    log = open(LOG_FILE, "a")
    subprocess.Popen([usbipd(), "attach", "--wsl", "--hardware-id", dev_id, "--auto-attach"],
                     stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
    return True


def stop_watcher(dev_id):
    pids = watcher_pids(dev_id)
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    subprocess.run([usbipd(), "detach", "--hardware-id", dev_id],
                   capture_output=True, text=True)
    return bool(pids)


# ---------- .bashrc ----------
def hook_text():
    return (f"{BEGIN}\n"
            f"# 由 wsl_usb_auto.py 自動產生；用「python3 {SCRIPT} uninstall」移除\n"
            f'if [ -f "{SCRIPT}" ]; then\n'
            f'    python3 "{SCRIPT}" start --quiet >/dev/null 2>&1\n'
            f"fi\n"
            f"{END}\n")


def install_hook():
    text = BASHRC.read_text(encoding="utf-8") if BASHRC.exists() else ""
    block = re.compile(re.escape(BEGIN) + r".*?" + re.escape(END) + r"\n?", re.S)
    if block.search(text):
        text = block.sub(hook_text(), text)      # 更新（例如腳本搬了位置）
    else:
        text = text.rstrip("\n") + "\n\n" + hook_text()
    BASHRC.write_text(text, encoding="utf-8")


def remove_hook():
    if not BASHRC.exists():
        return
    text = BASHRC.read_text(encoding="utf-8")
    block = re.compile(r"\n*" + re.escape(BEGIN) + r".*?" + re.escape(END) + r"\n?", re.S)
    BASHRC.write_text(block.sub("\n", text), encoding="utf-8")


# ---------- 快捷指令（alias）----------
ALIAS_BEGIN = "# >>> wsl-usb-auto alias >>>"
ALIAS_END = "# <<< wsl-usb-auto alias <<<"
ALIAS_RE = re.compile(r"\n*" + re.escape(ALIAS_BEGIN) + r".*?" + re.escape(ALIAS_END) + r"\n?", re.S)
NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")


def get_alias():
    if not BASHRC.exists():
        return None
    m = ALIAS_RE.search(BASHRC.read_text(encoding="utf-8"))
    if m:
        n = re.search(r"^alias ([^=\s]+)=", m.group(0), re.M)
        return n.group(1) if n else None
    return None


def set_alias(name):
    if not NAME_RE.match(name):
        die(f"「{name}」不能當指令名稱，只能用英文、數字、底線或減號，且不能以數字開頭")
    text = BASHRC.read_text(encoding="utf-8") if BASHRC.exists() else ""
    text = ALIAS_RE.sub("\n", text).rstrip("\n")
    block = (f"{ALIAS_BEGIN}\n"
             f"alias {name}='python3 \"{SCRIPT}\"'\n"
             f"{ALIAS_END}\n")
    BASHRC.write_text(text + "\n\n" + block, encoding="utf-8")


def remove_alias():
    if not BASHRC.exists():
        return False
    text = BASHRC.read_text(encoding="utf-8")
    new = ALIAS_RE.sub("\n", text)
    BASHRC.write_text(new, encoding="utf-8")
    return new != text


def menu_set_alias():
    current = get_alias()
    if current:
        print(f"目前的快捷指令是：{current}")
    name = ask("想用什麼指令開啟這個選單？（直接 Enter 用「usb」）：") or "usb"
    if name != current and shutil.which(name):
        if ask(f"系統已經有「{name}」這個指令（{shutil.which(name)}），確定要覆蓋嗎？(y/N)：").lower() != "y":
            return
    set_alias(name)
    print(f"✓ 已設定快捷指令：{name}")
    print(f"  開一個新的 WSL 終端機就能用；想在目前這個視窗立刻生效，請執行：source ~/.bashrc")


def menu_remove_alias():
    current = get_alias()
    if not current:
        print("目前沒有設定快捷指令")
        return
    if ask(f"確定移除快捷指令「{current}」？(y/N)：").lower() == "y":
        remove_alias()
        print(f"✓ 已移除快捷指令：{current}")
        print(f"  新開的終端機就不會有了；目前這個視窗可執行 unalias {current} 立刻取消")


# ---------- 指令 ----------
def cmd_scan(_):
    devs, raw = scan_devices()
    if not devs:
        print(raw or "沒有偵測到 USB 裝置")
        return
    rules = load_rules()
    print(f"{'VID:PID':<11} {'狀態':<14} {'規則':<4} 名稱")
    for d in devs:
        mark = "✓" if d["id"] in rules else ""
        print(f"{d['id']:<11} {d['state']:<14} {mark:<4} {d['name']}")


def cmd_add(a):
    add_rule(a.id, a.name)


def add_rule(dev_id, name=None):
    dev_id = norm_id(dev_id)
    devs, _ = scan_devices()
    found = [d for d in devs if d["id"] == dev_id]
    if not found:
        die(f"目前沒有偵測到 {dev_id}。綁定時裝置要插著，請插上後再執行一次（用 scan 查看目前的裝置）。")

    if not run_admin([f"usbipd bind --hardware-id {dev_id}"]):
        die("綁定失敗（可能按了「否」）。請再試一次。")

    rules = load_rules()
    rules[dev_id] = {
        "name": name or found[0]["name"],
        "added": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    save_rules(rules)
    install_hook()
    start_watcher(dev_id)
    print(f"✓ 已新增規則：{dev_id}（{rules[dev_id]['name']}）")
    print("  之後只要打開 WSL 終端機，插上這個裝置就會自動掛進來。")


def cmd_remove(a):
    remove_rule(a.id)


def remove_rule(dev_id):
    dev_id = norm_id(dev_id)
    rules = load_rules()
    if dev_id not in rules:
        die(f"沒有 {dev_id} 這條規則（用 list 查看）")
    stop_watcher(dev_id)
    if not run_admin([f"usbipd unbind --hardware-id {dev_id}"]):
        print(f"! 解除綁定失敗，之後可在系統管理員 PowerShell 手動執行：usbipd unbind --hardware-id {dev_id}")
    name = rules.pop(dev_id)["name"]
    save_rules(rules)
    if not rules:
        remove_hook()
        print("  已沒有任何規則，.bashrc 的設定區塊也一併移除。")
    print(f"✓ 已刪除規則：{dev_id}（{name}）")


def cmd_list(_):
    rules = load_rules()
    if not rules:
        print("目前沒有任何規則。用 add 新增，例如：python3 wsl_usb_auto.py add 10c4:ea60 -n ESP32")
        return
    devs, _ = scan_devices()
    state = {d["id"]: d["state"] for d in devs}
    print(f"{'VID:PID':<11} {'監聽':<6} {'裝置狀態':<14} {'新增時間':<17} 名稱")
    for dev_id, r in rules.items():
        watching = "執行中" if watcher_pids(dev_id) else "未啟動"
        print(f"{dev_id:<11} {watching:<6} {state.get(dev_id, '未插入'):<14} {r['added']:<17} {r['name']}")
    print(f"\n規則檔：{RULES_FILE}")


def cmd_start(a):
    rules = load_rules()
    for dev_id in rules:
        started = start_watcher(dev_id)
        if not a.quiet:
            print(f"{dev_id}：{'已啟動監聽' if started else '監聽已在執行'}")
    if not rules and not a.quiet:
        print("沒有任何規則")


def cmd_stop(_):
    for dev_id in load_rules():
        stopped = stop_watcher(dev_id)
        print(f"{dev_id}：{'已停止' if stopped else '原本就沒有在執行'}")


def cmd_uninstall(_):
    rules = load_rules()
    for dev_id in rules:
        stop_watcher(dev_id)
    if rules and not run_admin([f"usbipd unbind --hardware-id {i}" for i in rules]):
        print("! 解除綁定失敗，可在系統管理員 PowerShell 手動執行：usbipd unbind --all")
    save_rules({})
    remove_hook()
    print(f"✓ 已刪除 {len(rules)} 條規則，並移除 .bashrc 設定")


# ---------- 互動選單 ----------
def ask(prompt):
    try:
        return input(prompt).strip()
    except EOFError:
        return "0"


def pick(items, label):
    """列出 items（字串清單）讓使用者選號碼，回傳索引或 None。"""
    for i, s in enumerate(items, 1):
        print(f"  {i}. {s}")
    print("  0. 返回")
    ans = ask(f"選擇{label}：")
    if ans.isdigit() and 1 <= int(ans) <= len(items):
        return int(ans) - 1
    return None


def menu_add():
    devs, _ = scan_devices()
    rules = load_rules()
    choices = [d for d in devs if d["id"] not in rules]
    print("\n目前插著、還沒設定規則的裝置：")
    labels = [f"{d['id']}  {d['name']}" for d in choices] + ["手動輸入 VID:PID"]
    idx = pick(labels, "要自動掛載的裝置")
    if idx is None:
        return
    if idx == len(choices):
        dev_id = ask("輸入 VID:PID（例如 10c4:ea60）：")
        default = ""
    else:
        dev_id, default = choices[idx]["id"], choices[idx]["name"]
    name = ask(f"幫它取個名字（直接 Enter 用「{default or dev_id}」）：") or default or None
    add_rule(dev_id, name)


def menu_remove():
    rules = load_rules()
    if not rules:
        print("目前沒有任何規則")
        return
    ids = list(rules)
    idx = pick([f"{i}  {rules[i]['name']}" for i in ids], "要刪除的規則")
    if idx is None:
        return
    if ask(f"確定刪除 {ids[idx]}（{rules[ids[idx]]['name']}）？(y/N)：").lower() == "y":
        remove_rule(ids[idx])


def menu_uninstall():
    if ask("確定刪除「全部」規則並移除 .bashrc 設定？(y/N)：").lower() == "y":
        cmd_uninstall(None)
        current = get_alias()
        if current and ask(f"快捷指令「{current}」也要一起移除嗎？(y/N)：").lower() == "y":
            remove_alias()
            print(f"✓ 已移除快捷指令：{current}")


def menu():
    actions = [
        ("新增自動掛載裝置", menu_add),
        ("刪除規則", menu_remove),
        ("查看所有規則", lambda: cmd_list(None)),
        ("查看目前插著的 USB 裝置", lambda: cmd_scan(None)),
        ("啟動監聽", lambda: cmd_start(argparse.Namespace(quiet=False))),
        ("停止監聽", lambda: cmd_stop(None)),
        ("設定快捷指令（不用打完整路徑就能開選單）", menu_set_alias),
        ("移除快捷指令", menu_remove_alias),
        ("全部清除", menu_uninstall),
    ]
    while True:
        rules = load_rules()
        alias = get_alias()
        print("\n========== WSL USB 自動掛載 ==========")
        print(f"目前規則：{len(rules)} 條" + ("" if not rules else "（" + "、".join(r["name"] for r in rules.values()) + "）"))
        print(f"快捷指令：{alias if alias else '未設定'}")
        for i, (label, _) in enumerate(actions, 1):
            print(f"  {i}. {label}")
        print("  0. 離開")
        ans = ask("請選擇：")
        if ans == "0":
            break
        if ans.isdigit() and 1 <= int(ans) <= len(actions):
            print()
            try:
                actions[int(ans) - 1][1]()
            except UserError as e:
                print("✗", e)
            ask("\n按 Enter 回到選單…")
        else:
            print("請輸入選單上的數字")


def main():
    ap = argparse.ArgumentParser(description="WSL USB 自動掛載管理工具（不加參數直接執行會開啟選單）")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("scan", help="列出 Windows 目前偵測到的 USB 裝置").set_defaults(func=cmd_scan)
    p = sub.add_parser("add", help="新增自動掛載規則")
    p.add_argument("id", help="VID:PID，例如 10c4:ea60")
    p.add_argument("-n", "--name", help="自訂名稱，例如 ESP32")
    p.set_defaults(func=cmd_add)
    p = sub.add_parser("remove", help="刪除規則")
    p.add_argument("id")
    p.set_defaults(func=cmd_remove)
    sub.add_parser("list", help="列出所有規則").set_defaults(func=cmd_list)
    p = sub.add_parser("start", help="啟動所有監聽")
    p.add_argument("--quiet", action="store_true")
    p.set_defaults(func=cmd_start)
    sub.add_parser("stop", help="停止所有監聽").set_defaults(func=cmd_stop)
    sub.add_parser("uninstall", help="刪除全部規則與 .bashrc 設定").set_defaults(func=cmd_uninstall)
    a = ap.parse_args()
    try:
        if a.cmd is None:
            menu()
        else:
            a.func(a)
    except UserError as e:
        print("✗", e)
        sys.exit(1)
    except KeyboardInterrupt:
        print()


if __name__ == "__main__":
    main()