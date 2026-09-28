# TOMS SLA MB Updates

Windows updates for TOMS SLA MB are built by the `release-windows.yml` workflow.
Version 1.0.4 adds the MB staff/location split and routes all three alert types
to the selected destinations. It searches with all statuses, the three HTKH
units (MB/MN/MT), and all four OneBSS regions.

After updating, open **OneBSS / OTP / Lịch trực** and import the Telegram ID
spreadsheet on each Windows computer. The contact list is intentionally not
stored in this public repository; imported contacts remain in that computer's
application data. Select **Group** if alerts should go only to the group.
