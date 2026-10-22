"""Offline tests: no real credentials, login attempts or Telegram messages."""
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, Mock

import pandas as pd
import duty_roster as roster
import onebss_grid as grid
import remote_login as login
import remote_resume
import app
import TXL_Monitor_Tele_Group_All_Over10 as txl
import zalo_bot


def snapshot(ids=("BH_1",), status="Mới tiếp nhận", handler="Trống"):
    headers = ["Mã báo hỏng", "Loại hình TB", "Đơn vị nhận", "Nhân viên", "Ngày báo hỏng",
               "Trạng thái báo hỏng", "Đơn vị xử lý", "Đơn vị đang thực hiện", "Tỉnh", "Địa chỉ lắp đặt"]
    values = ["", "Megawan", "Phòng HTKH Miền Bắc", "Nhân viên", "28/09/2026 08:00:00",
              status, handler, "Phòng HTKH Miền Bắc", "Thành phố Hà Nội", "Số 1 Hà Nội"]
    rows = [dict(zip(headers, [ident] + values[1:])) for ident in ids]
    return {"headers": headers, "rows": rows, "page": 1, "total": len(rows)}


class PreviewTests(unittest.TestCase):
    def test_resume_requires_authorized_private_chat_and_exact_command(self):
        def update(chat_id, text, *, chat_type="private", sender_id=None, is_bot=False):
            sender_id = chat_id if sender_id is None else sender_id
            return {"message": {
                "chat": {"id": chat_id, "type": chat_type},
                "from": {"id": sender_id, "is_bot": is_bot},
                "text": text,
            }}

        self.assertTrue(remote_resume.resume_command(update(123, "/resume"), {"123"}))
        self.assertTrue(remote_resume.resume_command(update(123, "/resume@TomsBot"), {"123"}))
        self.assertFalse(remote_resume.resume_command(update(456, "/resume"), {"123"}))
        self.assertFalse(remote_resume.resume_command(update(-123, "/resume", chat_type="group"), {"123"}))
        self.assertFalse(remote_resume.resume_command(update(123, "/resume now"), {"123"}))
        self.assertFalse(remote_resume.resume_command(update(123, "/resume", is_bot=True), {"123"}))

    def test_resume_state_and_request_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            data_dir = Path(temporary)
            (data_dir / "settings.json").write_text(
                '{"otp_chat_ids":"123, -456, 789x, 456"}', encoding="utf-8"
            )
            self.assertEqual(remote_resume._chat_ids(remote_resume._read_settings(data_dir)), {"123", "456"})
            remote_resume._write_resume_request(data_dir)
            self.assertTrue((data_dir / "remote-resume.json").exists())
            (data_dir / "remote-workflow.json").write_text(
                '{"pid":' + str(os.getpid()) + '}', encoding="utf-8"
            )
            self.assertTrue(remote_resume._active_workflow(data_dir))

    def test_repeat_interval_and_mb_scopes(self):
        self.assertEqual(app._parse_repeat_minutes("5"), 5)
        self.assertIsNone(app._parse_repeat_minutes("0"))
        self.assertIsNone(app._parse_repeat_minutes("x"))
        self.assertEqual(len(app.SEARCH_SCOPES), 1)
        self.assertEqual(len(app.SEARCH_SCOPES[0][0]), 3)
        self.assertEqual(app.SEARCH_SCOPES[0][1], ("Tập trung", "Miền Bắc", "Miền Trung", "Miền Nam"))
        self.assertEqual(len(app.MB_PROVINCES), 28)
        self.assertTrue({"thanh hóa", "nghệ an", "hà tĩnh"}.issubset(app.MB_PROVINCES))

        source = pd.DataFrame([
            {"ten_nv": "Trần Quốc Ân", "tentinh": "Tỉnh Cà Mau", "diachi_ld": "Cà Mau"},
            {"ten_nv": "Nhân viên ngoài danh sách", "tentinh": "Thành phố Hà Nội", "diachi_ld": "Hà Nội"},
            {"ten_nv": "Trần Quốc Ân", "tentinh": "Thành phố Hà Nội", "diachi_ld": "Hà Nội"},
            {"ten_nv": "Nhân viên ngoài danh sách", "tentinh": "Tỉnh Cà Mau", "diachi_ld": "Cà Mau"},
            {"ten_nv": "Nhân viên ngoài danh sách", "tentinh": "Tỉnh Thanh Hóa", "diachi_ld": "Thanh Hóa"},
            {"ten_nv": "Nhân viên ngoài danh sách", "tentinh": "Tỉnh Nghệ An", "diachi_ld": "Nghệ An"},
            {"ten_nv": "Nhân viên ngoài danh sách", "tentinh": "Tỉnh Hà Tĩnh", "diachi_ld": "Hà Tĩnh"},
        ])
        staff, installation = app.split_mb_ticket_scopes(
            source,
            {roster.normalize("Trần Quốc Ân"): "123456789"},
        )
        self.assertEqual(len(staff), 2)
        self.assertEqual(len(installation), 4)
        self.assertEqual(installation.iloc[0]["ten_nv"], "Nhân viên ngoài danh sách")
        self.assertEqual(set(installation["tentinh"].iloc[1:]), {"Tỉnh Thanh Hóa", "Tỉnh Nghệ An", "Tỉnh Hà Tĩnh"})

    def test_roster_boundaries(self):
        rosters = {"2026-09": {"people": {"old": {"name": "Previous", "shifts": {"30": "Đêm"}}}},
                   "2026-10": {"people": {"day": {"name": "Day", "shifts": {"1": "HC"}},
                                          "night": {"name": "Night", "shifts": {"1": "Đêm"}}}}}
        for clock, expected in (("00:00", "Previous"), ("07:59", "Previous"), ("08:00", "Day"),
                                ("16:59", "Day"), ("17:00", "Night"), ("23:59", "Night")):
            now = datetime.fromisoformat("2026-10-01T" + clock)
            self.assertEqual([p["name"] for p in roster.on_duty(rosters, now)], [expected])
        self.assertEqual(roster.recipients(rosters, {"day": "123"}, datetime(2026, 10, 1, 8)), ["123"])
        with self.assertRaises(ValueError):
            roster.recipients(rosters, {}, datetime(2026, 10, 1, 8))

    def test_supplied_workbooks(self):
        source = Path("/Users/tranmanhcuong/Downloads/tthtkh_xlsx_lichtrucca_t10.xlsx")
        contacts = next(Path(__file__).parent.glob("Danh*.xlsx"), None)
        if not source.exists() or contacts is None:
            self.skipTest("Local user fixtures unavailable")
        result = roster.read_roster(source, 2026, 10)
        ids = roster.read_contacts(contacts)
        self.assertEqual(len(result["people"]), 20)
        self.assertTrue(all(roster.normalize(p["name"]) in ids for p in result["people"].values()))
        with self.assertRaises(ValueError):
            roster.read_roster(source, 2026, 9)

    def test_grid_aliases_empty_and_validation(self):
        frame = grid.parse(snapshot())
        self.assertEqual(frame.iloc[0]["ten_dv_xl"], "")
        self.assertEqual(len(grid.parse(snapshot(()))), 0)
        data = snapshot()
        data["headers"].remove("Nhân viên")
        with self.assertRaises(RuntimeError):
            grid.parse(data)
        data = snapshot()
        data["rows"][0]["Ngày báo hỏng"] = "2026-09-28T01:00:00Z"
        self.assertEqual(grid.parse(data).iloc[0]["ngay_bh"], "28/09/2026 08:00:00")
        data["rows"][0]["Ngày báo hỏng"] = "invalid"
        with self.assertRaises(RuntimeError):
            grid.parse(data)

    def test_grid_pagination_count_and_duplicates(self):
        class Page:
            def __init__(self, duplicate=False):
                self.number = 1
                self.duplicate = duplicate
            def evaluate(self, script):
                value = snapshot(("BH_1" if self.number == 1 or self.duplicate else "BH_2",))
                value.update(page=self.number, total=2)
                return value
            def locator(self, selector):
                return self
            @property
            def first(self):
                return self
            def click(self, **kwargs):
                self.number += 1
            def wait_for_function(self, *args, **kwargs):
                pass
            def wait_for_timeout(self, delay):
                pass
        # This test covers pagination after page-size selection; the mocked
        # page does not model the EJ2 pager itself.
        with patch.object(grid, "_select_100_per_page", return_value=False):
            self.assertEqual(grid.read(Page())["ma_bh"].tolist(), ["BH_1", "BH_2"])
            with self.assertRaises(RuntimeError):
                grid.read(Page(True))

    def test_select_100_uses_ej2_dropdown_and_waits_for_applied_state(self):
        class Locator:
            def __init__(self, page):
                self.page = page
            @property
            def first(self):
                return self
            def nth(self, _index):
                return self
            def locator(self, _selector):
                return self
            def count(self):
                return 1
            def wait_for(self, **_kwargs):
                pass
            def click(self, **_kwargs):
                self.page.opened = True
            def press(self, key, **_kwargs):
                self.page.keys.append(key)
                if key == "Enter":
                    self.page.selected = True

        class Page:
            opened = False
            selected = False
            keys = []
            def locator(self, _selector):
                return Locator(self)
            def is_closed(self):
                return False
            def wait_for_timeout(self, _delay):
                pass
            def evaluate(self, script, *_args):
                if script != grid.PAGE_SIZE_SCRIPT:
                    raise AssertionError("Unexpected page evaluation")
                selected = self.selected
                return {
                    "pagerIndex": 0, "page_size_control_found": True,
                    "total": 11, "page": 1,
                    "effectiveSize": 100 if selected else 10,
                    "inputSize": 100 if selected else 10,
                    "dropdownOpened": self.opened and not selected,
                    "options": [10, 20, 30, 50, 100, 200, 500, 1000, 2000],
                    "rangeStart": 1, "rangeEnd": 11 if selected else 10,
                    "rows": 11 if selected else 10, "spinner": False,
                }

        page = Page()
        self.assertTrue(grid._select_100_per_page(page, lambda: False))
        self.assertEqual(page.keys, ["Home", "ArrowDown", "ArrowDown", "ArrowDown", "ArrowDown", "Enter"])

    def test_dataframe_rules_and_milestones(self):
        frame = grid.parse(snapshot())
        self.assertEqual(len(txl.process_bh_file(frame)), 1)
        for age, expected in ((119, 0), (120, 1), (179, 1), (180, 2), (240, 3), (300, 4)):
            frame = grid.parse(snapshot(status="Đang điều hành xử lý"))
            frame["ngay_bh"] = (datetime.now() - timedelta(minutes=age)).strftime("%d/%m/%Y %H:%M:%S")
            _, progress = txl.get_operational_alert_data(frame)
            self.assertEqual(int(progress.iloc[0]["alert_round"]) if len(progress) else 0, expected)
        with tempfile.TemporaryDirectory() as folder, patch.object(app, "APP_DATA", Path(folder)), patch.object(app, "IN_PROGRESS_ALERT_STATE_PATH", Path(folder) / "state.json"):
            pending, milestone = app._get_new_in_progress_alerts(progress, "123")
            app._record_in_progress_alerts(milestone)
            self.assertEqual(len(app._get_new_in_progress_alerts(progress, "123")[0]), 0)
            self.assertEqual(len(app._get_new_in_progress_alerts(progress, "456")[0]), 1)

    def test_otp_authorization_challenge_and_age(self):
        update = {"message": {"chat": {"id": 123, "type": "private"}, "from": {"id": 123},
                              "date": 100, "text": "410183"}}
        self.assertEqual(login.extract_otp(update, {"123"}, "abcdef01", 100), "410183")
        update["message"]["text"] = "/otp abcdef01 012345"
        self.assertEqual(login.extract_otp(update, {"123"}, "abcdef01", 100), "012345")
        self.assertIsNone(login.extract_otp(update, {"456"}, "abcdef01", 100))
        self.assertIsNone(login.extract_otp(update, {"123"}, "abcdef02", 100))
        self.assertIsNone(login.extract_otp(update, {"123"}, "abcdef01", 101))
        update["message"]["chat"]["type"] = "group"
        self.assertIsNone(login.extract_otp(update, {"123"}, "abcdef01", 100))

    def test_webhook_untouched_and_no_token_in_error(self):
        with patch.object(login, "telegram_call", return_value={"url": "https://other-service"}) as call:
            with self.assertRaises(login.LoginError):
                login.wait_for_otp("secret", ["123"], lambda: False)
            self.assertEqual(call.call_count, 1)
        with patch.object(login.requests, "post", side_effect=login.requests.RequestException("secret URL")):
            with self.assertRaises(login.LoginError) as exc:
                login.telegram_call("secret", "getUpdates", {})
            self.assertNotIn("secret", str(exc.exception))

    def test_zalo_private_chat_discovery_and_plain_text_send(self):
        class Response:
            ok = True
            status_code = 200
            def __init__(self, payload):
                self.payload = payload
            def json(self):
                return self.payload
        session = Mock()
        session.post.return_value = Response({"ok": True, "result": {"message": {
            "chat": {"id": "zalo.chat.1", "type": "private"},
            "from": {"display_name": "Nhân sự A"}, "text": "Xin chào bot",
        }}})
        chats = zalo_bot.fetch_private_chats("secret", session=session)
        self.assertEqual(chats, [{"id": "zalo.chat.1", "name": "Nhân sự A"}])
        self.assertIn("getUpdates", session.post.call_args.args[0])

        session.post.return_value = Response({"ok": True, "result": {"message_id": "m1"}})
        result = zalo_bot.send_message("secret", "zalo.chat.1", "<b>Phiếu</b> &amp; nội dung", session=session)
        self.assertEqual(result, {"message_id": "m1"})
        sent = session.post.call_args.kwargs["json"]
        self.assertEqual(sent, {"chat_id": "zalo.chat.1", "text": "Phiếu\n& nội dung"})

        session.post.side_effect = zalo_bot.requests.ConnectionError(
            "request to https://bot-api.zaloplatforms.com/botsecret/getUpdates failed"
        )
        with self.assertRaises(RuntimeError) as error:
            zalo_bot.fetch_private_chats("secret", session=session)
        self.assertNotIn("secret", str(error.exception))

    def test_zalo_get_updates_timeout_is_an_empty_poll_only(self):
        class Response:
            ok = False
            status_code = 200
            def __init__(self, payload):
                self.payload = payload
            def json(self):
                return self.payload

        session = Mock()
        session.post.return_value = Response({"ok": False, "description": "Request timeout"})
        self.assertEqual(zalo_bot.fetch_updates("secret", session=session), [])

        session.post.return_value = Response({"ok": False, "description": "Unauthorized"})
        with self.assertRaisesRegex(RuntimeError, "Unauthorized"):
            zalo_bot.fetch_updates("secret", session=session)

    def test_zalo_id_lookup_starts_registration_listener(self):
        instance = app.ATSApp.__new__(app.ATSApp)
        instance.worker = None
        instance.zalo_token = Mock()
        instance.zalo_token.get.return_value = "test-token"
        instance._start_zalo_registration_listener = Mock()
        instance.write_log = Mock()
        with patch.object(app.credential_store, "save_secret"), \
             patch.object(app.threading, "Thread") as thread:
            app.ATSApp._fetch_zalo_chats_for_staff(instance, "Nhân sự A", None, False)
        self.assertEqual(instance._zalo_token_for_alerts, "test-token")
        instance._start_zalo_registration_listener.assert_called_once_with("test-token")
        thread.return_value.start.assert_called_once_with()

    def test_zalo_registration_command_is_explicit_and_private(self):
        self.assertEqual(zalo_bot.registration_name("Đỗ Đức Anh đăng ký nhận cảnh báo"), "Đỗ Đức Anh")
        self.assertEqual(zalo_bot.registration_name("Đỗ Đức Anh_Đăng ký nhận cảnh báo"), "Đỗ Đức Anh")
        self.assertEqual(zalo_bot.registration_name("Xin chào bot"), "")
        self.assertEqual(zalo_bot.registration_name("Tôi đã đăng ký"), "")
        details = zalo_bot.private_message_details({
            "update_id": "up1",
            "message": {"date": 1790841600, "text": "Đỗ Đức Anh đăng ký nhận cảnh báo",
                        "chat": {"id": "zalo.chat.1", "type": "private"}},
        })
        self.assertEqual(details, ("zalo.chat.1", "Đỗ Đức Anh đăng ký nhận cảnh báo", 1790841600000, "up1"))
        self.assertIsNone(zalo_bot.private_message_details({
            "message": {"chat": {"id": "group.1", "type": "group"}, "text": "x"}
        }))

    def test_zalo_registration_exact_match_and_local_binding(self):
        instance = app.ATSApp.__new__(app.ATSApp)
        instance._zalo_token_for_alerts = "test-token"
        logs = []
        refreshed = []
        instance.write_log = logs.append
        instance._duty_roster_refresh_callback = lambda: refreshed.append(True)
        instance.after = lambda _delay, callback: callback()
        with tempfile.TemporaryDirectory() as folder:
            roster_path = Path(folder) / "rosters.json"
            contacts_path = Path(folder) / "zalo.json"
            roster.save(roster_path, {"2026-10": {"people": {
                "VNPT001": {"name": "Đỗ Đức Anh", "shifts": {}},
            }}})
            with patch.object(app, "ROSTER_PATH", roster_path), \
                 patch.object(app, "ZALO_CONTACTS_PATH", contacts_path), \
                 patch.object(app.zalo_bot, "send_message") as send:
                instance._register_zalo_staff("Đỗ Đức Anh", "zalo.chat.1")
                self.assertEqual(roster.load(contacts_path), {roster.normalize("Đỗ Đức Anh"): "zalo.chat.1"})
                send.assert_called_once()
                self.assertTrue(any("Đã tự động gán" in line for line in logs))
                self.assertEqual(refreshed, [True])
                instance._register_zalo_staff("Đỗ Đức", "zalo.chat.2")
                self.assertNotIn(roster.normalize("Đỗ Đức"), roster.load(contacts_path))

    def test_zalo_manual_assignment_skips_poll_when_staff_already_linked(self):
        instance = app.ATSApp.__new__(app.ATSApp)
        instance.worker = None
        instance.zalo_token = type("Value", (), {"get": lambda _self: "test-token"})()
        refreshed = []
        with patch.object(roster, "load", return_value={roster.normalize("Phạm Anh Huy"): "zalo.chat.1"}), \
             patch.object(app.messagebox, "showinfo") as showinfo, \
             patch.object(instance, "_start_zalo_registration_listener") as start_listener, \
             patch.object(app.threading, "Thread") as thread:
            instance._fetch_zalo_chats_for_staff("Phạm Anh Huy", None, lambda: refreshed.append(True))
        self.assertEqual(refreshed, [True])
        showinfo.assert_called_once()
        start_listener.assert_not_called()
        thread.assert_not_called()

    def test_zalo_id_import_optional_column_and_duty_routing(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "contacts.xlsx"
            from openpyxl import Workbook
            book = Workbook()
            sheet = book.active
            sheet.append(["Họ và tên", "ID Telegram", "Zalo Chat ID"])
            sheet.append(["Nhân sự A", 123456, "zalo.chat.1"])
            book.save(path)
            book.close()
            self.assertEqual(roster.read_zalo_contacts(path), {"nhân sự a": "zalo.chat.1"})
        rosters = {"2026-10": {"people": {"a": {"name": "Nhân sự A", "shifts": {"1": "HC"}}}}}
        with patch.object(roster, "on_duty", return_value=[{"name": "Nhân sự A", "staff_id": "staff-a"}]):
            self.assertEqual(app._zalo_duty_recipients(rosters, {"nhân sự a": "zalo.chat.1"}), ["zalo.chat.1"])
            with self.assertRaisesRegex(ValueError, "Thiếu Zalo Chat ID"):
                app._zalo_duty_recipients(rosters, {})
            missing = []
            self.assertEqual(
                app._zalo_duty_recipients(rosters, {}, allow_missing=True, missing_names=missing), []
            )
            self.assertEqual(missing, ["Nhân sự A"])

    def test_zalo_group_updates_are_discovered(self):
        updates = [
            {"message": {"chat": {"id": "group-1", "type": "group", "title": "Nhóm thử"}}},
            {"message": {"chat": {"id": "private-1", "type": "private", "name": "Cá nhân"}}},
        ]
        self.assertEqual(
            zalo_bot.group_chats_from_updates(updates),
            [{"id": "group-1", "name": "Nhóm thử"}],
        )

    def test_zalo_registration_listener_preserves_group_updates_for_id_lookup(self):
        instance = app.ATSApp.__new__(app.ATSApp)
        instance._zalo_group_chats = {}
        instance._zalo_group_chats_lock = app.threading.Lock()
        instance._zalo_group_seen = app.threading.Event()
        instance._zalo_registration_seen = set()
        instance._zalo_registration_started_at_ms = 0
        instance._register_zalo_staff = Mock()

        update = {"message": {"chat": {"id": "group-1", "type": "group", "title": "Nhóm thử"}}}
        app.ATSApp._process_zalo_registration_updates(instance, [update])

        self.assertEqual(
            app.ATSApp._zalo_groups_from_updates(instance),
            [{"id": "group-1", "name": "Nhóm thử"}],
        )
        self.assertTrue(instance._zalo_group_seen.is_set())
        instance._register_zalo_staff.assert_not_called()

    def test_zalo_group_alert_runs_with_incomplete_staff_ids(self):
        frame = grid.parse(snapshot())
        frame["sla_bh"] = "10 ≤ TXL ≤ 20"
        frame["DONVI"] = "P.HTKH MB"
        instance = Mock()
        instance._alert_destination = "Cả hai"
        instance._alert_channel = "Zalo"
        instance._zalo_token_for_alerts = "bot-secret"
        instance.zalo_group_chat_id = Mock()
        instance.zalo_group_chat_id.get.return_value = "group-1"
        instance.stop_requested = False
        instance.service_preferences = {}
        instance.write_log = Mock()
        empty = pd.DataFrame()

        def partial_recipients(_rosters, _contacts, allow_missing=False, missing_names=None, **_kwargs):
            self.assertTrue(allow_missing)
            missing_names.extend(["Nhân sự chưa có ID"])
            return ["private-1"]

        with patch.object(app, "_zalo_duty_recipients", side_effect=partial_recipients), \
             patch.object(app.duty_roster, "load", return_value={}), \
             patch.object(app.zalo_bot, "send_message") as send_zalo, \
             patch.object(app.txl, "build_bh_message", return_value="Phiếu test"), \
             patch.object(app.time, "sleep"):
            app.ATSApp._send_routed_alerts(
                instance, frame, empty, empty, {"staff_labels": [], "staff_links": []}
            )
        self.assertEqual([call.args[1] for call in send_zalo.call_args_list], ["group-1", "private-1"])
        self.assertTrue(any("chưa có Chat ID" in call.args[0] for call in instance.write_log.call_args_list))

    def test_zalo_channel_sends_only_to_on_duty_private_chat(self):
        frame = grid.parse(snapshot())
        frame["sla_bh"] = "10 ≤ TXL ≤ 20"
        frame["DONVI"] = "P.HTKH MB"
        instance = Mock()
        instance._alert_destination = "Nhân sự trong ca"
        instance._alert_channel = "Zalo"
        instance._zalo_token_for_alerts = "bot-secret"
        instance.zalo_group_chat_id = Mock()
        instance.zalo_group_chat_id.get.return_value = "group-1"
        instance.stop_requested = False
        instance.service_preferences = {}
        empty = pd.DataFrame()
        with patch.object(app, "_zalo_duty_recipients", return_value=["zalo.chat.1"]), \
             patch.object(app.zalo_bot, "send_message") as send_zalo, \
             patch.object(app.txl, "send_telegram_message") as send_telegram, \
             patch.object(app.txl, "build_bh_message", return_value="<b>Phiếu kiểm tra</b>") as build_message, \
             patch.object(app.time, "sleep"):
            app.ATSApp._send_routed_alerts(
                instance, frame, empty, empty, {"staff_labels": [], "staff_links": []}
            )
        send_zalo.assert_called_once()
        self.assertEqual(send_zalo.call_args.args[:2], ("bot-secret", "zalo.chat.1"))
        send_telegram.assert_not_called()
        instance._alert_channel = "Cả hai"
        instance._alert_destination = "Cả hai"
        with patch.dict(os.environ, {"TXL_TELEGRAM_GROUP_CHAT_ID": "-123"}), \
             patch.object(app, "_zalo_duty_recipients", return_value=["zalo.chat.1"]), \
             patch.object(app.duty_roster, "recipients", return_value=["tg-staff"]), \
             patch.object(app.zalo_bot, "send_message") as both_zalo, \
             patch.object(app.txl, "send_telegram_message") as both_telegram, \
             patch.object(app.txl, "build_bh_message", return_value="<b>Phiếu kiểm tra</b>"), \
             patch.object(app.time, "sleep"):
            app.ATSApp._send_routed_alerts(
                instance, frame, empty, empty, {"staff_labels": [], "staff_links": []}
            )
        self.assertEqual(both_zalo.call_count, 2)
        self.assertTrue({call.kwargs.get("chat_id") for call in both_telegram.call_args_list}.issuperset({"-123", "tg-staff"}))

    def test_delivery_failure_does_not_consume_milestone(self):
        frame = grid.parse(snapshot(status="Đang điều hành xử lý"))
        frame["ngay_bh"] = (datetime.now() - timedelta(minutes=130)).strftime("%d/%m/%Y %H:%M:%S")
        completion, progress = txl.get_operational_alert_data(frame)
        instance = Mock()
        instance._alert_destination = "Group"
        instance._telegram_token_for_alerts = "fake"
        instance.stop_requested = False
        with tempfile.TemporaryDirectory() as folder, patch.object(app, "APP_DATA", Path(folder)), patch.object(app, "IN_PROGRESS_ALERT_STATE_PATH", Path(folder) / "state.json"), patch.dict(os.environ, {"TXL_TELEGRAM_GROUP_CHAT_ID": "-123"}), patch.object(txl, "send_telegram_message", side_effect=RuntimeError("offline")):
            with self.assertRaises(RuntimeError):
                app.ATSApp._send_routed_alerts(
                    instance, pd.DataFrame(), completion, progress,
                    {"staff_labels": [], "staff_links": []},
                )
            self.assertEqual(len(app._get_new_in_progress_alerts(progress)[0]), 1)

    def test_browser_grid_metadata(self):
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            source = snapshot()
            html = '<div class="e-grid"><table><thead><tr>' + ''.join(f'<th>{h}</th>' for h in source["headers"]) + '</tr></thead><tbody><tr class="e-row" data-uid="1"><td></td></tr></tbody></table><div class="e-pager">Tổng cộng 1 bản ghi</div></div>'
            page.set_content(html)
            page.evaluate("s => {document.querySelector('.e-grid').ej2_instances=[{getColumns:()=>s.headers.map((h,n)=>({headerText:h,field:'f'+n})),getRowObjectFromUID:()=>({data:Object.fromEntries(s.headers.map((h,n)=>['f'+n,s.rows[0][h]]))}),pageSettings:{currentPage:1}}];}", source)
            with patch.object(grid, "_select_100_per_page", return_value=False):
                self.assertEqual(grid.read(page).iloc[0]["ma_bh"], "BH_1")
            browser.close()

    def test_local_gui_controls(self):
        # Tk is only exercised on the requested preview platform.
        if app.sys.platform != "darwin":
            self.skipTest("macOS UI preview")
        with patch.object(app, "_load_settings", return_value={}), patch.object(app.credential_store, "load_secret", return_value=""), patch.object(app.updater, "can_self_update", return_value=False):
            instance = app.ATSApp()
            try:
                instance.withdraw()
                instance._preview_settings()
                instance.update_idletasks()
                windows = [w for w in instance.winfo_children() if isinstance(w, app.tk.Toplevel)]
                self.assertEqual(len(windows), 1)
                self.assertEqual(instance.otp_selector.get(), 'input[placeholder="Mã OTP"]')
                windows[0].destroy()
            finally:
                instance.destroy()

    def test_monthly_roster_grid_and_cell_edit(self):
        if app.sys.platform != "darwin":
            self.skipTest("macOS UI preview")
        data = {
            "2026-10": {"people": {"NV1": {"name": "Nhân sự A", "shifts": {"1": "HC", "2": "Đêm", "31": "HC"}},
                                     "NV2": {"name": "Nhân sự B", "shifts": {"1": "Đêm"}}}},
            "2026-02": {"people": {"NV1": {"name": "Nhân sự A", "shifts": {"28": "Đêm"}}}},
        }
        contacts = {roster.normalize("Nhân sự A"): "123456789"}
        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)
        with patch.object(app, "_load_settings", return_value={}), patch.object(app.credential_store, "load_secret", return_value=""), patch.object(app.updater, "can_self_update", return_value=False), patch.object(roster, "load", side_effect=lambda path: data if path == app.ROSTER_PATH else contacts if path == app.CONTACTS_PATH else {}), patch.object(roster, "save") as save:
            instance = app.ATSApp()
            try:
                instance.withdraw()
                instance.roster_month.set("2026-10")
                instance._preview_settings()
                instance.update()
                window = next(w for w in instance.winfo_children() if isinstance(w, app.tk.Toplevel))
                widgets = list(descendants(window))
                tables = [w for w in widgets if isinstance(w, app.ttk.Treeview)]
                days = next(w for w in tables if len(w["columns"]) == 31)
                names = next(w for w in tables if tuple(w["columns"]) == ("telegram", "zalo"))
                self.assertEqual(len(days.get_children()), 2)
                self.assertEqual(names.item("NV1", "text"), "Nhân sự A")
                self.assertEqual(names.set("NV1", "telegram"), "123456789")
                self.assertEqual(names.set("NV2", "telegram"), "Chưa có ID")
                self.assertEqual(days.set("NV1", "2"), "Đêm")
                self.assertEqual(days.set("NV1", "3"), "–")
                self.assertEqual(days.heading("1", "text"), "01 T5")
                days.selection_set("NV1")
                instance.update()
                day_picker = next(w for w in widgets if isinstance(w, app.ttk.Combobox) and len(w["values"]) == 31)
                day_picker.set("2")
                day_picker.event_generate("<<ComboboxSelected>>")
                shift_picker = next(w for w in widgets if isinstance(w, app.ttk.Combobox) and "Nghỉ" in w["values"])
                self.assertEqual(shift_picker.get(), "Đêm")
                shift_picker.set("HC")
                next(w for w in widgets if isinstance(w, app.ttk.Button) and w["text"] == "Lưu đổi ca").invoke()
                self.assertEqual(data["2026-10"]["people"]["NV1"]["shifts"]["2"], "HC")
                self.assertEqual(data["2026-10"]["people"]["NV2"]["shifts"]["1"], "Đêm")
                save.assert_called_once()
                days.xview_moveto(1)
                instance.update()
                self.assertEqual(names.xview()[0], 0)
                with patch.object(app.filedialog, "askopenfilename", return_value="contacts.xlsx"), patch.object(roster, "read_contacts", return_value={roster.normalize("Nhân sự A"): "123456789", roster.normalize("Nhân sự B"): "987654321"}), patch.object(roster, "read_zalo_contacts", return_value={}), patch.object(roster, "save", side_effect=lambda path, value: contacts.update(value)):
                    next(w for w in widgets if isinstance(w, app.ttk.Button) and w["text"] == "Import ID Telegram").invoke()
                self.assertEqual(names.set("NV2", "telegram"), "987654321")
                self.assertEqual(days.set("NV1", "2"), "HC")
                instance.roster_month.set("2026-02")
                next(w for w in widgets if isinstance(w, app.ttk.Button) and w["text"] == "Xem tháng").invoke()
                self.assertEqual(len(days["columns"]), 28)
                self.assertEqual(days.set("NV1", "28"), "Đêm")
                window.destroy()
            finally:
                instance.destroy()


if __name__ == "__main__":
    unittest.main()
