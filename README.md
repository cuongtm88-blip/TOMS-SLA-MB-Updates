# TOMS SLA MB Updates

Windows updates for TOMS SLA MB are built by the `release-windows.yml` workflow.
Version 1.0.7 adds shared staff labels in the service settings window. Selected
service labels enable service filtering; selected staff labels limit private
alerts to matching on-duty personnel. Group alerts are unchanged. It searches
with all statuses, the three HTKH units (MB/MN/MT), and all four OneBSS regions.

The shared service catalog is configured at `config.py`. To enable staff-label
management, update the Google Apps Script project from `services_api.gs` and
deploy a new version while keeping its existing `/exec` URL. Personnel codes
are stored as stable hashes, and no names or Telegram Chat IDs are sent to the
online catalog.

After updating, open **OneBSS / OTP / Lịch trực** and import the Telegram ID
spreadsheet on each Windows computer. The contact list is intentionally not
stored in this public repository; imported contacts remain in that computer's
application data. Select **Group** if alerts should go only to the group.
