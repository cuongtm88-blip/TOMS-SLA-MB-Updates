"""Small Zalo Bot API client for private staff alerts."""
from __future__ import annotations

import html
import re
import threading

import requests


API_ROOT = "https://bot-api.zaloplatforms.com/bot"
_UPDATES_LOCK = threading.Lock()


def _api(token, method):
    return f"{API_ROOT}{token}/{method}"


def _response_payload(response, token=""):
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError(f"Zalo trả về phản hồi không phải JSON (HTTP {response.status_code})") from exc
    if not response.ok or not payload.get("ok", payload.get("error") == 0):
        description = payload.get("description") or payload.get("message") or f"HTTP {response.status_code}"
        if token:
            description = str(description).replace(token, "[đã ẩn token]")
        raise RuntimeError(f"Zalo Bot API: {description}")
    return payload.get("result", payload.get("data", payload))


def fetch_updates(token, timeout=5, session=requests):
    """Get updates; serialize polling so UI actions cannot race the listener."""
    if not str(token or "").strip():
        raise ValueError("Hãy nhập Zalo Bot token trước.")
    token = str(token).strip()
    try:
        with _UPDATES_LOCK:
            response = session.post(
                _api(token, "getUpdates"),
                json={"timeout": max(0, min(int(timeout), 10))},
                timeout=max(15, int(timeout) + 5),
            )
    except requests.RequestException:
        raise RuntimeError("Không kết nối được Zalo Bot API; hãy kiểm tra mạng và thử lại.") from None
    result = _response_payload(response, token)
    if isinstance(result, dict):
        return result.get("updates") or result.get("result") or [result]
    return result if isinstance(result, list) else []


def update_message(update):
    if not isinstance(update, dict):
        return {}
    message = update.get("message") or update.get("edited_message") or update
    return message if isinstance(message, dict) else {}


def private_message_details(update):
    """Return (chat_id, text, message_time_ms, update_key) for private messages."""
    message = update_message(update)
    chat = message.get("chat") or {}
    if not isinstance(chat, dict):
        chat = {}
    chat_type = str(chat.get("type") or chat.get("chat_type") or "").casefold()
    if chat_type not in {"private", "user"}:
        return None
    sender = message.get("from") or message.get("sender") or {}
    if not isinstance(sender, dict):
        sender = {}
    chat_id = chat.get("id") or chat.get("chat_id") or sender.get("id") or message.get("chat_id")
    if chat_id is None or not str(chat_id).strip():
        return None
    stamp = message.get("date") or message.get("timestamp") or update.get("date") or update.get("timestamp")
    try:
        stamp = int(stamp)
        if stamp < 100_000_000_000:
            stamp *= 1000
    except (TypeError, ValueError):
        stamp = None
    key = update.get("update_id") or message.get("message_id") or message.get("id")
    return str(chat_id).strip(), str(message.get("text") or message.get("message") or ""), stamp, str(key or "")


_REGISTRATION_SUFFIX = re.compile(r"\s*(?:_|\s)+\s*đăng\s*ký\s+nhận\s+cảnh\s+báo\s*[.!]?\s*$", re.I)


def registration_name(text):
    """Extract a name only from the explicit private registration phrase."""
    match = _REGISTRATION_SUFFIX.search(str(text or ""))
    if not match:
        return ""
    return str(text)[:match.start()].strip(" _\t\r\n.-")


def fetch_private_chats(token, session=requests):
    """Return recent private chats seen by the bot, newest first."""
    updates = fetch_updates(token, session=session)
    return private_chats_from_updates(updates)


def private_chats_from_updates(updates):
    """Extract private chat identities from an already-retrieved update batch."""
    chats = {}
    for update in reversed(updates):
        details = private_message_details(update)
        if details is None:
            continue
        chat_id, _text, _stamp, _key = details
        message = update_message(update)
        chat = message.get("chat") or {}
        if not isinstance(chat, dict):
            chat = {}
        sender = message.get("from") or message.get("sender") or {}
        if not isinstance(sender, dict):
            sender = {}
        name = str(
            sender.get("display_name") or sender.get("name") or chat.get("name")
            or " ".join(str(sender.get(k, "")).strip() for k in ("first_name", "last_name")).strip()
            or "Người dùng Zalo"
        ).strip()
        chats.setdefault(chat_id, {"id": chat_id, "name": name})
    return list(chats.values())


def send_message(token, chat_id, text, session=requests):
    if not str(token or "").strip():
        raise ValueError("Thiếu Zalo Bot token.")
    if not str(chat_id or "").strip():
        raise ValueError("Thiếu Zalo Chat ID.")
    text = str(text or "")
    # Ticket builders use Telegram HTML; send readable plain text to Zalo.
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</(?:p|div|li|b|strong|i|em|u)>\s*", "\n", text, flags=re.I)
    text = re.sub(r"<(?:b|strong|i|em|u|p|div|li)[^>]*>", "", text, flags=re.I)
    text = html.unescape(re.sub(r"<[^>]+>", "", text)).strip()
    if not text:
        raise ValueError("Nội dung cảnh báo đang trống.")
    if len(text) > 2000:
        raise ValueError("Nội dung gửi Zalo vượt quá giới hạn 2.000 ký tự.")
    token = str(token).strip()
    try:
        response = session.post(
            _api(token, "sendMessage"),
            json={"chat_id": str(chat_id).strip(), "text": text},
            timeout=20,
        )
    except requests.RequestException:
        raise RuntimeError("Không kết nối được Zalo Bot API; hãy kiểm tra mạng và thử lại.") from None
    return _response_payload(response, token)
