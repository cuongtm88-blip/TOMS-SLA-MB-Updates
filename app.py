"""ATS TXL - automated OneBSS export and Telegram reporting."""
from __future__ import annotations

import json
import calendar
import html
import os
import platform
import re
import socket
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import requests

try:
    from playwright._impl._errors import TargetClosedError
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - friendly message at runtime
    sync_playwright = None
    PlaywrightTimeoutError = RuntimeError
    TargetClosedError = RuntimeError

import TXL_Monitor_Tele_Group_All_Over10 as txl
import credential_store
import github_diagnostics
import updater
import duty_roster
import onebss_grid
import remote_login
import service_catalog
import service_ui
import zalo_bot
from config import APP_DATA_ENV, APP_NAME, APP_SLUG, SERVICE_CATALOG_API_URL, TELEGRAM_CHAT_ENV, TELEGRAM_TOKEN_ENV
from version import APP_VERSION, DIAGNOSTICS_REPOSITORY, UPDATE_CHECK_INTERVAL_SECONDS


ROOT = Path(__file__).resolve().parent


def _app_data_dir():
    override = os.getenv(APP_DATA_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32":
        base = Path(os.getenv("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.getenv("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / APP_SLUG


APP_DATA = _app_data_dir()
SETTINGS_PATH = APP_DATA / "settings.json"
IN_PROGRESS_ALERT_STATE_PATH = APP_DATA / "in_progress_alert_state.json"
STORAGE_STATE_PATH = APP_DATA / "onebss-storage-state.json"
if getattr(sys, "frozen", False):
    PROFILE = APP_DATA / "chrome-profile"
    DOWNLOADS = Path.home() / "Downloads" / APP_SLUG
else:
    PROFILE = ROOT / "chrome-profile"
    DOWNLOADS = ROOT / "downloads"
ONEBSS_URL = "https://onebss.vnpt.vn/"
INCIDENT_INVENTORY_URL = ONEBSS_URL + "#/htkh/ManagementIncidentInventory?tag=2"
MAX_EXCEL_RECOVERY_ATTEMPTS = 1
DIAGNOSTICS_DIRNAME = "diagnostics"
DIAGNOSTICS_CREDENTIAL_SERVICE = f"{APP_SLUG}/github-diagnostics"
ONEBSS_CREDENTIAL_SERVICE = f"{APP_SLUG}/onebss-login"
ZALO_CREDENTIAL_SERVICE = f"{APP_SLUG}/zalo-bot"
ROSTER_PATH = APP_DATA / "duty_rosters.json"
CONTACTS_PATH = APP_DATA / "duty_contacts.json"
ZALO_CONTACTS_PATH = APP_DATA / "duty_zalo_contacts.json"
SEARCH_SCOPES = (
    (("Phòng HTKH Miền Bắc (VIP 1)", "Phòng HTKH Miền Nam (VIP 2)",
      "Phòng HTKH Miền Trung (VIP 3)"),
     ("Tập trung", "Miền Bắc", "Miền Trung", "Miền Nam")),
)
MB_PROVINCES = {
    duty_roster.normalize(name) for name in (
        "Bắc Giang", "Bắc Kạn", "Bắc Ninh", "Cao Bằng", "Điện Biên",
        "Hà Giang", "Hà Nam", "Hà Nội", "Hải Dương", "Hải Phòng",
        "Hòa Bình", "Hưng Yên", "Lai Châu", "Lạng Sơn", "Lào Cai",
        "Nam Định", "Ninh Bình", "Phú Thọ", "Quảng Ninh", "Sơn La",
        "Thanh Hóa", "Thái Bình", "Thái Nguyên", "Tuyên Quang", "Vĩnh Phúc",
        "Yên Bái", "Nghệ An", "Hà Tĩnh",
    )
}


def split_mb_ticket_scopes(frame, contacts):
    """Split tickets into MB staff and remaining tickets installed in MB."""
    required = {"ten_nv", "tentinh", "diachi_ld"}
    missing = required - set(frame.columns)
    if missing:
        raise RuntimeError("Bảng OneBSS thiếu cột phân loại MB: " + ", ".join(sorted(missing)))
    if not contacts:
        raise RuntimeError("Chưa import danh sách tên nhân sự và ID chat Telegram để lọc phiếu MB")

    contact_names = {duty_roster.normalize(name) for name in contacts}
    staff_mask = frame["ten_nv"].fillna("").map(duty_roster.normalize).isin(contact_names)

    def is_mb_province(value):
        place = duty_roster.normalize(value)
        place = re.sub(r"^(?:tỉnh|thành phố|tp\.?|thị xã)\s+", "", place)
        return place in MB_PROVINCES

    def is_mb_installation(row):
        if is_mb_province(row.get("tentinh", "")):
            return True
        address = duty_roster.normalize(row.get("diachi_ld", ""))
        return any(province in address for province in MB_PROVINCES)

    location_mask = frame.apply(is_mb_installation, axis=1)
    mb_staff = frame[staff_mask].copy()
    mb_installation = frame[location_mask & ~staff_mask].copy()
    return mb_staff.reset_index(drop=True), mb_installation.reset_index(drop=True)


def _zalo_duty_recipients(rosters, contacts, staff_ids=None):
    people = duty_roster.on_duty(rosters)
    if staff_ids is not None:
        allowed = set(map(str, staff_ids))
        people = [person for person in people if person["staff_id"] in allowed]
    if not people:
        raise ValueError("Chưa có nhân sự thuộc nhãn đã chọn trong ca trực hiện tại")
    missing = [p["name"] for p in people if not contacts.get(duty_roster.normalize(p["name"]))]
    if missing:
        raise ValueError("Thiếu Zalo Chat ID: " + ", ".join(missing))
    return list(dict.fromkeys(contacts[duty_roster.normalize(p["name"])] for p in people))


def _load_settings():
    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _save_settings(data):
    APP_DATA.mkdir(parents=True, exist_ok=True)
    temporary = SETTINGS_PATH.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    try:
        temporary.chmod(0o600)
    except OSError:
        pass
    os.replace(temporary, SETTINGS_PATH)
    try:
        SETTINGS_PATH.chmod(0o600)
    except OSError:
        pass


def _load_in_progress_alert_state():
    """Read the latest successfully delivered milestone for each ticket."""
    try:
        data = json.loads(IN_PROGRESS_ALERT_STATE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _in_progress_alert_key(row):
    return "\x1f".join((str(row.get("ma_bh", "")).strip(), str(row.get("ngay_bh", "")).strip()))


def _get_new_in_progress_alerts(frame, recipient=None):
    """Keep only tickets that reached an unsent 60-minute alert milestone."""
    state = _load_in_progress_alert_state()
    indexes = []
    milestones = {}
    for index, row in frame.iterrows():
        key = _in_progress_alert_key(row)
        if recipient is not None:
            key = str(recipient) + "\x1e" + key
        current = int(row["alert_round"])
        try:
            previous = int(state.get(key, 0))
        except (TypeError, ValueError):
            previous = 0
        if current > previous:
            indexes.append(index)
            milestones[key] = current
    return frame.loc[indexes].copy(), milestones


def _record_in_progress_alerts(milestones):
    """Persist milestones only after Telegram accepts the alert message."""
    if not milestones:
        return
    state = _load_in_progress_alert_state()
    state.update(milestones)
    APP_DATA.mkdir(parents=True, exist_ok=True)
    temporary = IN_PROGRESS_ALERT_STATE_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, IN_PROGRESS_ALERT_STATE_PATH)


def _parse_recipient_chat_ids(value):
    """Return unique Telegram chat IDs from comma/space/newline-separated text."""
    if isinstance(value, (list, tuple)):
        parts = [str(item).strip() for item in value]
    else:
        parts = re.split(r"[,;\s]+", str(value or "").strip())

    recipients = []
    for part in parts:
        if not part:
            continue
        if not re.fullmatch(r"-?\d+", part):
            raise ValueError(f'Chat ID không hợp lệ: "{part}"')
        if part not in recipients:
            recipients.append(part)
    return recipients


def _load_recipient_chat_ids(value):
    try:
        return _parse_recipient_chat_ids(value)
    except ValueError:
        return []


def _parse_repeat_minutes(value):
    try:
        minutes = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return minutes if 1 <= minutes <= 10080 else None


class ATSApp(tk.Tk):
    def __init__(self):
        super().__init__()
        saved = _load_settings()
        self.title(f"{APP_NAME} - OneBSS → Telegram / Zalo")
        self.geometry("980x700")
        self.minsize(860, 600)
        self.worker = None
        self.stop_requested = False
        self.auto_repeat = bool(saved.get("schedule_enabled", True))
        self.schedule_enabled = tk.BooleanVar(value=self.auto_repeat)
        saved_repeat_minutes = _parse_repeat_minutes(saved.get("repeat_minutes", 5)) or 5
        self.repeat_seconds = saved_repeat_minutes * 60
        self.repeat_minutes = tk.StringVar(value=str(saved_repeat_minutes))
        self.keep_awake_enabled = tk.BooleanVar(
            value=bool(saved.get("keep_awake_enabled", False))
        )
        self.keep_awake_active = False
        self.keep_awake_process = None
        self.telegram_token = tk.StringVar(
            value=os.getenv(TELEGRAM_TOKEN_ENV, "") or saved.get("telegram_token", "")
        )
        self.telegram_chat_id = tk.StringVar(
            value=os.getenv(TELEGRAM_CHAT_ENV, "") or saved.get("telegram_chat_id", "")
        )
        self.zalo_token = tk.StringVar(value=credential_store.load_secret(
            ZALO_CREDENTIAL_SERVICE, "bot-token"
        ))
        self._zalo_token_for_alerts = self.zalo_token.get().strip()
        self._zalo_registration_stop = threading.Event()
        self._zalo_registration_thread = None
        self._zalo_registration_seen = set()
        self._zalo_registration_pending = []
        self._zalo_registration_started_at_ms = 0
        saved_recipients = _load_recipient_chat_ids(saved.get("error_recipient_chat_ids", []))
        self.error_recipient_chat_ids = tk.StringVar(value=", ".join(saved_recipients))
        self.github_diagnostics_token = tk.StringVar(
            value=os.getenv("TOMS_SLA_GITHUB_DIAGNOSTICS_TOKEN", "")
            or credential_store.load_secret(
                DIAGNOSTICS_CREDENTIAL_SERVICE, DIAGNOSTICS_REPOSITORY
            )
        )
        self._error_recipient_ids = saved_recipients
        self._telegram_token_for_alerts = self.telegram_token.get().strip()
        self.current_stage = "Khởi tạo ứng dụng"
        self.browser_context = None
        self.browser_page = None
        self.browser_backend = "playwright-chromium"
        self._active_export_diagnostic = None
        self._diagnostic_context_ids = set()
        self._diagnostic_page_ids = set()
        self._last_diagnostic_path = None
        self.start_event = None
        self.update_in_progress = False
        self.macos_preview = sys.platform == "darwin"
        self.mb_extended = True
        self.destination = tk.StringVar(value=saved.get("alert_destination", "Group"))
        self.alert_channel = tk.StringVar(value=saved.get("alert_channel", "Telegram"))
        self._alert_channel = self.alert_channel.get()
        self.onebss_user = tk.StringVar(value=saved.get("onebss_user", ""))
        self.onebss_password = tk.StringVar(value="")
        self.remote_enabled = tk.BooleanVar(value=saved.get("remote_login_enabled", False))
        self.otp_chat_ids = tk.StringVar(value=saved.get("otp_chat_ids", ""))
        self.otp_selector = tk.StringVar(value=saved.get("otp_selector", 'input[placeholder="Mã OTP"]'))
        self.service_preferences = {key: saved[key] for key in ("service_filter_mode", "selected_service_ids", "selected_service_label_ids", "selected_staff_label_ids") if key in saved}
        self._remote_config = None
        self._alert_destination = self.destination.get()
        self.roster_month = tk.StringVar(value=time.strftime("%Y-%m"))
        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        if self._zalo_token_for_alerts:
            self._start_zalo_registration_listener(self._zalo_token_for_alerts)
        if updater.can_self_update():
            self.after(2500, self._automatic_update_tick)
    def _build_ui(self):
        pad = {"padx": 12, "pady": 8}
        header = ttk.Frame(self)
        header.pack(fill="x", padx=12, pady=(8, 0))
        ttk.Label(header, text=APP_NAME, font=("Arial", 18, "bold")).pack(side="left")
        self.update_btn = ttk.Button(header, text="Kiểm tra cập nhật", command=lambda: self._start_update_check(silent=False))
        self.update_btn.pack(side="right")
        ttk.Label(header, text=f"Phiên bản {APP_VERSION}").pack(side="right", padx=(0, 10))
        ttk.Label(self, text=f"Tự động xuất phiếu {APP_NAME} từ OneBSS và gửi cảnh báo Telegram/Zalo").pack(anchor="w", padx=12)

        box = ttk.LabelFrame(self, text="Thiết lập")
        box.pack(fill="x", **pad)
        ttk.Label(box, text="Ngày từ (dd/mm/yyyy)").grid(row=0, column=0, sticky="w", **pad)
        self.from_date = tk.StringVar(value=time.strftime("%d/%m/%Y"))
        ttk.Entry(box, textvariable=self.from_date, width=16).grid(row=0, column=1, sticky="w", **pad)
        ttk.Label(box, text="Đến ngày").grid(row=0, column=2, sticky="w", **pad)
        self.to_date = tk.StringVar(value=time.strftime("%d/%m/%Y"))
        ttk.Entry(box, textvariable=self.to_date, width=16).grid(row=0, column=3, sticky="w", **pad)
        ttk.Label(box, text="Telegram Bot token").grid(row=1, column=0, sticky="w", **pad)
        ttk.Entry(box, textvariable=self.telegram_token, show="*", width=28).grid(row=1, column=1, sticky="ew", **pad)
        ttk.Label(box, text="Group chat ID").grid(row=1, column=2, sticky="w", **pad)
        ttk.Entry(box, textvariable=self.telegram_chat_id, width=20).grid(row=1, column=3, sticky="ew", **pad)
        ttk.Label(box, text="Chat ID nhận cảnh báo lỗi").grid(row=2, column=0, sticky="w", **pad)
        ttk.Entry(box, textvariable=self.error_recipient_chat_ids).grid(
            row=2, column=1, sticky="ew", **pad
        )
        self.fetch_chat_btn = ttk.Button(
            box,
            text="Lấy Chat ID",
            command=self._fetch_private_chats,
        )
        self.fetch_chat_btn.grid(row=2, column=2, sticky="ew", **pad)
        ttk.Button(box, text="Gửi thử", command=self._test_error_recipients).grid(
            row=2, column=3, sticky="ew", **pad
        )
        ttk.Label(
            box,
            text="Có thể nhập nhiều Chat ID, cách nhau bằng dấu phẩy. Để trống nếu không nhận cảnh báo lỗi.",
        ).grid(row=3, column=0, columnspan=4, sticky="w", padx=12, pady=(0, 4))
        ttk.Label(box, text="GitHub token chẩn đoán").grid(row=4, column=0, sticky="w", **pad)
        ttk.Entry(box, textvariable=self.github_diagnostics_token, show="*").grid(row=4, column=1, columnspan=2, sticky="ew", **pad)
        self.github_token_btn = ttk.Button(box, text="Lưu và kiểm tra", command=self._save_github_token)
        self.github_token_btn.grid(row=4, column=3, sticky="ew", **pad)
        ttk.Label(box, text=f"Gói lỗi tự tải lên repository private: {DIAGNOSTICS_REPOSITORY}").grid(row=5, column=0, columnspan=4, sticky="w", padx=12, pady=(0, 4))
        ttk.Label(box, text=f"Cấu hình Telegram được lưu riêng trên máy này: {SETTINGS_PATH}").grid(row=6, column=0, columnspan=4, sticky="w", padx=12, pady=(0, 8))
        ttk.Label(box, text="Zalo Bot token").grid(row=7, column=0, sticky="w", **pad)
        ttk.Entry(box, textvariable=self.zalo_token, show="*", width=42).grid(row=7, column=1, columnspan=3, sticky="ew", **pad)
        box.columnconfigure(1, weight=1)
        box.columnconfigure(2, weight=1)

        if self.mb_extended:
            preview = ttk.Frame(self)
            preview.pack(fill="x", padx=12)
            ttk.Label(
                preview,
                text=("MB — đọc bảng OneBSS, không tải Excel" if self.macos_preview
                      else "MB — lọc nhân sự/địa chỉ, cảnh báo theo nhóm")
            ).pack(side="left")
            ttk.Button(preview, text="OneBSS / OTP / Lịch trực", command=self._preview_settings).pack(side="right")
            ttk.Combobox(preview, textvariable=self.destination, values=("Group", "Nhân sự trong ca", "Cả hai"), state="readonly", width=18).pack(side="right", padx=8)
            ttk.Label(preview, text="Kênh cảnh báo:").pack(side="right")
            ttk.Combobox(preview, textvariable=self.alert_channel, values=("Telegram", "Zalo", "Cả hai"), state="readonly", width=11).pack(side="right", padx=8)

        actions = ttk.Frame(self)
        actions.pack(fill="x", **pad)
        self.start_btn = ttk.Button(actions, text="1. Đăng nhập OneBSS", command=self.open_browser)
        self.start_btn.pack(side="left", padx=4)
        self.run_btn = ttk.Button(
            actions,
            text="2. Cấu hình và chạy",
            command=self.run_workflow,
            state="disabled",
        )
        self.run_btn.pack(side="left", padx=4)
        ttk.Button(actions, text="Dịch vụ", command=self._open_service_settings).pack(side="left", padx=4)
        ttk.Button(actions, text="Dừng", command=self.request_stop).pack(side="left", padx=4)
        ttk.Checkbutton(actions, text="Tự động cảnh báo sau", variable=self.schedule_enabled).pack(side="left", padx=(12, 4))
        ttk.Spinbox(actions, from_=1, to=10080, textvariable=self.repeat_minutes, width=6).pack(side="left")
        ttk.Label(actions, text="phút").pack(side="left", padx=(4, 0))
        ttk.Checkbutton(
            actions,
            text="Giữ máy thức khi chạy",
            variable=self.keep_awake_enabled,
            command=self._on_keep_awake_changed,
        ).pack(side="left", padx=(12, 0))

        self.progress = ttk.Progressbar(self, mode="indeterminate")
        self.progress.pack(fill="x", padx=12, pady=(0, 8))
        log_frame = ttk.LabelFrame(self, text="Nhật ký")
        log_frame.pack(fill="both", expand=True, **pad)
        self.log = tk.Text(log_frame, state="disabled", wrap="word")
        self.log.pack(fill="both", expand=True, padx=6, pady=6)
        self.write_log("Sẵn sàng. Hãy mở Chrome và đăng nhập OneBSS.")

    def _prepare_preview_config(self):
        try:
            self._alert_destination = self.destination.get()
            self._alert_channel = self.alert_channel.get()
            if self._alert_destination not in ("Group", "Nhân sự trong ca", "Cả hai"):
                raise ValueError("Chọn nơi nhận cảnh báo")
            if self._alert_channel not in ("Telegram", "Zalo", "Cả hai"):
                raise ValueError("Chọn kênh cảnh báo Telegram, Zalo hoặc Cả hai")
            need_telegram_staff = self._alert_channel in ("Telegram", "Cả hai") and self._alert_destination in ("Nhân sự trong ca", "Cả hai")
            need_zalo_staff = self._alert_channel in ("Zalo", "Cả hai")
            selected_staff_ids = None
            if need_telegram_staff or need_zalo_staff:
                selected_labels = self.service_preferences.get("selected_staff_label_ids", [])
                if selected_labels:
                    catalog = service_catalog.ServiceCatalog(SERVICE_CATALOG_API_URL).get()
                    known_labels = {str(item["staff_label_id"]) for item in catalog.get("staff_labels", [])}
                    if not set(map(str, selected_labels)).issubset(known_labels):
                        raise ValueError("Danh mục nhãn nhân sự chưa được đồng bộ. Hãy cập nhật Apps Script và kiểm tra lại nút Dịch vụ.")
                    selected_staff_ids = service_catalog.staff_ids_for_labels(catalog, selected_labels)
                if need_telegram_staff:
                    duty_roster.recipients(
                        duty_roster.load(ROSTER_PATH), duty_roster.load(CONTACTS_PATH),
                        staff_ids=selected_staff_ids,
                    )
                elif self._alert_channel == "Telegram":
                    duty_roster.recipients(duty_roster.load(ROSTER_PATH), duty_roster.load(CONTACTS_PATH))
                if need_zalo_staff:
                    _zalo_duty_recipients(
                        duty_roster.load(ROSTER_PATH), duty_roster.load(ZALO_CONTACTS_PATH),
                        staff_ids=selected_staff_ids,
                    )
            user = self.onebss_user.get().strip()
            password = self.onebss_password.get()
            allowed = _parse_recipient_chat_ids(self.otp_chat_ids.get())
            if self.remote_enabled.get():
                if not user or not allowed or any(v.startswith("-") or v == "0" for v in allowed):
                    raise ValueError("Đăng nhập từ xa cần user và Chat ID cá nhân nhận OTP")
                if password:
                    credential_store.save_secret(ONEBSS_CREDENTIAL_SERVICE, user, password)
                    self.onebss_password.set("")
                password = password or credential_store.load_secret(ONEBSS_CREDENTIAL_SERVICE, user)
                if not password:
                    raise ValueError("Hãy nhập mật khẩu OneBSS (lưu trong Keychain)")
                if not self.telegram_token.get().strip() or not self.otp_selector.get().strip():
                    raise ValueError("Thiếu Telegram Bot token hoặc selector ô OTP")
                self._remote_config = (user, password, allowed, self.otp_selector.get().strip())
            else:
                self._remote_config = None
            settings = _load_settings()
            settings.update(onebss_user=user, remote_login_enabled=self.remote_enabled.get(),
                            otp_chat_ids=self.otp_chat_ids.get(), otp_selector=self.otp_selector.get(),
                            alert_destination=self._alert_destination,
                            alert_channel=self._alert_channel)
            zalo_token = self.zalo_token.get().strip()
            if zalo_token:
                credential_store.save_secret(ZALO_CREDENTIAL_SERVICE, "bot-token", zalo_token)
            self._zalo_token_for_alerts = zalo_token
            if zalo_token:
                self._start_zalo_registration_listener(zalo_token)
            _save_settings(settings)
            return True
        except (ValueError, OSError, subprocess.SubprocessError) as exc:
            # Keychain command errors can contain the supplied secret in argv.
            detail = str(exc) if isinstance(exc, ValueError) else "Không lưu được vào Keychain/cấu hình"
            messagebox.showerror("Thiết lập MB", detail)
            return False

    def _preview_settings(self):
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("Thiết lập", "Hãy dừng quy trình trước khi thay đổi tài khoản hoặc lịch trực.")
            return
        window = tk.Toplevel(self)
        window.title("MB — OneBSS, OTP và lịch trực")
        window.geometry(f"{min(1200, self.winfo_screenwidth() - 80)}x{min(720, self.winfo_screenheight() - 100)}")
        auth = ttk.LabelFrame(window, text="Đăng nhập lại OneBSS — không lưu mật khẩu/OTP trong chẩn đoán")
        auth.pack(fill="x", padx=10, pady=8)
        ttk.Checkbutton(auth, text="Tự động đăng nhập lại qua OTP Telegram", variable=self.remote_enabled).grid(row=0, column=0, columnspan=4, sticky="w")
        for index, (label, var, masked) in enumerate((
            ("User OneBSS", self.onebss_user, False),
            ("Mật khẩu (để trống dùng Keychain)", self.onebss_password, True),
            ("Chat ID cá nhân được phép gửi OTP", self.otp_chat_ids, False),
            ("Selector OTP (chỉ đổi khi OneBSS đổi UI)", self.otp_selector, False),
        ), 1):
            ttk.Label(auth, text=label).grid(row=index, column=0, sticky="w", padx=6, pady=3)
            ttk.Entry(auth, textvariable=var, show="*" if masked else "", width=65).grid(row=index, column=1, columnspan=3, sticky="ew", padx=6)
        ttk.Button(auth, text="Lưu thiết lập", command=self._prepare_preview_config).grid(row=5, column=3, pady=5)
        auth.columnconfigure(1, weight=1)
        tools = ttk.Frame(window)
        tools.pack(fill="x", padx=10, pady=5)
        ttk.Label(tools, text="Tháng YYYY-MM").pack(side="left")
        ttk.Entry(tools, textvariable=self.roster_month, width=10).pack(side="left", padx=5)
        table = ttk.Frame(window)
        table.pack(fill="both", expand=True, padx=10)
        table.columnconfigure(1, weight=1)
        table.rowconfigure(0, weight=1)
        style = ttk.Style(window)
        style.configure("DutyRoster.Treeview", rowheight=30)
        names = ttk.Treeview(table, columns=("telegram", "zalo"), show="tree headings", selectmode="browse", style="DutyRoster.Treeview", height=12)
        names.heading("#0", text="Tên nhân sự (cố định)")
        names.column("#0", width=220, minwidth=180, stretch=False)
        names.heading("telegram", text="Telegram Chat ID")
        names.column("telegram", width=145, minwidth=145, stretch=False, anchor="center")
        names.heading("zalo", text="Zalo Chat ID")
        names.column("zalo", width=180, minwidth=150, stretch=False, anchor="center")
        names.grid(row=0, column=0, sticky="ns")
        tree = ttk.Treeview(table, show="headings", selectmode="browse", style="DutyRoster.Treeview", height=12)
        tree.grid(row=0, column=1, sticky="nsew")
        for widget in (names, tree):
            widget.tag_configure("even", background="#f0f4f8")
            widget.tag_configure("odd", background="#ffffff")
        scrollbar = ttk.Scrollbar(table, orient="vertical", command=lambda *args: (names.yview(*args), tree.yview(*args)))
        scrollbar.grid(row=0, column=2, sticky="ns")
        horizontal = ttk.Scrollbar(table, orient="horizontal", command=tree.xview)
        horizontal.grid(row=1, column=1, sticky="ew")
        tree.configure(xscrollcommand=horizontal.set)

        def sync_scroll(other, first, last):
            scrollbar.set(first, last)
            if abs(other.yview()[0] - float(first)) > 0.0001:
                other.yview_moveto(first)

        names.configure(yscrollcommand=lambda first, last: sync_scroll(tree, first, last))
        tree.configure(yscrollcommand=lambda first, last: sync_scroll(names, first, last))
        selected_person = tk.StringVar(value="Chưa chọn nhân sự")
        selected_day = tk.StringVar(value="1")
        new_shift = tk.StringVar(value="HC")
        active_person = [None]

        def month_key():
            value = self.roster_month.get().strip()
            if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", value):
                raise ValueError("Tháng phải có dạng YYYY-MM")
            txl.datetime.strptime(value, "%Y-%m")
            return value

        displayed_month = [None]
        def refresh():
            try:
                key = month_key()
                data = duty_roster.load(ROSTER_PATH).get(key, {})
                contacts = duty_roster.load(CONTACTS_PATH)
                zalo_contacts = duty_roster.load(ZALO_CONTACTS_PATH)
                tree.delete(*tree.get_children())
                names.delete(*names.get_children())
                displayed_month[0] = key
                active_person[0] = None
                selected_person.set("Chưa chọn nhân sự")
                year, month = map(int, key.split("-"))
                days = range(1, calendar.monthrange(year, month)[1] + 1)
                tree.configure(columns=[str(day) for day in days])
                day_picker.configure(values=list(days))
                selected_day.set("1")
                weekdays = ("T2", "T3", "T4", "T5", "T6", "T7", "CN")
                for day in days:
                    weekday = weekdays[txl.datetime(year, month, day).weekday()]
                    tree.heading(str(day), text=f"{day:02d} {weekday}")
                    tree.column(str(day), width=62, minwidth=62, stretch=False, anchor="center")
                for index, (ident, person) in enumerate(data.get("people", {}).items()):
                    tag = "even" if index % 2 == 0 else "odd"
                    chat_id = contacts.get(duty_roster.normalize(person["name"]), "Chưa có ID")
                    zalo_id = zalo_contacts.get(duty_roster.normalize(person["name"]), "Chưa có ID")
                    names.insert("", "end", iid=ident, text=person["name"], values=(chat_id, zalo_id), tags=(tag,))
                    tree.insert("", "end", iid=ident, values=[person["shifts"].get(str(day)) or "–" for day in days], tags=(tag,))
                if not data.get("people"):
                    selected_person.set("Chưa có lịch tháng này — hãy import lịch Excel")
            except ValueError:
                messagebox.showerror("Lịch trực", "Nhập tháng dạng YYYY-MM, ví dụ 2026-10", parent=window)

        def import_file(kind):
            path = filedialog.askopenfilename(parent=window, filetypes=[("Excel", "*.xlsx")])
            if not path:
                return
            try:
                if kind == "contacts":
                    duty_roster.save(CONTACTS_PATH, duty_roster.read_contacts(path))
                    # Preserve locally linked IDs and import an optional Zalo column
                    # from the same personnel workbook when present.
                    imported_zalo = duty_roster.read_zalo_contacts(path)
                    if imported_zalo:
                        merged_zalo = duty_roster.load(ZALO_CONTACTS_PATH)
                        merged_zalo.update(imported_zalo)
                        duty_roster.save(ZALO_CONTACTS_PATH, merged_zalo)
                else:
                    key = month_key()
                    year, month = map(int, key.split("-"))
                    rosters = duty_roster.load(ROSTER_PATH)
                    if key in rosters and not messagebox.askyesno("Nhập lại lịch", "Thay thế lịch tháng này và các chỉnh sửa đã lưu?", parent=window):
                        return
                    rosters[key] = duty_roster.read_roster(path, year, month)
                    duty_roster.save(ROSTER_PATH, rosters)
                    if self._zalo_registration_pending:
                        pending, self._zalo_registration_pending = self._zalo_registration_pending, []
                        for pending_name, pending_chat_id in pending:
                            threading.Thread(
                                target=self._register_zalo_staff,
                                args=(pending_name, pending_chat_id), daemon=True,
                            ).start()
                refresh()
            except Exception as exc:
                messagebox.showerror("Import lịch/Telegram", str(exc), parent=window)

        ttk.Button(tools, text="Import lịch Excel", command=lambda: import_file("roster")).pack(side="left", padx=5)
        ttk.Button(tools, text="Import ID Telegram", command=lambda: import_file("contacts")).pack(side="left", padx=5)
        def link_zalo_to_selected():
            ident = active_person[0]
            if not ident or not names.exists(ident):
                messagebox.showinfo("Liên kết Zalo", "Hãy chọn nhân sự trong bảng trước.", parent=window)
                return
            self._fetch_zalo_chats_for_staff(names.item(ident, "text"), window, refresh)

        ttk.Button(tools, text="Gán ID Zalo cho nhân sự chọn", command=link_zalo_to_selected).pack(side="left", padx=5)
        ttk.Button(tools, text="Xem tháng", command=refresh).pack(side="left", padx=5)
        editor = ttk.Frame(window)
        editor.pack(fill="x", padx=10, pady=8)
        ttk.Label(editor, textvariable=selected_person).pack(side="left", padx=(0, 10))
        ttk.Label(editor, text="Ngày:").pack(side="left")
        day_picker = ttk.Combobox(editor, textvariable=selected_day, state="readonly", width=3)
        day_picker.pack(side="left", padx=5)
        ttk.Label(editor, text="Ca:").pack(side="left")
        ttk.Combobox(editor, textvariable=new_shift, values=("HC", "Đêm", "Nghỉ"), state="readonly", width=10).pack(side="left", padx=5)

        def show_current_shift():
            ident = active_person[0]
            if ident and tree.exists(ident):
                current = tree.set(ident, selected_day.get())
                new_shift.set("Nghỉ" if current == "–" else current)

        def select_person(widget):
            selection = widget.selection()
            if not selection:
                return
            ident = selection[0]
            active_person[0] = ident
            selected_person.set(names.item(ident, "text"))
            for other in (names, tree):
                if other.selection() != (ident,):
                    other.selection_set(ident)
            show_current_shift()

        def select_cell(event):
            ident = tree.identify_row(event.y)
            column = tree.identify_column(event.x)
            if ident and column and column != "#0":
                selected_day.set(tree["columns"][int(column[1:]) - 1])
                tree.selection_set(ident)
                select_person(tree)

        names.bind("<<TreeviewSelect>>", lambda event: select_person(names))
        tree.bind("<<TreeviewSelect>>", lambda event: select_person(tree))
        tree.bind("<ButtonRelease-1>", select_cell)
        day_picker.bind("<<ComboboxSelected>>", lambda event: show_current_shift())

        def edit_shift():
            try:
                key = month_key()
                if key != displayed_month[0]:
                    raise ValueError("Bấm Xem tháng trước khi chọn dòng để đổi ca")
                if not active_person[0]:
                    raise ValueError("Hãy chọn ô ca trực hoặc tên nhân sự và ngày")
                rosters = duty_roster.load(ROSTER_PATH)
                ident, day = active_person[0], selected_day.get()
                rosters[key]["people"][ident]["shifts"][day] = "" if new_shift.get() == "Nghỉ" else new_shift.get()
                duty_roster.save(ROSTER_PATH, rosters)
                tree.set(ident, day, "–" if new_shift.get() == "Nghỉ" else new_shift.get())
            except (ValueError, KeyError, OSError) as exc:
                messagebox.showerror("Đổi ca", str(exc), parent=window)

        ttk.Button(editor, text="Lưu đổi ca", command=edit_shift).pack(side="left", padx=5)
        ttk.Label(window, text="Bấm ô để đổi ca. HC 08:00–17:00; Đêm 17:00–08:00 hôm sau; –: Nghỉ. Kéo thanh ngang để xem các ngày.").pack(pady=4)
        refresh()

    def _reauthenticate(self, page):
        delay = 60
        rejected_credentials = 0
        while not self.stop_requested:
            if not self._onebss_session_expired(page) and page.locator("text=Trang chủ").count():
                self._save_browser_storage_state(page.context)
                return
            if not self._remote_config:
                self.write_log("Phiên hết hạn: chờ đăng nhập/OTP trong trình duyệt; sẽ tự tiếp tục.")
                while not self.stop_requested:
                    if not self._onebss_session_expired(page) and page.locator("text=Trang chủ").count():
                        self._save_browser_storage_state(page.context)
                        return
                    page.wait_for_timeout(1000)
                break
            user, password, allowed, selector = self._remote_config
            try:
                page.goto(ONEBSS_URL + "#/auth/login", wait_until="domcontentloaded")
                switch = page.get_by_role("button", name="Dùng tài khoản khác", exact=True)
                if switch.count() and switch.is_visible():
                    switch.click()
                page.locator('input[placeholder="Tài khoản"]').wait_for(timeout=15000)
                remote_login.login(page, user, password, self._telegram_token_for_alerts, allowed,
                                   selector, lambda: self.stop_requested,
                                   lambda: not self._onebss_session_expired(page) and bool(page.locator("text=Trang chủ").count()),
                                   self.write_log)
                self._save_browser_storage_state(page.context)
                self.write_log("Đăng nhập lại thành công; tiếp tục quy trình.")
                return
            except Exception as exc:
                if isinstance(exc, remote_login.LoginError):
                    self.write_log(str(exc))
                if isinstance(exc, remote_login.InvalidCredentials):
                    rejected_credentials += 1
                    if rejected_credentials >= 3:
                        self.write_log("Mật khẩu bị từ chối 3 lần: tạm ngừng tự nhập để tránh khóa tài khoản; chờ đăng nhập trực tiếp.")
                        self._remote_config = None
                        continue
                self.write_log(f"Đăng nhập lại chưa thành công; thử lại sau {delay} giây. Có thể đăng nhập trực tiếp hoặc bấm Dừng.")
                for _ in range(delay):
                    if self.stop_requested:
                        break
                    if not self._onebss_session_expired(page) and page.locator("text=Trang chủ").count():
                        self._save_browser_storage_state(page.context)
                        return
                    page.wait_for_timeout(1000)
                delay = min(delay * 2, 300)
        raise remote_login.LoginError("Đã dừng đăng nhập OneBSS")

    def _read_two_scopes(self, page):
        frames = []
        today = time.strftime("%d/%m/%Y")
        self.after(0, lambda: (self.from_date.set(today), self.to_date.set(today)))
        scope_count = len(SEARCH_SCOPES)
        for number, (units, provinces) in enumerate(SEARCH_SCOPES, 1):
            self._ensure_onebss_session_active(page)
            self._fill_date(page, today, 0)
            self._fill_date(page, today, 1)
            self._ensure_all_statuses(page)
            self._clear_tree_selections(page, 0)
            self._clear_tree_selections(page, 1)
            for tree_index in (0, 1):
                if page.locator(".vue-treeselect").nth(tree_index).locator(".vue-treeselect__multi-value-item").count():
                    raise RuntimeError("Không xóa được bộ lọc trước lượt tìm kiếm; không đọc phạm vi bị lẫn")
            self._expand_tree_parent(page, "Đài HTDV CNTT&DVS", 0)
            for unit in units:
                self._ensure_tree_checked(page, unit, 0)
            for province in provinces:
                self._ensure_tree_checked(page, province, 1)
            self.write_log(f"Tìm kiếm phạm vi {number}/{scope_count}: {', '.join(units)}")
            page.get_by_text("Tìm kiếm", exact=True).click(timeout=15000)
            self._wait_for_search_complete(page)
            frame = onebss_grid.read(page, lambda: self.stop_requested, self.write_log)
            self._ensure_onebss_session_active(page)
            self.write_log(f"Đã đọc đủ {len(frame)} phiếu từ bảng phạm vi {number}/{scope_count}.")
            frames.append(frame)
        result = txl.pd.concat(frames, ignore_index=True).drop_duplicates("ma_bh", keep="last")
        return result.reset_index(drop=True)

    def _send_routed_alerts(self, preprocessing, completion, in_progress, catalog, scope_label=None):
        destinations = []
        channel_mode = getattr(self, "_alert_channel", None)
        if channel_mode not in ("Telegram", "Zalo", "Cả hai"):
            channel_mode = "Telegram"
        use_telegram = channel_mode in ("Telegram", "Cả hai")
        use_zalo = channel_mode in ("Zalo", "Cả hai")
        if use_telegram and self._alert_destination in ("Group", "Cả hai"):
            destinations.append(("telegram", os.environ["TXL_TELEGRAM_GROUP_CHAT_ID"], None))
        need_telegram_staff = use_telegram and self._alert_destination in ("Nhân sự trong ca", "Cả hai")
        need_zalo_staff = use_zalo
        if need_telegram_staff or need_zalo_staff:
            selected_labels = self.service_preferences.get("selected_staff_label_ids", [])
            known_labels = {str(item["staff_label_id"]) for item in catalog.get("staff_labels", [])}
            if selected_labels and not set(map(str, selected_labels)).issubset(known_labels):
                raise RuntimeError("Nhãn nhân sự chưa có trong API Google Sheet. Hãy cập nhật Apps Script rồi mở lại nút Dịch vụ.")
            staff_ids = service_catalog.staff_ids_for_labels(catalog, selected_labels) if selected_labels else None
        if need_telegram_staff:
            ids = duty_roster.recipients(
                duty_roster.load(ROSTER_PATH), duty_roster.load(CONTACTS_PATH),
                staff_ids=staff_ids,
            )
            if selected_labels and not ids:
                self.write_log("Không có nhân sự trong ca thuộc các nhãn đã chọn; bỏ qua tin nhắn riêng.")
            destinations.extend(("telegram", ident, ident) for ident in ids)
        if need_zalo_staff:
            ids = _zalo_duty_recipients(
                duty_roster.load(ROSTER_PATH), duty_roster.load(ZALO_CONTACTS_PATH),
                staff_ids=staff_ids,
            )
            destinations.extend(("zalo", ident, "zalo:" + ident) for ident in ids)
        failures = []
        for channel, ident, state_recipient in destinations:
            for frame, builder in ((preprocessing, txl.build_bh_message), (completion, txl.build_completion_message),
                                   (in_progress, txl.build_in_progress_message)):
                operational = builder is txl.build_in_progress_message
                pending, _ = _get_new_in_progress_alerts(frame, state_recipient) if operational else (frame, {})
                # Small per-ticket batches avoid Telegram's 4096-character limit.
                for index in range(len(pending)):
                    if self.stop_requested:
                        return
                    ticket = pending.iloc[index:index + 1]
                    try:
                        if builder is txl.build_bh_message:
                            ticket = ticket.copy()
                            for field in ("ma_bh", "loaihinh_tb", "ten_nv", "DONVI", "ngay_bh"):
                                ticket[field] = ticket[field].map(lambda v: html.escape(str(v)))
                        message = builder(ticket)
                        if scope_label:
                            message = message.replace(
                                "</b>",
                                "</b>\n<i>Phạm vi: " + html.escape(scope_label) + "</i>",
                                1,
                            )
                        if channel == "zalo":
                            zalo_bot.send_message(self._zalo_token_for_alerts, ident, message)
                        else:
                            txl.send_telegram_message(message, chat_id=ident, bot_token=self._telegram_token_for_alerts)
                        if operational:
                            _, milestone = _get_new_in_progress_alerts(ticket, state_recipient)
                            _record_in_progress_alerts(milestone)
                        for _ in range(31 if channel == "telegram" and ident.startswith("-") else 11):
                            if self.stop_requested:
                                return
                            time.sleep(0.1)
                    except Exception:
                        failures.append(ident)
                        self.write_log(f"Gửi cảnh báo {channel} tới Chat ID {ident} chưa thành công; không ghi nhận mốc chưa gửi.")
                        break
        if failures:
            raise RuntimeError("Có người nhận chưa nhận được cảnh báo. Kiểm tra đúng Chat ID, quyền bot và việc người nhận đã mở cuộc trò chuyện với bot.")
        self.write_log(f"Đã xử lý cảnh báo qua {channel_mode} cho {len(destinations)} nơi nhận; chỉ gửi mốc mới của phiếu đang thực hiện.")

    def write_log(self, text):
        self.after(0, self._append_log, text)

    def report_callback_exception(self, exc_type, exc_value, traceback):
        """Report otherwise-unhandled Tk callback failures to private recipients."""
        error_text = f"{exc_type.__name__}: {exc_value}"
        self.current_stage = "Xử lý giao diện ứng dụng"
        self.write_log(f"LỖI GIAO DIỆN: {error_text}")
        if self._error_recipient_ids and self._telegram_token_for_alerts:
            threading.Thread(
                target=self._send_workflow_error_alert,
                args=(error_text,),
                daemon=True,
            ).start()
        messagebox.showerror("ATS TXL", error_text)

    def _append_log(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", f"{time.strftime('%H:%M:%S')}  {text}\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _save_github_token(self):
        token = self.github_diagnostics_token.get().strip()
        if not token:
            messagebox.showerror("Thiếu GitHub token", "Hãy nhập fine-grained GitHub token.")
            return
        self.github_token_btn.configure(state="disabled")
        self.write_log("Đang kiểm tra quyền tải chẩn đoán lên GitHub...")
        threading.Thread(target=self._save_github_token_worker, args=(token,), daemon=True).start()

    def _save_github_token_worker(self, token):
        try:
            github_diagnostics.verify_access(DIAGNOSTICS_REPOSITORY, token)
            credential_store.save_secret(
                DIAGNOSTICS_CREDENTIAL_SERVICE, DIAGNOSTICS_REPOSITORY, token
            )
            self.write_log("Đã lưu GitHub token an toàn trong kho thông tin xác thực của hệ điều hành.")
            self.after(0, lambda: messagebox.showinfo("GitHub chẩn đoán", "Token hợp lệ và đã được lưu an toàn."))
        except Exception as exc:
            error_text = str(exc)
            self.write_log(f"Không lưu được GitHub token: {error_text}")
            self.after(0, lambda error_text=error_text: messagebox.showerror("GitHub chẩn đoán", error_text))
        finally:
            self.after(0, lambda: self.github_token_btn.configure(state="normal"))

    def _on_close(self):
        """Release the temporary no-sleep request before the UI exits."""
        self._zalo_registration_stop.set()
        self.request_stop()
        self._release_keep_awake()
        self.destroy()

    def _on_keep_awake_changed(self):
        """Persist the choice and apply it immediately during an active session."""
        enabled = bool(self.keep_awake_enabled.get())
        try:
            settings = _load_settings()
            settings["keep_awake_enabled"] = enabled
            _save_settings(settings)
        except OSError as exc:
            self.write_log(f"Không lưu được lựa chọn giữ máy thức: {exc}")

        if self.worker and self.worker.is_alive():
            if enabled:
                self._acquire_keep_awake()
            else:
                self._release_keep_awake()

    def _acquire_keep_awake(self):
        """Keep macOS/Windows awake only while the OneBSS session is active."""
        if not self.keep_awake_enabled.get() or self.keep_awake_active:
            return

        try:
            if sys.platform == "darwin":
                command = ["/usr/bin/caffeinate", "-ims", "-w", str(os.getpid())]
                self.keep_awake_process = subprocess.Popen(
                    command,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
            elif sys.platform == "win32":
                import ctypes

                continuous = 0x80000000
                system_required = 0x00000001
                if not ctypes.windll.kernel32.SetThreadExecutionState(
                    continuous | system_required
                ):
                    raise OSError("Windows không chấp nhận yêu cầu giữ máy thức")
            else:
                self.write_log("Chức năng giữ máy thức chưa hỗ trợ hệ điều hành này.")
                return
        except (OSError, subprocess.SubprocessError) as exc:
            self.write_log(f"Không bật được chế độ giữ máy thức: {exc}")
            return

        self.keep_awake_active = True
        self.write_log("Đã bật giữ máy thức trong lúc phiên OneBSS đang chạy.")

    def _release_keep_awake(self):
        """Release the request when the session stops or the checkbox is cleared."""
        if not self.keep_awake_active:
            return

        if sys.platform == "darwin":
            process = self.keep_awake_process
            self.keep_awake_process = None
            if process and process.poll() is None:
                try:
                    process.terminate()
                except OSError:
                    pass
        elif sys.platform == "win32":
            try:
                import ctypes

                ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
            except OSError:
                pass

        self.keep_awake_active = False
        self.write_log("Đã tắt giữ máy thức.")

    def _automatic_update_tick(self):
        """Check the public release channel at startup and every six hours."""
        self._start_update_check(silent=True)
        self.after(UPDATE_CHECK_INTERVAL_SECONDS * 1000, self._automatic_update_tick)

    def _start_update_check(self, silent=False):
        if self.update_in_progress:
            if not silent:
                self.write_log("Đang kiểm tra hoặc tải bản cập nhật; vui lòng chờ.")
            return
        self.update_in_progress = True
        self.update_btn.configure(state="disabled")
        if not silent:
            self.write_log("Đang kiểm tra bản cập nhật trên GitHub Releases...")
        threading.Thread(
            target=self._update_check_worker,
            args=(silent,),
            daemon=True,
        ).start()

    def _update_check_worker(self, silent):
        try:
            info = updater.get_available_update(APP_VERSION)
            self.after(
                0,
                lambda info=info, silent=silent: self._finish_update_check(
                    info,
                    None,
                    silent,
                ),
            )
        except Exception as exc:
            error_text = str(exc)
            self.after(
                0,
                lambda error_text=error_text, silent=silent: self._finish_update_check(
                    None,
                    error_text,
                    silent,
                ),
            )

    def _finish_update_check(self, info, error_text, silent):
        self.update_in_progress = False
        self.update_btn.configure(state="normal")
        if error_text:
            self.write_log(f"Không kiểm tra được cập nhật: {error_text}")
            if not silent:
                messagebox.showerror(f"Cập nhật {APP_NAME}", error_text)
            return
        if info is None:
            if not silent:
                messagebox.showinfo(
                    f"Cập nhật {APP_NAME}",
                    f"Bạn đang dùng phiên bản mới nhất ({APP_VERSION}).",
                )
            return

        if self.worker and self.worker.is_alive():
            self.write_log(
                f"Có phiên bản {info.version}. Hãy dừng quy trình rồi bấm Kiểm tra cập nhật."
            )
            if not silent:
                messagebox.showinfo(
                    "Có bản cập nhật",
                    f"Phiên bản {info.version} đã sẵn sàng.\n"
                    "Hãy dừng quy trình đang chạy rồi kiểm tra lại để cập nhật an toàn.",
                )
            return

        if not updater.can_self_update():
            self.write_log(
                f"Có phiên bản Windows {info.version}; tự thay EXE chỉ hoạt động trong bản Windows đóng gói."
            )
            if not silent:
                messagebox.showinfo(
                    "Có bản cập nhật Windows",
                    f"Phiên bản {info.version} đã có trên GitHub Releases.\n"
                    "Tính năng tự thay EXE chỉ hoạt động khi chạy EXE đóng gói trên Windows.",
                )
            return

        size_text = self._format_size(info.size)
        notes = info.notes.strip()
        if len(notes) > 700:
            notes = notes[:697] + "..."
        prompt = (
            f"Có phiên bản {APP_NAME} {info.version} ({size_text}).\n\n"
            "Ứng dụng sẽ tải bản mới, kiểm tra SHA-256 rồi tự khởi động lại. "
            "Token và các thiết lập hiện tại được giữ nguyên."
        )
        if notes:
            prompt += f"\n\nNội dung cập nhật:\n{notes}"
        if messagebox.askyesno("Có bản cập nhật", prompt):
            self._start_update_download(info)

    @staticmethod
    def _format_size(size):
        if not size:
            return "không rõ dung lượng"
        value = float(size)
        for unit in ("B", "KB", "MB", "GB"):
            if value < 1024 or unit == "GB":
                return f"{value:.1f} {unit}"
            value /= 1024
        return f"{size} B"

    def _start_update_download(self, info):
        self.update_in_progress = True
        self.update_btn.configure(state="disabled")
        self.start_btn.configure(state="disabled")
        self.run_btn.configure(state="disabled")
        self.progress.start(10)
        self.write_log(f"Đang tải {APP_NAME} {info.version} và xác minh SHA-256...")
        threading.Thread(
            target=self._update_download_worker,
            args=(info,),
            daemon=True,
        ).start()

    def _update_download_worker(self, info):
        try:
            target = updater.download_update(info, APP_DATA / "updates")
            self.after(
                0,
                lambda info=info, target=target: self._install_downloaded_update(
                    info,
                    target,
                ),
            )
        except Exception as exc:
            error_text = str(exc)
            self.after(
                0,
                lambda error_text=error_text: self._finish_update_download_error(
                    error_text
                ),
            )

    def _finish_update_download_error(self, error_text):
        self.update_in_progress = False
        self.progress.stop()
        self.update_btn.configure(state="normal")
        self.start_btn.configure(state="normal")
        self.run_btn.configure(state="disabled")
        self.write_log(f"Cập nhật thất bại: {error_text}")
        messagebox.showerror(f"Cập nhật {APP_NAME}", error_text)

    def _install_downloaded_update(self, info, target):
        self.progress.stop()
        try:
            updater.launch_windows_replacement(target, APP_DATA / "updates")
        except Exception as exc:
            self._finish_update_download_error(str(exc))
            return
        self.write_log(
            f"Đã xác minh {APP_NAME} {info.version}; ứng dụng sẽ tự khởi động lại."
        )
        messagebox.showinfo(
            f"Cập nhật {APP_NAME}",
            f"Đã tải và xác minh phiên bản {info.version}.\n"
            "Ứng dụng sẽ đóng và tự mở lại bằng phiên bản mới.",
        )
        self.destroy()

    def open_browser(self):
        if self.update_in_progress:
            self.write_log("Hãy chờ quá trình cập nhật hoàn tất.")
            return
        if sync_playwright is None:
            messagebox.showerror("Thiếu thư viện", "Chạy: pip install -r requirements.txt && playwright install chromium")
            return
        if self.worker and self.worker.is_alive():
            self.write_log("Chrome đã mở. Hãy đăng nhập rồi bấm nút 2.")
            return
        interval_minutes = _parse_repeat_minutes(self.repeat_minutes.get())
        if interval_minutes is None:
            messagebox.showerror(
                "Chu kỳ không hợp lệ",
                "Số phút lặp lại phải là số nguyên từ 1 đến 10080.",
            )
            return
        self.auto_repeat = bool(self.schedule_enabled.get())
        self.repeat_seconds = interval_minutes * 60
        try:
            _save_settings({
                **_load_settings(),
                "schedule_enabled": self.auto_repeat,
                "repeat_minutes": interval_minutes,
            })
        except OSError as exc:
            messagebox.showerror("Cấu hình chu kỳ", f"Không lưu được chu kỳ lặp: {exc}")
            return
        try:
            self._error_recipient_ids = _parse_recipient_chat_ids(
                self.error_recipient_chat_ids.get()
            )
        except ValueError as exc:
            messagebox.showerror("Chat ID nhận cảnh báo lỗi", str(exc))
            return
        self._telegram_token_for_alerts = self.telegram_token.get().strip()
        # The post-login auto-run skips _apply_telegram_config(), so seed the
        # legacy TXL environment used by the shared alert-routing code here too.
        os.environ["TXL_TELEGRAM_BOT_TOKEN"] = self._telegram_token_for_alerts
        os.environ["TXL_TELEGRAM_GROUP_CHAT_ID"] = self.telegram_chat_id.get().strip()
        if self.mb_extended and not self._prepare_preview_config():
            return
        if self._error_recipient_ids and not self._telegram_token_for_alerts and self.alert_channel.get() != "Zalo":
            messagebox.showerror(
                "Thiếu Telegram Bot token",
                "Cần nhập Telegram Bot token để gửi cảnh báo lỗi riêng.",
            )
            return
        self.stop_requested = False
        self.start_event = threading.Event()
        self.start_btn.configure(state="disabled")
        self.run_btn.configure(state="disabled")
        self._acquire_keep_awake()
        self.worker = threading.Thread(target=self._session_workflow, daemon=True)
        self.worker.start()

    def request_stop(self):
        self.stop_requested = True
        if self.start_event:
            self.start_event.set()
        self.write_log("Đã yêu cầu dừng.")

    def run_workflow(self):
        if self.worker and self.worker.is_alive():
            if self.start_event:
                if not self._apply_telegram_config():
                    return
                if (self._alert_channel in ("Telegram", "Cả hai")
                        and self._alert_destination in ("Group", "Cả hai")
                        and not self._validate_telegram_group()):
                    return
                self.run_btn.configure(state="disabled")
                self.write_log("Bắt đầu bước 2: cấu hình OneBSS và chạy quy trình...")
                self.start_event.set()
            return
        self.write_log("Hãy bấm nút 1 để mở Chrome trước.")

    def _validate_telegram_group(self):
        token = self.telegram_token.get().strip()
        chat_id = self.telegram_chat_id.get().strip()
        try:
            response = requests.get(
                f"https://api.telegram.org/bot{token}/getChat",
                params={"chat_id": chat_id},
                timeout=15,
            )
            payload = response.json()
            if not response.ok or not payload.get("ok"):
                description = payload.get("description") or f"HTTP {response.status_code}"
                if "chat not found" in description.lower():
                    raise RuntimeError(
                        f"Không tìm thấy Group chat ID {chat_id}. Hãy kiểm tra lại ID "
                        "và thêm bot Telegram vào đúng nhóm trước khi chạy."
                    )
                raise RuntimeError(f"Không kiểm tra được nhóm Telegram: {description}")
            return True
        except (requests.RequestException, ValueError, RuntimeError) as exc:
            self.write_log(f"Cấu hình Telegram chưa hợp lệ: {exc}")
            messagebox.showerror("Kiểm tra Telegram", str(exc))
            return False

    def _apply_telegram_config(self):
        token = self.telegram_token.get().strip()
        chat_id = self.telegram_chat_id.get().strip()
        channel = self.alert_channel.get()
        use_telegram = channel in ("Telegram", "Cả hai")
        use_zalo = channel in ("Zalo", "Cả hai")
        if channel not in ("Telegram", "Zalo", "Cả hai"):
            messagebox.showerror(
                "Thiếu kênh cảnh báo",
                "Chọn Telegram, Zalo hoặc Cả hai.",
            )
            return False
        if use_telegram and not token:
            messagebox.showerror("Thiếu cấu hình Telegram", "Kênh đã chọn cần Telegram Bot token.")
            return False
        if use_telegram and not chat_id and self.destination.get() in ("Group", "Cả hai"):
            messagebox.showerror("Thiếu cấu hình Telegram", "Kênh Telegram cần Group chat ID theo nơi nhận đã chọn.")
            return False
        zalo_token = self.zalo_token.get().strip()
        if use_zalo and not zalo_token:
            messagebox.showerror("Thiếu cấu hình Zalo", "Nhập Zalo Bot token để gửi riêng cho nhân sự trực.")
            return False
        try:
            error_recipient_ids = _parse_recipient_chat_ids(
                self.error_recipient_chat_ids.get()
            )
        except ValueError as exc:
            messagebox.showerror("Chat ID nhận cảnh báo lỗi", str(exc))
            return False
        self.auto_repeat = bool(self.schedule_enabled.get())
        interval_minutes = _parse_repeat_minutes(self.repeat_minutes.get())
        if interval_minutes is None:
            messagebox.showerror(
                "Chu kỳ không hợp lệ",
                "Số phút lặp lại phải là số nguyên từ 1 đến 10080.",
            )
            return False
        if self.mb_extended and not self._prepare_preview_config():
            return False
        self.repeat_seconds = interval_minutes * 60
        self._telegram_token_for_alerts = token
        self._alert_channel = channel
        self._zalo_token_for_alerts = zalo_token
        self._error_recipient_ids = error_recipient_ids
        if token:
            os.environ["TXL_TELEGRAM_BOT_TOKEN"] = token
        os.environ["TXL_TELEGRAM_GROUP_CHAT_ID"] = chat_id
        if token:
            os.environ[TELEGRAM_TOKEN_ENV] = token
        os.environ[TELEGRAM_CHAT_ENV] = chat_id
        try:
            if zalo_token:
                credential_store.save_secret(ZALO_CREDENTIAL_SERVICE, "bot-token", zalo_token)
            _save_settings({**_load_settings(),
                "telegram_token": token,
                "telegram_chat_id": chat_id,
                "alert_channel": channel,
                "alert_destination": self.destination.get(),
                "error_recipient_chat_ids": error_recipient_ids,
                "schedule_enabled": self.auto_repeat,
                "repeat_minutes": interval_minutes,
                "keep_awake_enabled": bool(self.keep_awake_enabled.get()),
            })
        except (OSError, subprocess.SubprocessError):
            messagebox.showerror(
                "Không lưu được cấu hình",
                "Không thể lưu cấu hình an toàn trên máy này.",
            )
            return False
        self.write_log(
            f"Đã lưu cấu hình kênh {channel}; chu kỳ lặp là {interval_minutes} phút; "
            f"có {len(error_recipient_ids)} người nhận cảnh báo lỗi."
        )
        return True

    def _send_private_message(self, message):
        """Best-effort delivery to every configured private error recipient."""
        recipients = list(self._error_recipient_ids)
        token = self._telegram_token_for_alerts
        successes = []
        failures = []
        if not token:
            return successes, [(chat_id, "Thiếu Telegram Bot token") for chat_id in recipients]

        for chat_id in recipients:
            try:
                txl.send_telegram_message(
                    message,
                    chat_id=chat_id,
                    bot_token=token,
                )
                successes.append(chat_id)
            except Exception as exc:
                failures.append((chat_id, str(exc)))
        return successes, failures

    def _fetch_private_chats(self):
        """Fetch recent private bot conversations without requiring Terminal."""
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("Telegram", "Hãy dừng quy trình trước khi lấy Chat ID để không tranh luồng OTP.")
            return
        token = self.telegram_token.get().strip()
        if not token:
            messagebox.showerror(
                "Thiếu Telegram Bot token",
                "Hãy nhập Telegram Bot token trước khi lấy Chat ID.",
            )
            return
        self.fetch_chat_btn.configure(state="disabled")
        self.write_log("Đang lấy danh sách người đã nhắn tin riêng cho bot...")
        threading.Thread(
            target=self._fetch_private_chats_worker,
            args=(token,),
            daemon=True,
        ).start()

    def _fetch_private_chats_worker(self, token):
        try:
            response = requests.get(
                f"https://api.telegram.org/bot{token}/getUpdates",
                timeout=15,
            )
            try:
                result = response.json()
            except ValueError:
                result = {}
            if not response.ok or not result.get("ok"):
                description = result.get("description") or f"HTTP {response.status_code}"
                raise RuntimeError(f"Không lấy được Chat ID: {description}")

            chats_by_id = {}
            # Most recent conversations are shown first. Only private chats are
            # eligible; group and channel updates are deliberately ignored.
            for update in reversed(result.get("result", [])):
                message = update.get("message") or update.get("edited_message") or {}
                chat = message.get("chat") or {}
                if chat.get("type") != "private" or "id" not in chat:
                    continue
                chat_id = str(chat["id"])
                if chat_id in chats_by_id:
                    continue
                full_name = " ".join(
                    str(chat.get(key, "")).strip()
                    for key in ("first_name", "last_name")
                    if str(chat.get(key, "")).strip()
                )
                username = str(chat.get("username", "")).strip()
                display_name = full_name or (f"@{username}" if username else "Người dùng Telegram")
                if username and full_name:
                    display_name += f" (@{username})"
                chats_by_id[chat_id] = {
                    "id": chat_id,
                    "name": display_name,
                }

            chats = list(chats_by_id.values())
            self.after(0, lambda chats=chats: self._show_private_chat_picker(chats))
        except Exception as exc:
            error_text = str(exc)
            self.write_log(error_text)
            self.after(
                0,
                lambda error_text=error_text: messagebox.showerror(
                    "Lấy Chat ID",
                    error_text,
                ),
            )
        finally:
            self.after(0, lambda: self.fetch_chat_btn.configure(state="normal"))

    def _fetch_zalo_chats_for_staff(self, staff_name, parent, refresh):
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("Zalo", "Hãy dừng quy trình trước khi lấy ID Zalo.", parent=parent)
            return
        token = self.zalo_token.get().strip()
        if not token:
            messagebox.showerror("Thiếu Zalo Bot token", "Nhập Zalo Bot token ở cửa sổ chính trước.", parent=parent)
            return
        try:
            credential_store.save_secret(ZALO_CREDENTIAL_SERVICE, "bot-token", token)
        except (OSError, subprocess.SubprocessError):
            messagebox.showerror("Zalo Bot token", "Không lưu được token an toàn trên máy này.", parent=parent)
            return
        self.write_log("Đang tìm người vừa nhắn tin riêng cho Zalo Bot...")
        threading.Thread(
            target=self._fetch_zalo_chats_worker,
            args=(token, staff_name, parent, refresh),
            daemon=True,
        ).start()

    def _fetch_zalo_chats_worker(self, token, staff_name, parent, refresh):
        try:
            updates = zalo_bot.fetch_updates(token)
            self._process_zalo_registration_updates(updates)
            chats = zalo_bot.private_chats_from_updates(updates)
            self.after(0, lambda chats=chats: self._show_zalo_chat_picker(chats, staff_name, parent, refresh))
        except Exception as exc:
            error_text = str(exc)
            self.write_log(f"Không lấy được danh sách Zalo: {error_text}")
            self.after(0, lambda error_text=error_text: messagebox.showerror("Lấy Zalo Chat ID", error_text, parent=parent))

    def _start_zalo_registration_listener(self, token):
        if not token or (self._zalo_registration_thread and self._zalo_registration_thread.is_alive()):
            return
        self._zalo_registration_stop.clear()
        self._zalo_registration_started_at_ms = int(time.time() * 1000)
        self._zalo_registration_thread = threading.Thread(
            target=self._zalo_registration_loop,
            args=(token,),
            daemon=True,
        )
        self._zalo_registration_thread.start()
        self.write_log("Đã bật tiếp nhận đăng ký ID Zalo tự động; gửi tin riêng theo cú pháp ‘Họ và tên đăng ký nhận cảnh báo’.")

    def _zalo_registration_loop(self, token):
        last_error_log = 0.0
        while not self._zalo_registration_stop.is_set():
            try:
                updates = zalo_bot.fetch_updates(token, timeout=10)
                self._process_zalo_registration_updates(updates)
            except Exception as exc:
                now = time.monotonic()
                if now - last_error_log >= 60:
                    self.write_log(f"Không thể đọc đăng ký Zalo tự động: {exc}")
                    last_error_log = now
                self._zalo_registration_stop.wait(10)

    def _process_zalo_registration_updates(self, updates):
        for update in updates:
            details = zalo_bot.private_message_details(update)
            if details is None:
                continue
            chat_id, text, message_time, update_key = details
            if not message_time or message_time < self._zalo_registration_started_at_ms:
                continue
            dedupe_key = update_key or f"{chat_id}:{message_time}:{text}"
            if dedupe_key in self._zalo_registration_seen:
                continue
            self._zalo_registration_seen.add(dedupe_key)
            if len(self._zalo_registration_seen) > 1000:
                self._zalo_registration_seen.clear()
            name = zalo_bot.registration_name(text)
            if name:
                self._register_zalo_staff(name, chat_id)

    def _register_zalo_staff(self, submitted_name, chat_id):
        """Bind a new Zalo private chat to one exact employee name in local data."""
        name_key = duty_roster.normalize(submitted_name)
        people_by_name = {}
        rosters = duty_roster.load(ROSTER_PATH)
        if not rosters:
            pending_item = (submitted_name, str(chat_id))
            if pending_item not in self._zalo_registration_pending:
                self._zalo_registration_pending.append(pending_item)
            self.write_log(f"Đã nhận đăng ký Zalo của {submitted_name}; chờ import lịch trực để đối chiếu nhân sự.")
            return
        for person in duty_roster.catalog_staff(rosters):
            person_name = str(person.get("ten_nhan_su", "")).strip()
            if person_name:
                people_by_name.setdefault(duty_roster.normalize(person_name), {})[
                    str(person.get("staff_id") or person_name)
                ] = person_name
        matches = people_by_name.get(name_key, {})
        if len(matches) != 1:
            self.write_log(f"Bỏ qua đăng ký Zalo ‘{submitted_name}’: tên không khớp duy nhất với nhân sự trong lịch đã import.")
            try:
                zalo_bot.send_message(
                    self._zalo_token_for_alerts, chat_id,
                    "Chưa ghép được ID Zalo. Hãy gửi đúng họ tên như trong lịch trực theo cú pháp: Họ Và Tên đăng ký nhận cảnh báo.",
                )
            except Exception as exc:
                self.write_log(f"Không gửi được phản hồi đăng ký Zalo: {exc}")
            return
        employee_name = next(iter(matches.values()))
        contacts = duty_roster.load(ZALO_CONTACTS_PATH)
        normalized_name = duty_roster.normalize(employee_name)
        owner = next((name for name, value in contacts.items()
                      if str(value) == str(chat_id) and duty_roster.normalize(name) != normalized_name), None)
        if owner:
            self.write_log(f"Từ chối đăng ký Zalo của {employee_name}: Chat ID đã thuộc một nhân sự khác.")
            try:
                zalo_bot.send_message(
                    self._zalo_token_for_alerts, chat_id,
                    "ID Zalo này đã được gán cho nhân sự khác. Vui lòng liên hệ quản trị viên để kiểm tra.",
                )
            except Exception as exc:
                self.write_log(f"Không gửi được phản hồi đăng ký Zalo: {exc}")
            return
        contacts[normalized_name] = str(chat_id)
        try:
            duty_roster.save(ZALO_CONTACTS_PATH, contacts)
        except OSError as exc:
            self.write_log(f"Không lưu được ID Zalo của {employee_name}: {exc}")
            return
        self.write_log(f"Đã tự động gán Zalo Chat ID cho nhân sự {employee_name}.")
        try:
            zalo_bot.send_message(
                self._zalo_token_for_alerts, chat_id,
                f"Đăng ký nhận cảnh báo thành công. Tài khoản Zalo đã được gán cho nhân sự {employee_name}.",
            )
        except Exception as exc:
            self.write_log(f"Đã lưu ID Zalo nhưng không gửi được xác nhận cho {employee_name}: {exc}")

    def _show_zalo_chat_picker(self, chats, staff_name, parent, refresh):
        if not chats:
            messagebox.showinfo(
                "Chưa tìm thấy người dùng Zalo",
                f"Chưa nhận được tin nhắn đến bot. Nhân sự {staff_name} cần mở bot, gửi một tin nhắn riêng, "
                "sau đó chọn lại nhân sự và bấm Gán ID Zalo lần nữa. Nếu bot đã cấu hình webhook, getUpdates có thể không dùng được.",
                parent=parent,
            )
            return
        dialog = tk.Toplevel(parent)
        dialog.title(f"Gán Zalo Chat ID — {staff_name}")
        dialog.geometry("600x360")
        dialog.transient(parent)
        dialog.grab_set()
        ttk.Label(dialog, text=f"Nhân sự được chọn: {staff_name}. Chọn đúng tài khoản vừa nhắn tin cho bot.").pack(anchor="w", padx=14, pady=(14, 8))
        listbox = tk.Listbox(dialog, selectmode="browse", exportselection=False)
        listbox.pack(fill="both", expand=True, padx=14, pady=8)
        for chat in chats:
            listbox.insert("end", f'{chat["name"]} — Zalo Chat ID: {chat["id"]}')
        listbox.selection_set(0)

        def save_selected():
            selection = listbox.curselection()
            if not selection:
                messagebox.showwarning("Chưa chọn tài khoản", "Chọn tài khoản Zalo cần liên kết.", parent=dialog)
                return
            chat = chats[selection[0]]
            contacts = duty_roster.load(ZALO_CONTACTS_PATH)
            owner = next((name for name, value in contacts.items() if value == chat["id"] and name != duty_roster.normalize(staff_name)), None)
            if owner:
                messagebox.showerror("ID đã được gán", "Chat ID này đã gắn với một nhân sự khác. Kiểm tra danh bạ trước khi đổi.", parent=dialog)
                return
            contacts[duty_roster.normalize(staff_name)] = chat["id"]
            try:
                duty_roster.save(ZALO_CONTACTS_PATH, contacts)
            except OSError as exc:
                messagebox.showerror("Không lưu được", str(exc), parent=dialog)
                return
            dialog.destroy()
            refresh()
            self.write_log(f'Đã gán Zalo Chat ID cho nhân sự {staff_name}.')

        buttons = ttk.Frame(dialog)
        buttons.pack(fill="x", padx=14, pady=(0, 14))
        ttk.Button(buttons, text="Hủy", command=dialog.destroy).pack(side="right", padx=(8, 0))
        ttk.Button(buttons, text="Gán cho nhân sự", command=save_selected).pack(side="right")

    def _show_private_chat_picker(self, chats):
        if not chats:
            messagebox.showinfo(
                "Chưa tìm thấy người nhận",
                "Hãy mở Telegram, vào đúng bot và gửi /start hoặc một tin nhắn mới. "
                "Sau đó quay lại bấm Lấy Chat ID lần nữa.",
            )
            return

        dialog = tk.Toplevel(self)
        dialog.title("Chọn người nhận cảnh báo")
        dialog.geometry("560x360")
        dialog.minsize(480, 300)
        dialog.transient(self)
        dialog.grab_set()

        ttk.Label(
            dialog,
            text="Chọn một hoặc nhiều người rồi bấm Thêm người đã chọn.",
        ).pack(anchor="w", padx=14, pady=(14, 8))
        listbox = tk.Listbox(dialog, selectmode="extended", exportselection=False)
        listbox.pack(fill="both", expand=True, padx=14, pady=8)
        for chat in chats:
            listbox.insert("end", f'{chat["name"]} — Chat ID: {chat["id"]}')
        listbox.selection_set(0)

        def add_selected():
            selected = listbox.curselection()
            if not selected:
                messagebox.showwarning(
                    "Chưa chọn người nhận",
                    "Hãy chọn ít nhất một người nhận trong danh sách.",
                    parent=dialog,
                )
                return
            try:
                combined = _parse_recipient_chat_ids(
                    self.error_recipient_chat_ids.get()
                )
            except ValueError:
                combined = []
            for index in selected:
                chat_id = chats[index]["id"]
                if chat_id not in combined:
                    combined.append(chat_id)
            self.error_recipient_chat_ids.set(", ".join(combined))
            dialog.destroy()
            self.write_log(
                f"Đã thêm {len(selected)} người nhận cảnh báo lỗi. Hãy bấm Gửi thử để kiểm tra và lưu."
            )

        buttons = ttk.Frame(dialog)
        buttons.pack(fill="x", padx=14, pady=(0, 14))
        ttk.Button(buttons, text="Hủy", command=dialog.destroy).pack(side="right", padx=(8, 0))
        ttk.Button(
            buttons,
            text="Thêm người đã chọn",
            command=add_selected,
        ).pack(side="right")

    def _test_error_recipients(self):
        if not self._apply_telegram_config():
            return
        if not self._error_recipient_ids:
            messagebox.showwarning(
                "Chưa có người nhận",
                "Hãy nhập ít nhất một Chat ID nhận cảnh báo lỗi.",
            )
            return

        message = "\n".join([
            "<b>✅ KIỂM TRA CẢNH BÁO RIÊNG ATS TXL</b>",
            f"<i>{time.strftime('%d/%m/%Y %H:%M:%S')}</i>",
            "",
            "Bot đã gửi thành công tin nhắn kiểm tra đến người nhận này.",
        ])
        successes, failures = self._send_private_message(message)
        if failures:
            details = "\n".join(f"{chat_id}: {error}" for chat_id, error in failures)
            messagebox.showwarning(
                "Kết quả gửi thử",
                f"Gửi thành công: {len(successes)}\nGửi lỗi: {len(failures)}\n\n{details}",
            )
        else:
            messagebox.showinfo(
                "Kết quả gửi thử",
                f"Đã gửi thành công đến {len(successes)} người nhận.",
            )

    def _send_workflow_error_alert(self, error_text):
        if not self._error_recipient_ids:
            self.write_log("Chưa cấu hình người nhận cảnh báo lỗi riêng.")
            return

        escaped_error = html.escape(str(error_text)[:2500])
        escaped_stage = html.escape(self.current_stage)
        escaped_host = html.escape(socket.gethostname())
        escaped_os = html.escape(f"{platform.system()} {platform.release()}")
        message = "\n".join([
            "<b>⚠️ ATS TXL GẶP SỰ CỐ</b>",
            f"<i>{time.strftime('%d/%m/%Y %H:%M:%S')}</i>",
            "",
            f"<b>Máy:</b> {escaped_host}",
            f"<b>Hệ điều hành:</b> {escaped_os}",
            f"<b>Giai đoạn:</b> {escaped_stage}",
            f"<b>Lỗi:</b> {escaped_error}",
            "",
            "Vui lòng kiểm tra ứng dụng, kết nối mạng và phiên đăng nhập OneBSS.",
        ])
        successes, failures = self._send_private_message(message)
        self.write_log(
            f"Cảnh báo lỗi riêng: gửi thành công {len(successes)}/{len(self._error_recipient_ids)} người nhận."
        )
        if failures:
            self.write_log(f"Có {len(failures)} người nhận cảnh báo lỗi không thành công.")

    def _upload_last_diagnostic(self):
        folder = self._last_diagnostic_path
        token = self.github_diagnostics_token.get().strip()
        if not folder or not token:
            if folder and not token:
                self.write_log("Chưa cấu hình GitHub token; gói chẩn đoán chỉ được lưu trên máy.")
            return ""
        try:
            self.write_log("Đang nén và tải một gói chẩn đoán lên GitHub private...")
            url = github_diagnostics.upload_diagnostic(
                folder, DIAGNOSTICS_REPOSITORY, token, APP_SLUG
            )
            self.write_log(f"Đã tải gói chẩn đoán lên GitHub: {url}")
            return url
        except Exception as exc:
            self.write_log(f"Không tải được gói chẩn đoán lên GitHub: {exc}")
            return ""

    @staticmethod
    def _safe_page_url(page):
        try:
            return page.url
        except Exception:
            return "<không đọc được URL>"

    def _record_diagnostic_event(self, context, event_name, **details):
        """Keep a small in-memory event timeline for the current Export only."""
        active = self._active_export_diagnostic
        if not active or active.get("context") is not context:
            return
        active["events"].append(
            {
                "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "event": event_name,
                **{key: str(value)[:2000] for key, value in details.items()},
            }
        )

    def _attach_page_diagnostics(self, context, page):
        page_id = id(page)
        if page_id in self._diagnostic_page_ids:
            return
        self._diagnostic_page_ids.add(page_id)
        page.on(
            "crash",
            lambda: self._record_diagnostic_event(
                context, "page-crash", url=self._safe_page_url(page)
            ),
        )
        page.on(
            "close",
            lambda: self._record_diagnostic_event(
                context, "page-close", url=self._safe_page_url(page)
            ),
        )
        page.on(
            "pageerror",
            lambda error: self._record_diagnostic_event(
                context, "page-error", message=error, url=self._safe_page_url(page)
            ),
        )

        def record_console(message):
            if message.type in ("error", "warning"):
                self._record_diagnostic_event(
                    context,
                    "console-" + message.type,
                    message=message.text,
                    url=self._safe_page_url(page),
                )

        page.on("console", record_console)

    def _attach_context_diagnostics(self, context, page):
        """Subscribe once to browser events needed to identify an Export failure."""
        context_id = id(context)
        if context_id not in self._diagnostic_context_ids:
            self._diagnostic_context_ids.add(context_id)
            context.on(
                "close",
                lambda: self._record_diagnostic_event(context, "context-close"),
            )
            context.on(
                "page",
                lambda new_page: self._attach_page_diagnostics(context, new_page),
            )
            context.on(
                "requestfailed",
                lambda request: self._record_diagnostic_event(
                    context,
                    "request-failed",
                    url=request.url,
                    failure=request.failure,
                ),
            )
            context.on(
                "weberror",
                lambda error: self._record_diagnostic_event(
                    context, "web-error", message=error.error
                ),
            )
            try:
                browser = context.browser
                if browser:
                    browser.on(
                        "disconnected",
                        lambda: self._record_diagnostic_event(
                            context, "browser-disconnected"
                        ),
                    )
            except Exception:
                pass
        self._attach_page_diagnostics(context, page)

    def _begin_export_diagnostic(self, context, page):
        self._active_export_diagnostic = {
            "id": uuid.uuid4().hex[:10],
            "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "context": context,
            "backend": self.browser_backend,
            "page_url": self._safe_page_url(page),
            "events": [],
            "trace_started": False,
        }
        self._attach_context_diagnostics(context, page)
        try:
            context.tracing.start(screenshots=True, snapshots=True, sources=True)
            self._active_export_diagnostic["trace_started"] = True
        except Exception as exc:
            self._record_diagnostic_event(context, "trace-start-failed", message=exc)

    @staticmethod
    def _windows_crash_events():
        if sys.platform != "win32":
            return "Không áp dụng: không phải Windows."
        command = (
            "[Console]::OutputEncoding=[Text.UTF8Encoding]::new();"
            "$since=(Get-Date).AddMinutes(-10);"
            "$events=Get-WinEvent -FilterHashtable @{LogName='Application';StartTime=$since} "
            "-ErrorAction SilentlyContinue | Where-Object {"
            "$_.ProviderName -match 'Application Error|Windows Error Reporting' -or "
            "$_.Message -match 'chrome|chromium|msedge|ATS-TXL'"
            "} | Select-Object TimeCreated,ProviderName,Id,LevelDisplayName,Message;"
            "$events | ConvertTo-Json -Depth 3 -Compress"
        )
        try:
            result = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    command,
                ],
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
            return result.stdout.strip() or result.stderr.strip() or "Không có sự kiện phù hợp."
        except Exception as exc:
            return f"Không đọc được Windows Event Log: {exc}"

    def _finish_export_diagnostic(self, context, page, exc=None):
        active = self._active_export_diagnostic
        if not active or active.get("context") is not context:
            return None
        self._active_export_diagnostic = None

        if exc is None:
            try:
                if active["trace_started"]:
                    context.tracing.stop()
            except Exception:
                pass
            return None

        timestamp = time.strftime("%Y%m%d_%H%M%S")
        folder = APP_DATA / DIAGNOSTICS_DIRNAME / f"export_{timestamp}_{active['id']}"
        folder.mkdir(parents=True, exist_ok=True)
        trace_path = folder / "playwright-trace.zip"
        trace_error = ""
        if active["trace_started"]:
            try:
                context.tracing.stop(path=str(trace_path))
            except Exception as trace_exc:
                trace_error = str(trace_exc)

        summary = {
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "error": str(exc),
            "stage": self.current_stage,
            "browser_backend": active["backend"],
            "export_started_at": active["started_at"],
            "page_url_before_export": active["page_url"],
            "page_url_after_error": self._safe_page_url(page),
            "trace_path": str(trace_path) if trace_path.exists() else "",
            "trace_error": trace_error,
        }
        (folder / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (folder / "browser-events.json").write_text(
            json.dumps(active["events"], ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (folder / "windows-events.json").write_text(
            self._windows_crash_events(), encoding="utf-8"
        )
        self._last_diagnostic_path = folder
        self.write_log(f"Đã lưu gói chẩn đoán Export: {folder}")
        return folder

    def _create_workflow_diagnostic(self, exc):
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        folder = APP_DATA / DIAGNOSTICS_DIRNAME / f"workflow_{timestamp}_{uuid.uuid4().hex[:10]}"
        folder.mkdir(parents=True, exist_ok=True)
        summary = {
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "app": APP_NAME,
            "version": APP_VERSION,
            "host": socket.gethostname(),
            "operating_system": f"{platform.system()} {platform.release()}",
            "stage": self.current_stage,
            "error": str(exc),
        }
        (folder / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        (folder / "windows-events.json").write_text(self._windows_crash_events(), encoding="utf-8")
        self._last_diagnostic_path = folder
        self.write_log(f"Đã lưu gói chẩn đoán Workflow: {folder}")
        return folder

    def _session_workflow(self):
        try:
            self.current_stage = "Khởi tạo Playwright và Chromium"
            if sync_playwright is None:
                raise RuntimeError("Thiếu Playwright. Hãy cài requirements.txt trước.")
            DOWNLOADS.mkdir(exist_ok=True)
            with sync_playwright() as p:
                self.current_stage = "Khởi chạy Chromium"
                ctx = self._launch_browser_context(p)
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                self.browser_context, self.browser_page = ctx, page
                self.current_stage = "Mở OneBSS"
                page.goto(ONEBSS_URL, wait_until="domcontentloaded")
                self.write_log(
                    "Chrome đã mở OneBSS. Hãy đăng nhập nếu được yêu cầu; "
                    "sau khi xác nhận đăng nhập, ứng dụng sẽ tự cấu hình và chạy."
                )
                self.current_stage = "Chờ người dùng đăng nhập OneBSS"
                auto_run_after_login = self._wait_for_login(page)
                self._save_browser_storage_state(ctx)
                if auto_run_after_login:
                    self.write_log("Đã xác nhận đăng nhập OneBSS; tự bắt đầu cấu hình và chạy quy trình.")
                    self.current_stage = "Tự bắt đầu quy trình sau đăng nhập"
                    self.start_event.set()
                else:
                    self.write_log(
                        "Đăng nhập thành công. Website không bị làm mới; "
                        "hãy bấm nút 2 để cấu hình và chạy."
                    )
                    self.after(0, lambda: self.run_btn.configure(state="normal"))
                    self.current_stage = "Chờ bắt đầu quy trình"
                    self.start_event.wait()
                if self.stop_requested:
                    return
                self.current_stage = "Mở màn hình và cấu hình bộ lọc OneBSS"
                try:
                    self._navigate_onebss(page)
                except remote_login.SessionExpired:
                    self._reauthenticate(page)
                    self._navigate_onebss(page)
                self._save_browser_storage_state(ctx)
                self.after(0, lambda: self.progress.start(10))
                cycle = 1
                while not self.stop_requested:
                    self.current_stage = f"Chu kỳ {cycle}: kiểm tra phiên OneBSS"
                    if self.mb_extended and self._onebss_session_expired(page):
                        self._reauthenticate(page)
                        self._navigate_onebss(page)
                    self._ensure_onebss_session_active(page)
                    if cycle > 1:
                        self.write_log(f"Bắt đầu chu kỳ tự động lần {cycle}.")
                    if self.macos_preview:
                        try:
                            excel = self._read_two_scopes(page)
                        except Exception as exc:
                            if isinstance(exc, remote_login.SessionExpired) or self._onebss_session_expired(page):
                                self._reauthenticate(page)
                                self._navigate_onebss(page)
                                continue  # discard partial results; restart both searches
                            raise
                    else:
                        ctx, page, excel = self._export_excel_with_recovery(p, ctx, page, cycle)
                    self.browser_context, self.browser_page = ctx, page
                    self.current_stage = f"Chu kỳ {cycle}: xử lý dữ liệu và gửi Telegram"
                    self._process_and_send(excel)
                    self.write_log("Hoàn tất quy trình.")
                    if not self.auto_repeat:
                        break
                    interval_minutes = self.repeat_seconds // 60
                    self.write_log(
                        f"Đã bật tự động: sẽ chạy lại sau {interval_minutes} phút, không cần bấm thêm nút."
                    )
                    for _ in range(self.repeat_seconds):
                        if self.stop_requested:
                            break
                        time.sleep(1)
                    cycle += 1
                self._close_browser_context(ctx)
                self.browser_context, self.browser_page = None, None
        except Exception as exc:
            self.write_log(f"LỖI: {exc}")
            error_text = str(exc)
            if not self._last_diagnostic_path:
                self._create_workflow_diagnostic(exc)
            if self._last_diagnostic_path:
                error_text += f"\nGói chẩn đoán: {self._last_diagnostic_path}"
                diagnostic_url = self._upload_last_diagnostic()
                if diagnostic_url:
                    error_text += f"\nGitHub chẩn đoán: {diagnostic_url}"
            if not self.stop_requested:
                self._send_workflow_error_alert(error_text)
            self.after(0, lambda error_text=error_text: messagebox.showerror(APP_NAME, error_text))
        finally:
            self.current_stage = "Đã dừng"
            self._remote_config = None
            self.after(0, self.progress.stop)
            self.after(0, self._release_keep_awake)
            self.after(0, lambda: self.start_btn.configure(state="normal"))
            self.after(0, lambda: self.run_btn.configure(state="disabled"))

    def _wait_for_login(self, page):
        if (self.mb_extended and self._remote_config
                and self._onebss_session_expired(page)):
            self._reauthenticate(page)
            return True
        for _ in range(180):
            if self.stop_requested:
                raise RuntimeError("Đã dừng bởi người dùng")
            url = page.url.lower()
            if (not self._onebss_session_expired(page)
                    and ("onebss" in url or page.locator("text=Trang chủ").count())):
                return self.mb_extended
            time.sleep(1)
        raise RuntimeError("Hết thời gian chờ đăng nhập OneBSS")

    def _onebss_session_expired(self, page):
        if page.is_closed():
            return False
        url = page.url.lower()
        if any(marker in url for marker in ("login", "signin", "auth")):
            return True
        try:
            password = page.locator('input[type="password"]').first
            return password.count() > 0 and password.is_visible(timeout=300)
        except Exception:
            return False

    def _ensure_onebss_session_active(self, page):
        if page.is_closed():
            raise RuntimeError(
                "Trình duyệt OneBSS đã bị đóng. Hãy mở lại ứng dụng để tiếp tục."
            )
        if self._onebss_session_expired(page):
            if self.mb_extended:
                raise remote_login.SessionExpired("Phiên OneBSS hết hạn; cần đăng nhập lại")
            raise RuntimeError(
                "Phiên đăng nhập OneBSS đã hết hạn. Hãy mở ứng dụng và đăng nhập lại OneBSS."
            )

    def _launch_browser_context(
        self,
        playwright,
        *,
        headless=False,
        profile=PROFILE,
        browser_channel=None,
        use_configured_channel=True,
    ):
        """Launch an ephemeral context and restore the saved OneBSS session.

        Reusing a persistent Chromium profile can crash Chromium/Chrome when a
        download starts on Windows. Cookies and local storage are persisted via
        Playwright storage state instead, while every browser process receives
        a fresh profile.
        """
        launch_options = {
            "headless": headless,
        }
        context_options = {
            "accept_downloads": True,
            "viewport": {"width": 1440, "height": 900},
        }
        if browser_channel is None and use_configured_channel:
            browser_channel = os.getenv("ATS_BROWSER_CHANNEL", "").strip() or None
        if browser_channel:
            launch_options["channel"] = browser_channel
        if STORAGE_STATE_PATH.is_file():
            context_options["storage_state"] = str(STORAGE_STATE_PATH)
        self.browser_backend = browser_channel or "playwright-chromium"
        browser = playwright.chromium.launch(**launch_options)
        try:
            return browser.new_context(**context_options)
        except Exception:
            if "storage_state" not in context_options:
                browser.close()
                raise
            self.write_log(
                "Trạng thái đăng nhập OneBSS đã lưu không còn hợp lệ; "
                "đang mở phiên sạch để đăng nhập lại."
            )
            context_options.pop("storage_state", None)
            try:
                return browser.new_context(**context_options)
            except Exception:
                browser.close()
                raise

    @staticmethod
    def _close_browser_context(context):
        browser = None
        try:
            browser = context.browser
        except Exception:
            pass
        try:
            context.close()
        except Exception:
            pass
        try:
            if browser and browser.is_connected():
                browser.close()
        except Exception:
            pass

    def _save_browser_storage_state(self, context):
        """Persist authentication without reusing the crash-prone profile."""
        APP_DATA.mkdir(parents=True, exist_ok=True)
        temporary = STORAGE_STATE_PATH.with_suffix(".tmp")
        context.storage_state(path=str(temporary))
        os.replace(temporary, STORAGE_STATE_PATH)
        try:
            STORAGE_STATE_PATH.chmod(0o600)
        except OSError:
            pass

    def _navigate_onebss(self, page):
        menu_name = "Kiểm soát viên - Kiểm soát tồn báo hỏng CNTT"
        self.write_log(f'Mở mục Chăm sóc khách hàng → "{menu_name}"...')
        last_error = None
        for attempt in range(1, 4):
            try:
                page.goto(INCIDENT_INVENTORY_URL, wait_until="domcontentloaded", timeout=30000)
                page.locator('select[name="statusId"]').wait_for(state="attached", timeout=30000)
                page.wait_for_timeout(1500)
                self.write_log(f"Đã mở đúng trang {menu_name}.")
                self._configure_filters(page)
                return
            except Exception as exc:
                last_error = exc
                if self.mb_extended and self._onebss_session_expired(page):
                    raise remote_login.SessionExpired("OneBSS hết phiên trong khi mở màn hình tìm kiếm") from None
                self.write_log(f"Lần cấu hình {attempt}/3 chưa thành công: {exc}")
                if attempt < 3:
                    page.reload(wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_timeout(2000)
        raise RuntimeError(f"Không cấu hình được trang {menu_name}: {last_error}")

    def _configure_filters(self, page):
        self.write_log("Đang cấu hình ngày và bộ lọc theo ảnh mẫu...")
        self._refresh_cycle_dates(page)

        self._ensure_all_statuses(page)

        # Remove previous/persisted tree selections before applying the exact
        # requested set. Unit tree is first, province tree is second.
        self._clear_tree_selections(page, 0)
        self._clear_tree_selections(page, 1)

        self._expand_tree_parent(page, "Đài HTDV CNTT&DVS", 0)
        units, provinces = SEARCH_SCOPES[0]
        for label in units:
            self._ensure_tree_checked(page, label, 0)
        for label in provinces:
            self._ensure_tree_checked(page, label, 1)
        self.write_log(
            "Đã cấu hình ngày, tất cả trạng thái, đơn vị "
            + ", ".join(units)
            + " và tỉnh."
        )

    def _ensure_all_statuses(self, page):
        status_wrapper = page.locator('select[name="statusId"]').locator("xpath=..").first
        status_wrapper.wait_for(state="attached", timeout=30000)
        if status_wrapper.inner_text().strip().lower().startswith("4 selected"):
            return

        popup = page.locator("#statusId_popup")
        status_input = page.locator('input[placeholder="Chọn trạng thái"]').first
        for attempt in range(3):
            try:
                if attempt % 2 == 0:
                    status_input.click(force=True)
                else:
                    status_wrapper.click(force=True)
                popup.wait_for(state="visible", timeout=3000)
                break
            except Exception:
                page.keyboard.press("Escape")
                page.wait_for_timeout(300)
        else:
            raise RuntimeError("Không mở được danh sách Trạng thái")

        select_all = popup.locator(".e-selectall-parent").first
        select_all.wait_for(state="visible", timeout=5000)
        frame_class = select_all.locator(".e-frame").get_attribute("class") or ""
        if "e-check" not in frame_class:
            select_all.click(force=True)
            page.wait_for_timeout(500)
        page.keyboard.press("Escape")
        if not status_wrapper.inner_text().strip().lower().startswith("4 selected"):
            raise RuntimeError("Trạng thái chưa chọn đủ 4 mục")

    def _clear_tree_selections(self, page, index):
        tree = page.locator(".vue-treeselect").nth(index)
        tree.wait_for(state="attached", timeout=10000)
        clear = tree.locator(".vue-treeselect__x-container")
        try:
            if clear.count():
                clear.click(force=True)
                page.wait_for_timeout(500)
        except Exception:
            pass

    def _expand_tree_parent(self, page, label, tree_index=0):
        tree = page.locator(".vue-treeselect").nth(tree_index)
        tree_label = tree.locator("label.vue-treeselect__label").filter(has_text=label).first
        row = tree_label.locator("xpath=ancestor::div[contains(@class,'vue-treeselect__option')]").first
        row.wait_for(state="visible", timeout=10000)
        arrow = row.locator(".vue-treeselect__option-arrow-container")
        if arrow.count():
            svg_class = arrow.locator("svg").get_attribute("class") or ""
            if "--rotated" not in svg_class:
                arrow.click(force=True)
                page.wait_for_timeout(300)

    def _ensure_tree_checked(self, page, label, tree_index):
        tree = page.locator(".vue-treeselect").nth(tree_index)
        text = tree.locator("label.vue-treeselect__label").filter(has_text=label).first
        text.wait_for(state="visible", timeout=10000)
        # The fixed OneBSS footer can cover the last item (Miền Nam). Center
        # the item inside its scrollable tree before clicking.
        text.evaluate('(element) => element.scrollIntoView({block: "center"})')
        page.wait_for_timeout(150)
        row = text.locator("xpath=ancestor::div[contains(@class,'vue-treeselect__option')]").first
        checkbox = row.locator(".vue-treeselect__checkbox").first
        classes = checkbox.get_attribute("class") or ""
        if "--checked" not in classes:
            row.locator(".vue-treeselect__label-container").click()
            page.wait_for_timeout(200)
            classes = checkbox.get_attribute("class") or ""
            if "--checked" not in classes:
                checkbox.click(force=True)
                page.wait_for_timeout(300)
        classes = checkbox.get_attribute("class") or ""
        if "--checked" not in classes:
            raise RuntimeError(f'Không chọn được mục "{label}"')

    def _click_page_text(self, page, text):
        candidates = (
            page.get_by_text(text, exact=False).first,
            page.locator("a").filter(has_text=text).first,
            page.locator("button").filter(has_text=text).first,
            page.locator("li").filter(has_text=text).first,
        )
        for item in candidates:
            try:
                item.wait_for(state="visible", timeout=5000)
                item.click(timeout=5000)
                return
            except Exception:
                continue
        # Save a diagnostic screenshot next to the downloaded files.
        try:
            page.screenshot(path=str(DOWNLOADS / "onebss-navigation-error.png"), full_page=True)
        except Exception:
            pass
        raise RuntimeError(f'Không tìm thấy mục OneBSS: "{text}". Hãy kiểm tra đã đăng nhập và trang đã tải xong.')

    def _refresh_cycle_dates(self, page):
        """Apply today's date to OneBSS and keep the desktop UI in sync."""
        today = time.strftime("%d/%m/%Y")
        self.after(0, lambda: (self.from_date.set(today), self.to_date.set(today)))
        self._fill_date(page, today, 0)
        self._fill_date(page, today, 1)
        return today

    def _fill_date(self, page, value, index):
        inputs = page.locator('input.mx-input, input[type="date"], input[placeholder*="ngày"], input[placeholder*="Ngày"]')
        if inputs.count() <= index:
            raise RuntimeError("Không tìm thấy ô ngày trên màn hình OneBSS")
        date_input = inputs.nth(index)
        date_input.fill(value)
        # OneBSS uses a reactive date widget. Blur commits the changed value
        # before the next search, especially when the calendar date rolls over.
        date_input.press("Tab")
        page.wait_for_timeout(150)

    def _check_text(self, page, label):
        loc = page.get_by_text(label, exact=True)
        if loc.count():
            try:
                loc.first.scroll_into_view_if_needed()
                loc.first.click(force=True)
                return
            except Exception:
                pass
        # Tree controls often attach the click handler to the row/container,
        # not to the text node itself.
        for selector in ("label", "li", "tr", "div"):
            try:
                row = page.locator(selector).filter(has_text=label).first
                if row.count() and row.is_visible(timeout=300):
                    row.click(force=True)
                    return
            except Exception:
                continue

    def _export_excel(self, page, context):
        if page.is_closed():
            raise RuntimeError("Trình duyệt đã đóng. Hãy bấm nút 1 để mở lại OneBSS.")
        self._ensure_onebss_session_active(page)
        self._last_diagnostic_path = None
        self._begin_export_diagnostic(context, page)
        try:
            self.write_log("Bấm Tìm kiếm và chờ tải hết phiếu...")
            page.get_by_text("Tìm kiếm", exact=True).click(timeout=15000)
            self._wait_for_search_complete(page)
            self.write_log("Bấm Xuất Excel...")
            with page.expect_download(timeout=120000) as download_info:
                page.get_by_text("Xuất Excel", exact=True).click(timeout=15000)
            download = download_info.value
            target = DOWNLOADS / ("Bao_hong_ton_" + time.strftime("%Y%m%d%H%M%S") + ".xlsx")
            download.save_as(str(target))
            self._finish_export_diagnostic(context, page)
            self.write_log(f"Đã lưu Excel: {target}")
            return target
        except Exception as exc:
            self._finish_export_diagnostic(context, page, exc)
            raise

    @staticmethod
    def _is_excel_download_timeout(exc):
        return 'event "download"' in str(exc).lower()

    def _recover_onebss_after_download_timeout(self, page, cycle, recovery_attempt):
        self.current_stage = f"Chu kỳ {cycle}: phục hồi OneBSS sau lỗi xuất Excel"
        self.write_log(
            "OneBSS không tạo file Excel sau 2 phút. "
            f"Đang làm mới trang và cấu hình lại (lần {recovery_attempt}/"
            f"{MAX_EXCEL_RECOVERY_ATTEMPTS})..."
        )
        page.reload(wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(1500)
        self._ensure_onebss_session_active(page)
        # Navigate again and configure every filter from a clean state. This
        # is deliberately not just a retry of the Export button.
        self._navigate_onebss(page)
        self._ensure_onebss_session_active(page)
        self.write_log("Đã phục hồi OneBSS; chạy lại tìm kiếm và xuất Excel.")

    @staticmethod
    def _is_browser_closed_error(exc):
        return isinstance(exc, TargetClosedError) or (
            "target page, context or browser has been closed" in str(exc).lower()
        )

    def _recover_closed_browser(self, playwright, context, cycle, recovery_attempt):
        """Replace a crashed/closed context and restore the OneBSS workspace."""
        self.current_stage = f"Chu kỳ {cycle}: phục hồi Chromium sau khi bị đóng"
        self.write_log(
            "Chromium/OneBSS đã bị đóng khi xuất Excel. "
            f"Đang mở lại và cấu hình lại (lần {recovery_attempt}/"
            f"{MAX_EXCEL_RECOVERY_ATTEMPTS})..."
        )
        self._close_browser_context(context)

        # A repeated Export failure can be specific to Playwright Chromium.
        # On macOS use the installed stable Google Chrome first; on Windows,
        # try Chrome then Edge. Every candidate gets a fresh browser profile
        # and restores the saved OneBSS authentication state.
        if sys.platform == "win32":
            channels = ("chrome", "msedge", None)
        elif sys.platform == "darwin":
            channels = ("chrome", None)
        else:
            channels = (None,)
        last_error = None
        for channel in channels:
            backend = channel or "playwright-chromium"
            new_context = None
            try:
                self.write_log(f"Đang mở lại OneBSS bằng {backend}...")
                new_context = self._launch_browser_context(
                    playwright,
                    browser_channel=channel,
                    use_configured_channel=False,
                )
                new_page = (
                    new_context.pages[0] if new_context.pages else new_context.new_page()
                )
                self.browser_context, self.browser_page = new_context, new_page
                new_page.goto(ONEBSS_URL, wait_until="domcontentloaded", timeout=30000)
                self._ensure_onebss_session_active(new_page)
                self._navigate_onebss(new_page)
                self._ensure_onebss_session_active(new_page)
                self._save_browser_storage_state(new_context)
                self.write_log(
                    f"Đã mở lại OneBSS bằng {backend} và cấu hình xong; "
                    "chạy lại tìm kiếm và xuất Excel."
                )
                return new_context, new_page
            except Exception as exc:
                last_error = exc
                self.write_log(f"Không mở được OneBSS bằng {backend}: {exc}")
                try:
                    if new_context:
                        self._close_browser_context(new_context)
                except Exception:
                    pass
        raise RuntimeError(f"Không thể khởi chạy lại OneBSS sau lỗi browser: {last_error}")

    def _export_excel_with_recovery(self, playwright, context, page, cycle):
        """Export once, then recover from an absent download or closed browser."""
        recovery_attempt = 0
        while True:
            self.current_stage = f"Chu kỳ {cycle}: cập nhật ngày và bộ lọc"
            self._refresh_cycle_dates(page)
            self._save_browser_storage_state(context)
            self.current_stage = f"Chu kỳ {cycle}: tìm kiếm và xuất Excel"
            try:
                return context, page, self._export_excel(page, context)
            except Exception as exc:
                recover_download = isinstance(exc, PlaywrightTimeoutError) and (
                    self._is_excel_download_timeout(exc)
                )
                recover_browser = self._is_browser_closed_error(exc) or page.is_closed()
                if (
                    not (recover_download or recover_browser)
                    or recovery_attempt >= MAX_EXCEL_RECOVERY_ATTEMPTS
                ):
                    raise
                recovery_attempt += 1
                if recover_browser:
                    context, page = self._recover_closed_browser(
                        playwright, context, cycle, recovery_attempt
                    )
                else:
                    self._recover_onebss_after_download_timeout(
                        page, cycle, recovery_attempt
                    )

    def _wait_for_search_complete(self, page, timeout_seconds=600):
        """Wait until OneBSS finishes loading every record.

        OneBSS removes the ``disabled`` class from the "Dừng Xử lý" action
        while a search is running and restores it only after all pages have
        been loaded. Exporting before that transition produces a partial file.
        """
        stop_control = page.get_by_text("Dừng Xử lý", exact=True).first
        stop_control.wait_for(state="visible", timeout=15000)
        deadline = time.monotonic() + timeout_seconds
        processing_seen = False
        next_progress_log = time.monotonic() + 30

        while time.monotonic() < deadline:
            if self.stop_requested:
                raise RuntimeError("Đã dừng bởi người dùng")
            if page.is_closed():
                raise RuntimeError("Trình duyệt đã đóng trong khi OneBSS đang tìm kiếm.")
            self._ensure_onebss_session_active(page)

            classes = (stop_control.get_attribute("class") or "").split()
            is_disabled = "disabled" in classes
            if not is_disabled:
                processing_seen = True
            elif processing_seen:
                page.wait_for_timeout(1000)
                totals = page.locator("text=/Tổng cộng.*bản ghi/").all_inner_texts()
                if totals:
                    self.write_log(f"OneBSS đã tải xong: {totals[-1].strip()}")
                else:
                    self.write_log("OneBSS đã tải xong toàn bộ kết quả.")
                return

            if time.monotonic() >= next_progress_log:
                self.write_log("OneBSS vẫn đang xử lý; tiếp tục chờ, chưa xuất Excel...")
                next_progress_log += 30
            page.wait_for_timeout(500)

        if not processing_seen:
            raise RuntimeError("OneBSS không bắt đầu xử lý sau khi bấm Tìm kiếm.")
        raise RuntimeError(
            f"OneBSS chưa xử lý xong sau {timeout_seconds // 60} phút; chưa xuất Excel để tránh thiếu bản ghi."
        )

    def _process_and_send(self, excel):
        """Use the transferred MonitorTXL source directly on macOS/Windows."""
        is_grid = isinstance(excel, txl.pd.DataFrame)
        self.write_log("Đang xử lý bảng OneBSS..." if is_grid else "Đang xử lý Excel bằng mã nguồn MonitorTXL...")
        source_frame = excel.copy() if is_grid else txl.pd.read_excel(excel, dtype=object)
        catalog = self._refresh_service_catalog(source_frame)
        mb_staff, mb_installation = split_mb_ticket_scopes(
            source_frame,
            duty_roster.load(CONTACTS_PATH),
        )
        alert_scopes = (
            ("Nhân sự MB", mb_staff),
            ("Địa chỉ lắp đặt MB", mb_installation),
        )
        report_source = txl.pd.concat([mb_staff, mb_installation], ignore_index=True)
        self.write_log(
            f"Lọc MB: {len(mb_staff)} phiếu có nhân viên trong danh sách ID chat; "
            f"{len(mb_installation)} phiếu có tỉnh lắp đặt miền Bắc sau loại trừ nhân viên MB."
        )
        if is_grid:
            txl.FILE_DATA_TIME = txl.datetime.now()
        frame = txl.process_bh_file(report_source)
        frame = self._filter_service_frame(frame, catalog)
        alert_frame = txl.get_alert_dataframe_bh(frame)
        self.write_log(
            f"Đã lọc {len(frame)} phiếu phù hợp; "
            f"có {len(alert_frame)} phiếu từ 10 phút trở lên."
        )
        completion, in_progress = txl.get_operational_alert_data(report_source)
        completion = self._filter_service_frame(completion, catalog)
        in_progress = self._filter_service_frame(in_progress, catalog)
        if len(frame) or len(completion) or len(in_progress):
            report = DOWNLOADS / ("Bao_cao_TOMS_SLA_MB_" + time.strftime("%Y%m%d_%H%M%S") + ".xlsx")
            if len(frame):
                txl.FILE_DATA_TIME = txl.datetime.now() if is_grid else txl.get_file_datetime(str(excel))
                txl.export_bh_excel(frame, str(report))
            txl.append_operational_alert_sheets(completion, in_progress, str(report))
            self.write_log(f"Đã tạo báo cáo: {report}")

        for scope_label, scope_frame in alert_scopes:
            txl.FILE_DATA_TIME = txl.datetime.now()
            scope_txl = txl.process_bh_file(scope_frame)
            scope_txl = self._filter_service_frame(scope_txl, catalog)
            scope_preprocessing = txl.get_alert_dataframe_bh(scope_txl)
            scope_completion, scope_in_progress = txl.get_operational_alert_data(scope_frame)
            scope_completion = self._filter_service_frame(scope_completion, catalog)
            scope_in_progress = self._filter_service_frame(scope_in_progress, catalog)
            self.write_log(
                f"{scope_label}: TXL {len(scope_preprocessing)}, chưa nghiệm thu "
                f"{len(scope_completion)}, đang thực hiện {len(scope_in_progress)} phiếu."
            )
            self._send_routed_alerts(
                scope_preprocessing,
                scope_completion,
                scope_in_progress,
                catalog,
                scope_label,
            )

    def _open_service_settings(self):
        client = service_catalog.ServiceCatalog(SERVICE_CATALOG_API_URL)
        try:
            staff = duty_roster.catalog_staff(duty_roster.load(ROSTER_PATH))
            if staff:
                catalog = client.get()
                known_staff = {str(item["staff_id"]) for item in catalog.get("staff", [])}
                missing_staff = [item for item in staff if item["staff_id"] not in known_staff]
                if missing_staff:
                    client.sync_staff(missing_staff)
            service_ui.open_service_window(
                self, client, self.service_preferences, self._save_service_preferences,
                local_staff=staff,
            )
        except service_catalog.CatalogError as exc:
            messagebox.showerror(
                "Dịch vụ và nhân sự",
                "Không đồng bộ được danh mục. Hãy cập nhật mã Apps Script theo services_api.gs và triển khai phiên bản mới.\n\n" + str(exc),
                parent=self,
            )

    def _save_service_preferences(self, value):
        self.service_preferences = value
        _save_settings({**_load_settings(), **value})
        self.write_log("Đã lưu lựa chọn lọc dịch vụ riêng trên máy này.")

    def _refresh_service_catalog(self, source_frame):
        client = service_catalog.ServiceCatalog(SERVICE_CATALOG_API_URL)
        catalog = client.get()
        if "loaihinh_tb" not in source_frame.columns:
            raise service_catalog.CatalogError("Bảng OneBSS thiếu cột Loại hình thuê bao")
        known = {service_catalog.service_key(item["ten_dich_vu"]) for item in catalog["services"]}
        discovered = {}
        for value in source_frame["loaihinh_tb"].dropna().astype(str):
            value = value.strip()
            if value and service_catalog.service_key(value) not in known:
                discovered.setdefault(service_catalog.service_key(value), value)
        if discovered:
            added = client.sync_services(list(discovered.values()))
            catalog = client.get()
            names = [item["ten_dich_vu"] for item in added]
            if names:
                try:
                    txl.send_telegram_message("Dịch vụ mới trên OneBSS:\n" + "\n".join("• " + html.escape(name) for name in names), bot_token=self._telegram_token_for_alerts)
                except Exception as exc:
                    self.write_log(f"Chưa gửi được thông báo dịch vụ mới qua Telegram: {exc}")
                self.write_log("Đã đồng bộ dịch vụ mới: " + ", ".join(names))
        return catalog

    def _filter_service_frame(self, frame, catalog):
        mode = self.service_preferences.get("service_filter_mode", "all")
        label_ids = self.service_preferences.get("selected_service_label_ids", [])
        service_ids = self.service_preferences.get("selected_service_ids", [])
        if mode != "custom" and not label_ids:
            return frame
        # A stored label always means its membership is the intended filter,
        # including settings written by older builds where label selection did
        # not switch the radio mode and all services were saved as explicit IDs.
        if mode != "custom":
            service_ids = []
        return service_catalog.filter_selected_services(frame, service_ids, label_ids, catalog)


if __name__ == "__main__":
    # Used by the Windows build pipeline without opening a GUI or requiring
    # OneBSS/Telegram credentials.
    if "--self-test" in sys.argv:
        expected_units = {
            "Phòng HTKH Miền Bắc (VIP 1)",
            "Phòng HTKH Miền Nam (VIP 2)",
            "Phòng HTKH Miền Trung (VIP 3)",
        }
        expected_regions = {"Tập trung", "Miền Bắc", "Miền Trung", "Miền Nam"}
        if len(SEARCH_SCOPES) != 1 or set(SEARCH_SCOPES[0][0]) != expected_units or set(SEARCH_SCOPES[0][1]) != expected_regions:
            raise SystemExit("MB OneBSS scope self-test failed")
        if _parse_repeat_minutes("5") != 5:
            raise SystemExit("5-minute schedule self-test failed")
        if len(MB_PROVINCES) != 28 or not {
            duty_roster.normalize(name) for name in ("Thanh Hóa", "Nghệ An", "Hà Tĩnh")
        }.issubset(MB_PROVINCES):
            raise SystemExit("MB province mapping self-test failed")
        sample = txl.pd.DataFrame([
            {"ten_nv": "Nhân sự MB", "tentinh": "Tỉnh Cà Mau", "diachi_ld": "Cà Mau"},
            {"ten_nv": "Người khác", "tentinh": "Thành phố Hà Nội", "diachi_ld": "Hà Nội"},
            {"ten_nv": "Người khác", "tentinh": "Tỉnh Cà Mau", "diachi_ld": "Cà Mau"},
        ])
        staff, installation = split_mb_ticket_scopes(sample, {"nhân sự mb": "123"})
        if len(staff) != 1 or len(installation) != 1 or installation.iloc[0]["ten_nv"] != "Người khác":
            raise SystemExit("MB ticket-routing self-test failed")
        catalog = {"services": [{"service_id": "s1", "ten_dich_vu": "MetroNet FE"}],
                   "labels": [{"label_id": "l1", "ten_nhan": "Nhánh 4"}],
                   "links": [{"label_id": "l1", "service_id": "s1"}],
                   "staff": [{"staff_id": "person-a", "ten_nhan_su": "Nhân sự A"}],
                   "staff_labels": [{"staff_label_id": "sl1", "ten_nhan": "Ca MB"}],
                   "staff_links": [{"staff_label_id": "sl1", "staff_id": "person-a"}]}
        catalog = service_catalog.validate_catalog({"ok": True, **catalog})
        if service_catalog.staff_ids_for_labels(catalog, ["sl1"]) != {"person-a"}:
            raise SystemExit("MB staff-label mapping self-test failed")
        test_now = txl.datetime(2026, 9, 29, 10, 0, tzinfo=duty_roster.VIETNAM)
        test_rosters = {"2026-09": {"people": {
            "employee-a": {"name": "Nhân sự A", "shifts": {"29": "HC"}},
            "employee-b": {"name": "Nhân sự B", "shifts": {"29": "HC"}},
        }}}
        routed = duty_roster.recipients(
            test_rosters,
            {"nhân sự a": "1001", "nhân sự b": "1002"},
            now=test_now,
            staff_ids={duty_roster.staff_key("employee-a")},
        )
        if routed != ["1001"]:
            raise SystemExit("MB selected staff-label recipient self-test failed")
        service_frame = txl.pd.DataFrame([{"loaihinh_tb": "MetroNet FE"}, {"loaihinh_tb": "Khác"}])
        if len(service_catalog.filter_selected_services(service_frame, [], ["l1"], catalog)) != 1:
            raise SystemExit("MB service-label filtering self-test failed")
        raise SystemExit(0)
    if "--browser-self-test" in sys.argv:
        if sync_playwright is None:
            raise SystemExit("Playwright unavailable")
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context()
            page = context.new_page()
            page.set_content("<title>TOMS SLA browser self-test</title>")
            if page.title() != "TOMS SLA browser self-test":
                raise SystemExit("Chromium self-test failed")
            context.close()
            browser.close()
        raise SystemExit(0)
    app = ATSApp()
    ready_file = os.getenv("TOMS_UPDATE_READY_FILE", "").strip()
    if ready_file:
        try:
            ready_path = Path(ready_file)
            ready_path.parent.mkdir(parents=True, exist_ok=True)
            ready_path.write_text(str(os.getpid()), encoding="ascii")
        except OSError:
            pass
    app.mainloop()
