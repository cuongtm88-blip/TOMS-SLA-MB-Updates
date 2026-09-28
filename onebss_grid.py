"""Read the OneBSS result grid by metadata, with strict pagination checks."""
import unicodedata
import re
import time

import pandas as pd

FIELDS = {
    "Tỉnh": "tentinh", "Mã thuê bao": "ma_tb", "Tên thuê bao": "ten_tb",
    "Loại hình TB": "loaihinh_tb", "Đơn vị nhận": "ten_dv", "Đơn vị nhân": "ten_dv",
    "Đơn vị xử lí": "ten_dv_xl", "Đơn vị xử lý": "ten_dv_xl",
    "Đơn vị đang thực hiện": "ten_dv_dang_th", "Ngày báo hỏng": "ngay_bh",
    "SLA": "sla", "Người giữ phiếu": "ma_nd", "SĐT BH": "dienthoai_bh",
    "Điện thoại liên hệ": "dienthoai_lh", "Trạng thái bảo hỏng": "trangthai_bh",
    "Trạng thái báo hỏng": "trangthai_bh", "Nhân viên": "ten_nv",
    "Nội dung hỏng": "ghichu_hong", "Mã báo hỏng": "ma_bh",
    "Kênh tiếp nhận": "kenh_tn", "Địa chỉ lắp đặt": "diachi_ld",
    "Máy cập nhật": "may_cn", "Ngày cập nhật": "ngay_cn",
    "Người cập nhật": "nguoi_cn", "Người báo hỏng": "nguoi_bao_hong",
    "Quy trình": "ten_quytrinh", "Trạng thái xử lý": "ten_trangthai",
}
REQUIRED = {"ma_bh", "loaihinh_tb", "ten_dv", "ten_nv", "ngay_bh",
            "trangthai_bh", "ten_dv_xl", "ten_dv_dang_th", "tentinh", "diachi_ld"}

SCRIPT = r"""() => {
 const norm = s => (s || '').normalize('NFC').trim().replace(/\s+/g,' ').toLowerCase();
 const root = [...document.querySelectorAll('.e-grid')].find(e =>
   [...e.querySelectorAll('th')].some(h => norm(h.textContent).includes('mã báo hỏng')));
 if (!root) return null;
 const instance = root.ej2_instances?.find(i => typeof i.getColumns === 'function');
 const columns = instance ? instance.getColumns().filter(c => c.headerText).map(c =>
   ({header:c.headerText.trim(),field:c.field})) :
   [...root.querySelectorAll('th')].map(c => ({header:c.textContent.trim()}));
 const tables = [...root.querySelectorAll('table')].map(table => [...table.querySelectorAll('tr.e-row')]
   .filter(r => r.closest('.e-grid') === root));
 const domRows = tables.sort((a,b) => b.length-a.length)[0] || [];
 const rows = domRows.map(r => {
   const raw = instance?.getRowObjectFromUID(r.getAttribute('data-uid'))?.data;
   const cells = [...r.querySelectorAll('td')]; const result = {};
   columns.forEach((c,n) => {
     if (raw && c.field && Object.prototype.hasOwnProperty.call(raw,c.field)) result[c.header]=raw[c.field];
     else {
       const header = [...root.querySelectorAll('th')].find(h => norm(h.textContent) === norm(c.header));
       const ix=header?.getAttribute('aria-colindex');
       const cell=ix ? cells.find(e => e.getAttribute('aria-colindex') === ix) :
         cells.length === columns.length ? cells[n] : null;
       if(cell) result[c.header]=cell.innerText;
     }
   }); return result;
 });
 const pager=root.querySelector('.e-pager') || root.parentElement.querySelector('.e-pager');
 const totalText=(pager?.innerText || '').match(/Tổng cộng\s*([\d.,]+)\s*bản ghi/i);
 const total=totalText ? Number(totalText[1].replace(/\D/g,'')) : instance?.pageSettings?.totalRecordsCount;
 const page=instance?.pageSettings?.currentPage || Number(pager?.querySelector('.e-currentitem')?.textContent);
 return {headers:columns.map(c=>c.header),rows,total,page};
}"""

PAGE_SIZE_SCRIPT = r"""(allowedSizes) => {
 const norm = s => (s || '').normalize('NFC').trim().replace(/\s+/g,' ').toLowerCase();
 const visible = e => { if (!e) return false; const r=e.getBoundingClientRect(); const s=getComputedStyle(e); return s.display!=='none' && s.visibility!=='hidden' && r.width>0 && r.height>0; };
 const roots=[...document.querySelectorAll('.e-grid,[role="grid"]')];
 const index=roots.findIndex(e => { const h=[...e.querySelectorAll('th,[role="columnheader"]')].map(x=>norm(x.textContent)); return h.some(x=>x.includes(norm('Mã thuê bao'))) && h.some(x=>x.includes(norm('Mã báo hỏng'))); });
 const root=roots[index]; if (!root) return null;
 const pagers=[...root.querySelectorAll('.e-pager'), ...(root.parentElement?[...root.parentElement.querySelectorAll('.e-pager')]:[])];
 const pager=pagers.find(e => { const t=norm(e.innerText); return e.querySelector('.e-pagesizes') && (t.includes(norm('Tổng cộng')) || t.includes(norm('bản ghi trên trang'))); });
 if (!pager) return null;
 const pagerIndex=[...document.querySelectorAll('.e-pager')].indexOf(pager);
 const sizes=pager.querySelector('.e-pagesizes'); const dropdown=sizes?.querySelector('.e-pagerdropdown');
 const listbox=dropdown?.querySelector('.e-input-group.e-ddl,[role="listbox"]');
 const input=dropdown?.querySelector('input.e-dropdownlist,input'); const select=dropdown?.querySelector('select.e-ddl-hidden,select');
 const controlValue=input?.value || select?.value || '';
 const footer=(pager.innerText||'').replace(/\s+/g,' ').trim();
 const totalMatch=footer.match(/Tổng cộng\s*([\d.,\s\u00a0]+?)\s*bản ghi/i);
 const total=totalMatch ? Number(totalMatch[1].replace(/\D/g,'')) : 0;
 const range=footer.match(/Đang hiển thị bản ghi số\s*([\d.,]+)\s*đến\s*([\d.,]+)/i);
 const rangeStart=range ? Number(range[1].replace(/\D/g,'')) : 0, rangeEnd=range ? Number(range[2].replace(/\D/g,'')) : 0;
 const controlSize=Number(String(controlValue).match(/^\s*(\d+)\s*$/)?.[1] || 0);
 const effectiveSize=rangeStart===1 && rangeEnd<total ? rangeEnd : controlSize;
 const instance=root.ej2_instances?.find(i=>i.pageSettings && typeof i.getColumns==='function');
 const page=Number.isInteger(instance?.pageSettings?.currentPage) ? instance.pageSettings.currentPage : Number(pager.querySelector('.e-currentitem,[aria-current="page"]')?.textContent || 0);
 const tables=[...root.querySelectorAll('table')].map(t=>[...t.querySelectorAll('tr.e-row')].filter(r=>r.closest('.e-grid')===root));
 const rows=tables.sort((a,b)=>b.length-a.length)[0] || [];
 const spinner=root.querySelector('.e-spinner-pane.e-spin-show');
 const refs=[...(input?.getAttribute('aria-controls')||'').split(/\s+/),...(input?.getAttribute('aria-owns')||'').split(/\s+/),...(listbox?.getAttribute('aria-controls')||'').split(/\s+/),...(listbox?.getAttribute('aria-owns')||'').split(/\s+/)].filter(Boolean);
 const associated=refs.map(id=>document.getElementById(id)).filter(Boolean).map(e=>e.closest('.e-popup')||e).filter((e,i,a)=>a.indexOf(e)===i && visible(e));
 const open=[...document.querySelectorAll('.e-popup.e-popup-open')].filter(visible);
 const numeric=pop=>[...pop.querySelectorAll('*')].filter(e=>visible(e)&&!e.children.length).map(e=>(e.innerText||e.textContent||'').trim()).map(t=>/^\d+$/.test(t)?Number(t):null).filter(n=>allowedSizes.includes(n));
 const popup=associated[0] || open.find(e=>numeric(e).length) || null;
 const options=popup?[...new Set(numeric(popup))].sort((a,b)=>a-b):[];
 const popupContent=refs.map(id=>document.getElementById(id)).find(e=>e&&(e.closest('.e-popup')||e)===popup);
 const currentPageSize=rangeStart===1 && rangeEnd>0 && rangeEnd<total ? rangeEnd : controlSize;
 return {index,pagerIndex,page_size_control_found:Boolean(sizes&&dropdown&&listbox&&input&&visible(sizes)&&visible(listbox)),
  controlValue,inputSize:controlSize,configuredSize:Number.isInteger(instance?.pageSettings?.pageSize)?instance.pageSettings.pageSize:null,
  effectiveSize,page,currentPageSize,rangeStart,rangeEnd,total,rows:rows.length,spinner:Boolean(spinner),footer,
  dropdownOpened:Boolean(popup),options,popupId:popupContent?.id||popup?.id||null,
  dropdownId:input?.id||null,
  nativeOptions:select?[...select.options].map(o=>String(o.textContent||'').trim()):[]};
}"""


def normalize(value):
    return " ".join(unicodedata.normalize("NFC", str(value or "")).casefold().split())


def parse(snapshot):
    if not snapshot:
        raise RuntimeError("Không tìm thấy bảng Danh Sách Kết quả")
    aliases = {normalize(k): v for k, v in FIELDS.items()}
    mapping = {}
    for header in snapshot["headers"]:
        field = aliases.get(normalize(header))
        if field:
            if field in mapping:
                raise RuntimeError("Bảng có cột ánh xạ trùng: " + field)
            mapping[field] = header
    missing = REQUIRED - mapping.keys()
    if missing:
        raise RuntimeError("Bảng thiếu cột cảnh báo: " + ", ".join(sorted(missing)))
    records = []
    for row in snapshot["rows"]:
        if any(header not in row for field, header in mapping.items() if field in REQUIRED):
            raise RuntimeError("Bảng thiếu giá trị ô; không gửi cảnh báo dữ liệu chưa đầy đủ")
        record = {field: row.get(header, "") for field, header in mapping.items()}
        record["ma_bh"] = str(record["ma_bh"] or "").strip()
        if not record["ma_bh"]:
            raise RuntimeError("Bảng có phiếu thiếu mã báo hỏng")
        if normalize(record["ten_dv_xl"]) == "trống":
            record["ten_dv_xl"] = ""
        for field in ("ngay_bh", "ngay_cn"):
            value = record.get(field)
            if value:
                parsed = pd.to_datetime(value, dayfirst=not bool(re.match(r"^\d{4}-", str(value))), errors="coerce")
                if pd.isna(parsed):
                    if field == "ngay_bh":
                        raise RuntimeError("Ngày báo hỏng không hợp lệ; không gửi cảnh báo sai thời gian")
                else:
                    if parsed.tzinfo is not None:
                        parsed = parsed.tz_convert("Asia/Ho_Chi_Minh").tz_localize(None)
                    record[field] = parsed.strftime("%d/%m/%Y %H:%M:%S")
            elif field == "ngay_bh":
                raise RuntimeError("Phiếu thiếu ngày báo hỏng")
        records.append(record)
    return pd.DataFrame(records, columns=list(mapping))


def _ticket_ids(snapshot):
    header = next((item for item in snapshot.get("headers", [])
                   if normalize(item) == normalize("Mã báo hỏng")), None)
    if header is None:
        raise RuntimeError("Bảng không có cột Mã báo hỏng để kiểm tra phân trang")
    return tuple(normalize(row.get(header)) for row in snapshot.get("rows", []))


def _wait_for_page_change(page, previous_ids, stopped):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if stopped():
            raise RuntimeError("Đã dừng bởi người dùng")
        if getattr(page, "is_closed", lambda: False)():
            raise RuntimeError("Trình duyệt đã đóng khi OneBSS đang chuyển trang")
        current = page.evaluate(SCRIPT)
        if current and current.get("rows") and _ticket_ids(current) != previous_ids:
            return current
        page.wait_for_timeout(250)
    raise RuntimeError("OneBSS không đổi danh sách phiếu sau khi chuyển trang")


def _select_100_per_page(page, stopped):
    """Set the OneBSS grid's visible page-size control to 100 and confirm it."""
    allowed_sizes = [10, 20, 30, 50, 100, 200, 500, 1000, 2000]
    state = page.evaluate(PAGE_SIZE_SCRIPT, allowed_sizes)
    if not state or state.get("pagerIndex", -1) < 0 or not state.get("page_size_control_found"):
        raise RuntimeError("Không tìm thấy phân trang của bảng Danh Sách Kết quả")

    def current_state():
        return page.evaluate(PAGE_SIZE_SCRIPT, allowed_sizes)

    def wait_until(predicate, timeout=30):
        deadline = time.monotonic() + timeout
        latest = None
        while time.monotonic() < deadline:
            if stopped():
                raise RuntimeError("Đã dừng bởi người dùng")
            if getattr(page, "is_closed", lambda: False)():
                raise RuntimeError("Trình duyệt đã đóng khi đổi số bản ghi trên trang")
            latest = current_state()
            if latest and predicate(latest):
                return latest
            page.wait_for_timeout(250)
        return latest

    if state["page"] != 1:
        first_page = page.locator(".e-pager").nth(state["pagerIndex"]).locator(
            ".e-firstpage:not(.e-firstpagedisabled)"
        )
        if first_page.count():
            first_page.click(timeout=10000)
        state = wait_until(lambda current: current.get("page") == 1, timeout=10) or state

    expected_rows = min(state["total"], 100)

    def applied(current):
        empty = current.get("total") == 0 and current.get("rows") == 0
        populated = (
            current.get("rangeStart") == 1
            and current.get("rangeEnd") == expected_rows
            and current.get("rows") == expected_rows
        )
        return (
            current.get("inputSize") == 100
            and current.get("page") == 1
            and (empty or populated)
            and not current.get("spinner")
        )

    if state["effectiveSize"] == 100 and state["inputSize"] == 100:
        return bool(wait_until(applied, timeout=30))

    pager = page.locator(".e-pager").nth(state["pagerIndex"])
    page_sizes = pager.locator(".e-pagesizes").first
    dropdown = page_sizes.locator(".e-pagerdropdown").first
    page_sizes.wait_for(state="visible", timeout=10000)
    dropdown.wait_for(state="visible", timeout=10000)

    # The pager uses a readonly EJ2 input. Setting its hidden native select
    # only changes the DOM value, not the grid's pageSettings; drive the real
    # EJ2 control exactly as the proven ATS_TXL flow does.
    trigger = dropdown.locator(".e-input-group-icon:visible, .e-ddl-icon:visible").first
    if trigger.count():
        trigger.click(timeout=10000)
    else:
        dropdown.click(timeout=10000)

    open_state = wait_until(
        lambda current: current.get("dropdownOpened") and 100 in current.get("options", []),
        timeout=10,
    )
    if not open_state or not open_state.get("dropdownOpened") or 100 not in open_state.get("options", []):
        return False

    control = dropdown.locator("input.e-dropdownlist, input").first
    control.wait_for(state="visible", timeout=10000)
    target_index = open_state["options"].index(100)
    control.press("Home", timeout=10000)
    for _ in range(target_index):
        control.press("ArrowDown", timeout=10000)
    control.press("Enter", timeout=10000)

    return bool(wait_until(applied, timeout=30))


def read(page, stopped=lambda: False, logger=None):
    if not _select_100_per_page(page, stopped) and logger:
        logger("OneBSS chưa xác nhận được 100 bản ghi/trang; sẽ tự đọc lần lượt các trang để không bỏ sót phiếu.")
    snapshot = page.evaluate(SCRIPT)
    if not snapshot or not isinstance(snapshot.get("total"), int) or snapshot["total"] < 0:
        raise RuntimeError("Không xác định được tổng số phiếu OneBSS")
    total = snapshot["total"]
    if snapshot.get("page") not in (1, None, 0):
        previous_ids = _ticket_ids(snapshot)
        page.locator('.e-pager .e-firstpage:not(.e-firstpagedisabled)').first.click()
        snapshot = _wait_for_page_change(page, previous_ids, stopped)
    frames, seen = [], set()
    while True:
        if stopped():
            raise RuntimeError("Đã dừng bởi người dùng")
        snapshot = page.evaluate(SCRIPT)
        if snapshot["total"] != total:
            raise RuntimeError("Kết quả OneBSS thay đổi trong khi đọc; cần tìm kiếm lại")
        frame = parse(snapshot)
        keys = frame["ma_bh"].tolist()
        if len(set(keys)) != len(keys) or seen.intersection(keys):
            raise RuntimeError("Phiếu trùng khi phân trang; không gửi dữ liệu sai")
        seen.update(keys)
        frames.append(frame)
        if len(seen) == total:
            return pd.concat(frames, ignore_index=True)
        if not keys or len(seen) > total:
            raise RuntimeError("Số phiếu đọc không khớp tổng OneBSS")
        previous_ids = _ticket_ids(snapshot)
        next_button = page.locator('.e-pager .e-nextpage:not(.e-nextpagedisabled)').first
        next_button.click(timeout=10000)
        _wait_for_page_change(page, previous_ids, stopped)
