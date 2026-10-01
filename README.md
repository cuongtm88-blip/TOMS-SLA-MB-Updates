# TOMS SLA MB Updates

Windows updates for TOMS SLA MB are built by the `release-windows.yml` workflow.
The app searches with all statuses, the three HTKH units (MB/MN/MT), and all
four OneBSS regions. Selected service labels enable service filtering; selected
staff labels limit private alerts to matching on-duty personnel. Group alerts
are unchanged.

The shared service catalog is configured at `config.py`. To enable staff-label
management, update the Google Apps Script project from `services_api.gs` and
deploy a new version while keeping its existing `/exec` URL. Personnel codes
are stored as stable hashes, and no names or Telegram Chat IDs are sent to the
online catalog.

After updating, open **OneBSS / OTP / Lịch trực** and import the Telegram ID
spreadsheet on each Windows computer. The contact list is intentionally not
stored in this public repository; imported contacts remain in that computer's
application data. Select **Group** if alerts should go only to the group.

To link a Zalo account automatically, keep TOMS SLA MB open, set the Zalo Bot
token, import the duty roster, then have the employee send the bot a private
message in the form `Full Name_Đăng ký nhận cảnh báo` (a space instead of `_`
is also accepted). The name must exactly match one employee in the imported
roster. The app confirms successful registration and stores the Zalo Chat ID
locally on that computer. The bot must use long polling and must not be polled
by another app instance at the same time.
