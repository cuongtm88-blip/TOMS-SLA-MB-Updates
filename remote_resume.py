"""Private Telegram /resume supervisor for restarting the MB workflow."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import remote_login


def resume_command(update, allowed_chat_ids):
    message = update.get("message") or {}
    chat = message.get("chat") or {}
    sender = message.get("from") or {}
    chat_id = str(chat.get("id", ""))
    if (chat.get("type") != "private" or not chat_id
            or chat_id != str(sender.get("id", ""))
            or sender.get("is_bot")
            or chat_id not in set(map(str, allowed_chat_ids))):
        return False
    return bool(re.fullmatch(r"/resume(?:@\w+)?", str(message.get("text", "")).strip(), re.I))


def _read_settings(data_dir):
    try:
        value = json.loads((data_dir / "settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def _chat_ids(settings):
    values = str(settings.get("otp_chat_ids", ""))
    return {part.strip() for part in values.split(",") if re.fullmatch(r"[1-9]\d*", part.strip())}


def _process_running(value):
    try:
        os.kill(int(value), 0)
        return True
    except (TypeError, ValueError, OSError):
        return False


def _active_workflow(data_dir):
    path = data_dir / "remote-workflow.json"
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return False
    if _process_running(state.get("pid")):
        return True
    path.unlink(missing_ok=True)
    return False


def _gui_running(data_dir):
    try:
        pid = (data_dir / "remote-gui.pid").read_text(encoding="ascii").strip()
    except OSError:
        return False
    if _process_running(pid):
        return True
    (data_dir / "remote-gui.pid").unlink(missing_ok=True)
    return False


def _write_resume_request(data_dir):
    target = data_dir / "remote-resume.json"
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps({"requested_at": time.time()}), encoding="utf-8")
    temporary.replace(target)


def _lock_supervisor(path):
    lock_file = path.open("a+b")
    lock_file.seek(0)
    if not lock_file.read(1):
        lock_file.seek(0)
        lock_file.write(b"0")
        lock_file.flush()
    lock_file.seek(0)
    try:
        if sys.platform == "win32":
            import msvcrt
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, OSError):
        lock_file.close()
        return None
    return lock_file


def _send(token, chat_id, text):
    remote_login.telegram_call(token, "sendMessage", {"chat_id": chat_id, "text": text})


def _launch_app():
    if getattr(sys, "frozen", False):
        command = [sys.executable, "--resume"]
    else:
        command = [sys.executable, str(Path(__file__).with_name("app.py")), "--resume"]
    options = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
        "cwd": str(Path(__file__).resolve().parent),
    }
    if sys.platform == "win32":
        options["creationflags"] = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    else:
        options["start_new_session"] = True
    subprocess.Popen(command, **options)


def run_supervisor(data_dir):
    """Watch the configured private Telegram chat and resume only while idle."""
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    lock_file = _lock_supervisor(data_dir / "telegram-resume-supervisor.lock")
    if lock_file is None:
        return
    log_path = data_dir / "remote-resume.log"

    def log(message):
        try:
            with log_path.open("a", encoding="utf-8") as output:
                output.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")
        except OSError:
            pass

    offset = None
    token_seen = ""
    try:
        while True:
            settings = _read_settings(data_dir)
            enabled = bool(settings.get("remote_login_enabled"))
            token = str(settings.get("telegram_token", "")).strip()
            allowed = _chat_ids(settings)
            if not enabled or not token or not allowed:
                time.sleep(5)
                continue
            if token != token_seen:
                # The workflow owns getUpdates while it is handling login/OTP.
                # Defer initialization until it has stopped so this listener
                # cannot consume the OTP message first.
                if _active_workflow(data_dir):
                    time.sleep(2)
                    continue
                try:
                    updates = remote_login.telegram_call(token, "getUpdates", {
                        "timeout": 0, "allowed_updates": ["message"],
                    })
                    offset = max((int(item.get("update_id", -1)) for item in updates), default=-1) + 1
                    token_seen = token
                except Exception as exc:
                    log(f"Không khởi tạo được listener /resume: {exc}")
                    time.sleep(10)
                    continue
            if _active_workflow(data_dir):
                time.sleep(2)
                continue
            payload = {"timeout": 5, "allowed_updates": ["message"]}
            if offset is not None:
                payload["offset"] = offset
            try:
                updates = remote_login.telegram_call(token, "getUpdates", payload)
                # The worker may have started while getUpdates was waiting.
                # Do not advance the update offset in that case: OTP polling
                # belongs exclusively to the active login workflow.
                if _active_workflow(data_dir):
                    time.sleep(2)
                    continue
                for update in updates:
                    try:
                        offset = max(offset or 0, int(update["update_id"]) + 1)
                    except (KeyError, TypeError, ValueError):
                        continue
                    if not resume_command(update, allowed):
                        continue
                    chat_id = str(update["message"]["chat"]["id"])
                    if _active_workflow(data_dir):
                        _send(token, chat_id, "TOMS SLA MB đang chạy; bỏ qua lệnh /resume.")
                    else:
                        if _gui_running(data_dir):
                            _send(token, chat_id, "Đã nhận /resume. TOMS SLA MB sẽ đăng nhập lại OneBSS và chạy quy trình.")
                            _write_resume_request(data_dir)
                        else:
                            _send(token, chat_id, "Đã nhận /resume. TOMS SLA MB đang khởi động lại.")
                            _launch_app()
                        log("Đã nhận lệnh /resume từ Chat ID được cấu hình.")
                        time.sleep(5)
            except Exception as exc:
                log(f"Không đọc được lệnh /resume: {exc}")
                time.sleep(10)
    finally:
        lock_file.close()
