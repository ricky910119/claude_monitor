"""Read-only quota collectors. Never send a model prompt or approve a trust dialog."""
from __future__ import annotations

import json
import math
import os
import ctypes
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime

import pyte
import winpty


class UsageError(RuntimeError):
    pass


class RetryableUsageError(UsageError):
    """A transient collector failure that is safe to retry."""


@dataclass
class Quota:
    label: str
    used: float | None
    resets: str = ""
    note: str = ""


def percentage(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise UsageError("解析失敗：額度百分比不是數字")
    if not math.isfinite(value) or value < 0 or value > 100:
        raise UsageError("解析失敗：額度百分比超出 0–100")
    return float(value)


def parse_claude(text):
    """Bound every section, including model-specific limits, before parsing."""
    headings = list(re.finditer(
        r"(?im)^\s*(Current\s+session|Current\s+week(?:\s*\([^\n)]*\))?|"
        r"Extra\s+usage|Usage\s+credits)\s*$", text))
    result = []
    for i, heading in enumerate(headings):
        title = re.sub(r"\s+", " ", heading.group(1)).strip()
        chunk = text[heading.end():headings[i + 1].start() if i + 1 < len(headings) else len(text)]
        match = re.search(r"(\d+(?:\.\d+)?)\s*%\s*(used|left|remaining)\b", chunk, re.I)
        reset = re.search(r"(?im)^\s*(Resets?\b[^\n]+)", chunk)
        label = {"current session": "本次額度", "current week (all models)": "每週（全部模型）",
                 "current week": "每週額度", "extra usage": "額外用量",
                 "usage credits": "用量點數"}.get(title.lower(), title)
        if match:
            pct = percentage(float(match.group(1)))
            if match.group(2).lower() != "used":
                pct = 100 - pct
            result.append(Quota(label, pct, reset.group(1).strip() if reset else ""))
        elif title.lower() in ("extra usage", "usage credits"):
            off = re.search(r"\b(?:off|disabled|not enabled)\b", chunk, re.I)
            result.append(Quota(label, None, note="未啟用" if off else "未提供百分比"))
        else:
            result.append(Quota(label, None, note="未提供百分比"))
    return result


def claude_complete(quotas):
    return any(q.label == "本次額度" and q.used is not None for q in quotas) and any(
        q.label in ("每週（全部模型）", "每週額度") and q.used is not None for q in quotas)


def claude_render_complete(text, quotas):
    """Require an end marker so a partially redrawn usage panel is never committed."""
    if not claude_complete(quotas):
        return False
    return bool(re.search(
        r"(?im)^\s*(?:Extra\s+usage|Usage\s+credits)\s*$|"
        r"^\s*(?:Esc|Escape)\s+to\s+(?:cancel|close|go\s+back)\s*$",
        text,
    ))


def parse_codex(result):
    buckets = result.get("rateLimitsByLimitId")
    if not isinstance(buckets, dict) or not buckets:
        bucket = result.get("rateLimits")
        buckets = {"codex": bucket} if isinstance(bucket, dict) else {}
    quotas = []
    for key, bucket in buckets.items():
        if not isinstance(bucket, dict):
            continue
        for slot in ("primary", "secondary"):
            window = bucket.get(slot)
            if not isinstance(window, dict):
                continue
            minutes = window.get("windowDurationMins")
            if minutes == 10080:
                label = "每週額度"
            elif isinstance(minutes, (int, float)) and minutes > 0:
                label = f"{minutes / 60:g} 小時額度" if minutes % 60 == 0 else f"{minutes:g} 分鐘額度"
            else:
                label = "主要額度" if slot == "primary" else "次要額度"
            if key != "codex":
                label = f"{bucket.get('limitName') or key} · {label}"
            resets = ""
            stamp = window.get("resetsAt")
            if stamp is not None:
                try:
                    resets = datetime.fromtimestamp(stamp).astimezone().strftime("重設 %m/%d %H:%M %Z")
                except (ValueError, TypeError, OSError, OverflowError):
                    raise UsageError("解析失敗：Codex 重設時間無效") from None
            # The structured interface already returns USED percent. Do not invert.
            quotas.append(Quota(label, percentage(window.get("usedPercent")), resets))
    if not quotas:
        raise UsageError("未提供訂閱額度：請確認 Codex 使用 ChatGPT 登入")
    return quotas


def require_executable(path, agent):
    if not Path(path).is_file():
        raise UsageError(f"啟動失敗：找不到 {agent} 執行檔，請檢查路徑設定")


def fetch_codex(path, cwd, stop, timeout=40):
    """Local stdio app-server: initialize + quota read only, no thread/turn/MCP startup."""
    require_executable(path, "Codex")
    try:
        process = subprocess.Popen(
            [path, "app-server"], cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except OSError as exc:
        raise UsageError(f"啟動失敗：Codex ({type(exc).__name__})") from None
    messages = queue.Queue()

    def reader():
        try:
            for line in process.stdout:
                try:
                    messages.put(json.loads(line))
                except ValueError:
                    continue
        finally:
            messages.put(None)

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    deadline = time.monotonic() + timeout

    def send(message):
        process.stdin.write(json.dumps(message) + "\n")
        process.stdin.flush()

    def receive(request_id):
        while not stop.is_set() and time.monotonic() < deadline:
            try:
                message = messages.get(timeout=0.2)
            except queue.Empty:
                continue
            if message is None:
                raise UsageError("啟動失敗：Codex 額度介面提前結束")
            if message.get("id") != request_id:
                continue
            if "error" in message:
                error = message["error"]
                detail = str(error.get("message", "")).lower()
                if any(word in detail for word in ("auth", "login", "sign in", "401")):
                    raise UsageError("未登入或登入已過期：請執行 codex login")
                if "chatgpt" in detail or "api key" in detail:
                    raise UsageError("未提供訂閱額度：請以 ChatGPT 登入 Codex")
                raise UsageError(f"抓取失敗：Codex 額度介面錯誤 {error.get('code', 'unknown')}")
            return message.get("result", {})
        raise UsageError("已取消" if stop.is_set() else "逾時：Codex 額度介面未回覆")

    try:
        send({"id": 1, "method": "initialize", "params": {
            "clientInfo": {"name": "claude_monitor", "version": "0.2.0"}}})
        receive(1)
        send({"method": "initialized", "params": {}})
        send({"id": 2, "method": "account/rateLimits/read"})
        return parse_codex(receive(2))
    finally:
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)
        thread.join(timeout=1)
        process.stdin.close()
        process.stdout.close()


def fetch_claude(path, cwd, stop, timeout=55):
    """Give the PTY a hidden console host even when the HUD runs under pythonw."""
    python = Path(sys.executable).with_name("python.exe")
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = subprocess.SW_HIDE
    process = subprocess.Popen(
        [str(python), str(Path(__file__).resolve()), "--claude-worker", path, cwd, str(timeout)],
        cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL, text=True, encoding="utf-8",
        creationflags=subprocess.CREATE_NO_WINDOW, startupinfo=startup)
    deadline = time.monotonic() + timeout + 10
    try:
        while True:
            if stop.is_set():
                raise UsageError("已取消")
            if time.monotonic() > deadline:
                raise UsageError("逾時：Claude 背景抓取未完成")
            try:
                output, _ = process.communicate(timeout=0.2)
                break
            except subprocess.TimeoutExpired:
                continue
        try:
            result = json.loads(output)
        except ValueError:
            raise UsageError("啟動失敗：Claude 背景程序未提供結果") from None
        if "error" in result:
            raise UsageError(result["error"])
        return [Quota(**row) for row in result["quotas"]]
    finally:
        if process.poll() is None:
            # The worker owns its PTY; allow its bounded collection to close it.
            try:
                process.communicate(timeout=max(1, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
        process.stdout.close()


def _spawn_claude_pty(command, cwd, env):
    """Spawn Claude without allowing Windows loader failures to open a dialog."""
    previous_error_mode = None
    if os.name == "nt":
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.SetErrorMode.argtypes = [ctypes.c_uint]
        kernel32.SetErrorMode.restype = ctypes.c_uint
        # Child processes inherit the parent's error mode. This turns loader
        # failures such as 0xC0000142 into an exit code instead of a modal box.
        flags = 0x0001 | 0x0002 | 0x8000  # FAILCRITICALERRORS | NOGPFAULTERRORBOX | NOOPENFILEERRORBOX
        previous_error_mode = kernel32.SetErrorMode(flags)
    try:
        return winpty.PtyProcess.spawn(command, cwd=cwd, dimensions=(70, 160),
                                       env=env, backend=winpty.enums.Backend.ConPTY)
    finally:
        if previous_error_mode is not None:
            kernel32.SetErrorMode(previous_error_mode)


def _claude_exit_error(text, exit_code):
    """Preserve the real early-exit reason instead of reporting every exit as login failure."""
    unsigned_code = None if exit_code is None else exit_code & 0xFFFFFFFF
    if unsigned_code == 0xC0000142:
        return RetryableUsageError(
            "啟動失敗：Windows 無法初始化 Claude CLI（0xC0000142）")

    low = text.lower()
    if "unable to connect" in low or "connectionrefused" in low or "failed to connect" in low:
        return RetryableUsageError("連線失敗：Claude CLI 無法連線至 Anthropic 服務")
    if "option" in low and "argument missing" in low:
        return UsageError("啟動失敗：Claude CLI 隔離參數格式不相容")

    code = "unknown" if unsigned_code is None else f"0x{unsigned_code:08X}"
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    detail = " / ".join(lines[-3:])[:300]
    suffix = f"；{detail}" if detail else ""
    return RetryableUsageError(f"啟動失敗：Claude CLI 提前結束（{code}）{suffix}")


def _fetch_claude_pty_once(path, cwd, stop, timeout):
    require_executable(path, "Claude")
    # Safe mode disables hooks/plugins/custom commands. Strict MCP config excludes
    # configured MCP services. A quota command must never become a model prompt.
    command = subprocess.list2cmdline([
        path, "--safe-mode", "--strict-mcp-config",
        "--tools", ""])
    try:
        env = os.environ.copy()
        # pywinpty treats backend=0 as falsy, so also remove the env override.
        env.pop("PYWINPTY_BACKEND", None)
        process = _spawn_claude_pty(command, cwd, env)
    except Exception as exc:
        raise UsageError(f"啟動失敗：Claude PTY ({type(exc).__name__})") from None
    screen = pyte.Screen(160, 70)
    stream = pyte.Stream(screen)
    lock = threading.Lock()
    finished = threading.Event()

    def reader():
        try:
            while not stop.is_set():
                chunk = process.read(4096)
                if not chunk:
                    break
                with lock:
                    stream.feed(chunk)
        except Exception:
            pass
        finally:
            finished.set()

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    deadline = time.monotonic() + timeout
    sent = False
    previous = ""
    stable_since = time.monotonic()
    last_quotas = []
    last_parse_error = None
    try:
        while not stop.is_set() and time.monotonic() < deadline:
            with lock:
                text = "\n".join(line.rstrip() for line in screen.display)
            low = text.lower()
            if any(s in low for s in ("trust this folder", "trust this directory", "trust the files")):
                raise UsageError("需手動確認：請在此資料夾執行 claude，完成信任提示")
            if any(s in low for s in ("please run /login", "not logged in", "login expired", "select login method", "sign in to your account")):
                raise UsageError("未登入或登入已過期：請在 Claude 執行 /login")
            if "unknown option" in low or "unknown argument" in low:
                raise UsageError("啟動失敗：Claude 版本不支援監控隔離參數，請更新 CLI")
            if text != previous:
                stable_since = time.monotonic()
                previous = text
            if not sent:
                # Both legacy and current Claude prompts; do not require NBSP.
                if re.search(r"(?m)^\s*[❯›>]\s*(?:$|Try\b|Ask\b)", text) and time.monotonic() - stable_since > 0.4:
                    process.write("/usage")
                    time.sleep(0.2)
                    process.write("\r")
                    sent = True
            else:
                settled_for = time.monotonic() - stable_since
                # Claude redraws the panel in several terminal frames. Parsing a
                # frame mid-redraw can temporarily join old and new digits and
                # produce an impossible percentage. Ignore those transient frames.
                if settled_for >= 0.6:
                    try:
                        candidate = parse_claude(text)
                    except UsageError as exc:
                        last_parse_error = exc
                    else:
                        last_parse_error = None
                        last_quotas = candidate
                        if claude_render_complete(text, candidate) and settled_for >= 1.5:
                            return candidate
            if finished.is_set():
                try:
                    exit_code = process.exitstatus
                except Exception:
                    exit_code = None
                raise _claude_exit_error(text, exit_code)
            stop.wait(0.1)
        if stop.is_set():
            raise UsageError("已取消")
        if not sent:
            raise UsageError("逾時：Claude 未進入輸入畫面，請先手動完成啟動畫面")
        if last_parse_error is not None:
            raise UsageError(f"解析失敗：Claude /usage 穩定後仍無法解析（{last_parse_error}）")
        if last_quotas:
            raise UsageError("解析失敗：Claude 額度畫面不完整")
        raise UsageError("逾時：Claude /usage 未提供可辨識的額度")
    finally:
        try:
            process.close(force=True)
        except Exception:
            pass
        thread.join(timeout=1)


def _fetch_claude_pty(path, cwd, stop, timeout=55):
    """Retry only transient Claude startup failures within the original deadline."""
    deadline = time.monotonic() + timeout
    retry_delays = (2, 5)
    last_error = None
    attempts = 0
    for attempt in range(len(retry_delays) + 1):
        attempts = attempt + 1
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            return _fetch_claude_pty_once(path, cwd, stop, remaining)
        except RetryableUsageError as exc:
            last_error = exc
            if attempt >= len(retry_delays):
                break
            delay = min(retry_delays[attempt], max(0, deadline - time.monotonic()))
            if delay <= 0:
                break
            if stop.wait(delay):
                raise UsageError("已取消") from None
    if last_error is not None:
        raise UsageError(f"{last_error}（已重試 {attempts} 次）") from None
    raise UsageError("逾時：Claude 背景抓取未完成")


if __name__ == "__main__":
    if len(sys.argv) == 5 and sys.argv[1] == "--claude-worker":
        try:
            quotas = _fetch_claude_pty(sys.argv[2], sys.argv[3], threading.Event(), float(sys.argv[4]))
            print(json.dumps({"quotas": [vars(q) for q in quotas]}, ensure_ascii=True))
        except Exception as exc:
            detail = str(exc) if isinstance(exc, UsageError) else type(exc).__name__
            print(json.dumps({"error": detail}, ensure_ascii=True))
