"""Challenge-bound OTP reception. Never log OTPs, credentials or API URLs."""
import re
import time
import uuid
from urllib.parse import urlparse

import requests


class SessionExpired(RuntimeError):
    pass


class LoginError(RuntimeError):
    pass


class InvalidCredentials(LoginError):
    pass


def telegram_call(token, method, payload):
    try:
        response = requests.post(f"https://api.telegram.org/bot{token}/{method}",
                                 json=payload, timeout=(10, 20))
        data = response.json()
    except (requests.RequestException, ValueError):
        raise LoginError("Không kết nối được Telegram cho OTP") from None
    if not response.ok or not data.get("ok"):
        if response.status_code == 409:
            raise LoginError("Bot OTP đang có webhook hoặc ứng dụng khác đọc getUpdates; dùng bot riêng cho MB")
        raise LoginError(f"Telegram từ chối yêu cầu OTP (HTTP {response.status_code})")
    return data["result"]


def extract_otp(update, allowed, challenge, started):
    msg = update.get("message", {})
    chat = msg.get("chat", {})
    sender = msg.get("from", {})
    ident = str(sender.get("id", ""))
    if (chat.get("type") != "private" or ident not in allowed
            or str(chat.get("id", "")) != ident or sender.get("is_bot")
            or msg.get("date", 0) < started):
        return None
    text = msg.get("text", "").strip()
    # Accept the formats users commonly send, with or without this request's
    # challenge. Authorization and freshness are checked above.
    prefix = (
        r"(?:ONEBSS\s+OTP\s*:\s*|"
        r"/otp(?:@\w+)?\s+(?:" + re.escape(challenge) + r"\s+)?|)"
    )
    match = re.fullmatch(prefix + r"(\d{4,8})", text, re.IGNORECASE)
    return match[1] if match else None


def wait_for_otp(token, allowed, stopped, ready=lambda: False, timeout=180):
    allowed = set(map(str, allowed))
    if not allowed or any(not re.fullmatch(r"[1-9]\d*", v) for v in allowed):
        raise LoginError("Hãy cấu hình Chat ID cá nhân được phép gửi OTP")
    webhook = telegram_call(token, "getWebhookInfo", {})
    if webhook.get("url"):
        raise LoginError("Bot có webhook; cần bot riêng cho OTP MB, không tự xóa webhook")
    challenge, started = uuid.uuid4().hex[:8], int(time.time())
    delivered = 0
    for ident in allowed:
        try:
            telegram_call(token, "sendMessage", {"chat_id": ident, "text":
                f"TOMS SLA MB cần OTP OneBSS (hết hạn sau {timeout} giây).\n"
                f"Trả lời riêng cho bot bằng một trong các dạng:\n"
                f"410183 | /otp 410183 | ONEBSS OTP: 410183\n"
                f"Không gửi mật khẩu hoặc OTP vào nhóm."})
            delivered += 1
        except LoginError:
            continue
    if not delivered:
        raise LoginError("Không gửi được yêu cầu OTP; người nhận cần Start bot và kiểm tra Chat ID")
    offset = None
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not stopped():
        if ready():
            return None  # user finished login locally
        payload = {"timeout": 5, "allowed_updates": ["message"], "limit": 100}
        if offset is not None:
            payload["offset"] = offset
        updates = telegram_call(token, "getUpdates", payload)
        for update in updates:
            offset = update["update_id"] + 1
            otp = extract_otp(update, allowed, challenge, started)
            if otp:
                telegram_call(token, "getUpdates", {"offset": offset, "timeout": 0, "limit": 1})
                return otp
    raise LoginError("Đã dừng hoặc hết thời gian chờ OTP")


def visible_one(page, selector):
    fields = page.locator(selector)
    matches = [fields.nth(i) for i in range(fields.count()) if fields.nth(i).is_visible()]
    if len(matches) != 1:
        raise LoginError("Không nhận diện duy nhất ô đăng nhập/OTP; cần kiểm tra giao diện OneBSS")
    return matches[0]


def login(page, username, password, token, allowed, otp_selector, stopped, authenticated, log):
    """Only fill the verified OneBSS login form, not arbitrary redirected pages."""
    try:
        parsed = urlparse(page.url)
        if parsed.scheme != "https" or parsed.hostname != "onebss.vnpt.vn":
            raise LoginError("Không tự nhập tài khoản trên địa chỉ ngoài OneBSS")
        visible_one(page, 'input[placeholder="Tài khoản"]').fill(username)
        visible_one(page, 'input[name="password"][type="password"]').fill(password)
        page.get_by_role("button", name="Đăng nhập", exact=True).click()
        deadline = time.monotonic() + 30
        otp_field = None
        while time.monotonic() < deadline and not stopped():
            if authenticated():
                return
            if page.get_by_text("Tên truy cập hoặc mật khẩu không hợp lệ!", exact=False).count():
                raise InvalidCredentials("OneBSS báo tài khoản hoặc mật khẩu không hợp lệ")
            try:
                otp_field = visible_one(page, otp_selector)
                break
            except LoginError:
                page.wait_for_timeout(500)
        if otp_field is None:
            raise LoginError("Đăng nhập chưa thành công hoặc không tìm thấy ô OTP")
        log("OneBSS yêu cầu OTP; đã bắt đầu xác thực từ xa qua Telegram.")
        otp = wait_for_otp(token, allowed, stopped, authenticated)
        if otp is None:
            return
        # Recheck origin immediately before submitting a secret.
        if urlparse(page.url).hostname != "onebss.vnpt.vn":
            raise LoginError("OneBSS đổi địa chỉ; đã dừng gửi OTP")
        otp_field.fill(otp)
        otp = None
        buttons = page.get_by_role("button", name=re.compile(r"^(Xác nhận|Xác thực|Đăng nhập)$", re.I))
        visible = [buttons.nth(i) for i in range(buttons.count()) if buttons.nth(i).is_visible()]
        if len(visible) != 1:
            raise LoginError("Chưa nhận diện nút xác nhận OTP; hãy xác nhận trong trình duyệt")
        visible[0].click()
        for _ in range(30):
            if stopped():
                raise LoginError("Đã dừng đăng nhập")
            if authenticated():
                return
            page.wait_for_timeout(1000)
        raise LoginError("OneBSS không chấp nhận OTP hoặc chưa hoàn tất đăng nhập")
    except LoginError:
        raise
    except Exception:
        raise LoginError("Lỗi thao tác đăng nhập OneBSS; dữ liệu bí mật không được ghi vào chẩn đoán") from None
