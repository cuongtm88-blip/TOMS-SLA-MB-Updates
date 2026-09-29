/** Public, append-focused API for the TOMS SLA service catalog.
 * The spreadsheet stays private. Service/staff labels and opaque staff IDs
 * are exposed; employee names and Telegram chat IDs stay on users' machines.
 * Deploy as owner with
 * anonymous access.
 */
const CATALOG_SPREADSHEET_ID = '1iRv3Hi1fcun2Qxh_Sawpw8qOEQqwJx0z_tdCqjNFz0s';
const SHEETS = {
  services: 'Dịch vụ',
  labels: 'Nhãn',
  links: 'Nhãn_Dịch_Vụ',
  staff: 'Nhan_su',
  staff_labels: 'Nhan_su_Nhan',
  staff_links: 'Nhan_su_Nhan_Lien_ket'
};
const HEADERS = {
  services: ['service_id', 'ten_dich_vu', 'lan_dau_thay', 'lan_cuoi_thay'],
  labels: ['label_id', 'ten_nhan', 'ngay_tao', 'cap_nhat'],
  links: ['label_id', 'service_id'],
  staff: ['staff_id', 'cap_nhat'],
  staff_labels: ['staff_label_id', 'ten_nhan', 'ngay_tao', 'cap_nhat'],
  staff_links: ['staff_label_id', 'staff_id']
};
const MAX_SERVICES = 2500;
const MAX_LABELS = 500;
const MAX_LINKS = 20000;
const MAX_STAFF = 5000;
const MAX_STAFF_LABELS = 500;
const MAX_STAFF_LINKS = 25000;
const MAX_WRITES_PER_MINUTE = 40;
const MAX_WRITES_PER_DAY = 1500;

function doGet() {
  try {
    const cache = CacheService.getScriptCache();
    const cached = cache.get('catalog_v2');
    if (cached) return output_(JSON.parse(cached));
    const result = readCatalog_();
    cache.put('catalog_v2', JSON.stringify(result), 60);
    return output_(result);
  } catch (error) {
    return output_({ok: false, error: safeMessage_(error)});
  }
}

function doPost(event) {
  let lock;
  try {
    const raw = event && event.postData && event.postData.contents || '';
    if (!raw || raw.length > 20000) throw new Error('Yêu cầu trống hoặc vượt giới hạn');
    const request = JSON.parse(raw);
    if (!request || typeof request !== 'object' || Array.isArray(request)) throw new Error('Yêu cầu không hợp lệ');
    lock = LockService.getScriptLock();
    if (!lock.tryLock(10000)) throw new Error('Danh mục đang được cập nhật; vui lòng thử lại');
    checkWriteLimit_();
    let result;
    switch (request.action) {
      case 'sync_services': result = syncServices_(request.services); break;
      case 'create_label': result = createLabel_(request.name); break;
      case 'rename_label': result = renameLabel_(request.label_id, request.name); break;
      case 'delete_label': result = deleteLabel_(request.label_id); break;
      case 'set_label_services': result = setLabelServices_(request.label_id, request.service_ids); break;
      case 'sync_staff': result = syncStaff_(request.staff); break;
      case 'create_staff_label': result = createStaffLabel_(request.name); break;
      case 'rename_staff_label': result = renameStaffLabel_(request.staff_label_id, request.name); break;
      case 'delete_staff_label': result = deleteStaffLabel_(request.staff_label_id); break;
      case 'set_label_staff': result = setLabelStaff_(request.staff_label_id, request.staff_ids); break;
      default: throw new Error('Thao tác không được hỗ trợ');
    }
    CacheService.getScriptCache().remove('catalog_v2');
    return output_(Object.assign({ok: true}, result || {}));
  } catch (error) {
    return output_({ok: false, error: safeMessage_(error)});
  } finally {
    if (lock && lock.hasLock()) lock.releaseLock();
  }
}

function readCatalog_() {
  const ss = SpreadsheetApp.openById(CATALOG_SPREADSHEET_ID);
  ensureStaffTables_(ss);
  const services = readTable_(ss, 'services');
  const labels = readTable_(ss, 'labels');
  const links = readTable_(ss, 'links');
  const staff = readTable_(ss, 'staff');
  const staffLabels = readTable_(ss, 'staff_labels');
  const staffLinks = readTable_(ss, 'staff_links');
  return {ok: true, services: services, labels: labels, links: links,
    staff: staff, staff_labels: staffLabels, staff_links: staffLinks};
}

function ensureStaffTables_(ss) {
  ['staff', 'staff_labels', 'staff_links'].forEach(key => {
    let sheet = ss.getSheetByName(SHEETS[key]);
    if (!sheet) sheet = ss.insertSheet(SHEETS[key]);
    if (sheet.getLastRow() === 0) sheet.getRange(1, 1, 1, HEADERS[key].length).setValues([HEADERS[key]]);
  });
}

function readTable_(ss, key) {
  const sheet = ss.getSheetByName(SHEETS[key]);
  if (!sheet) throw new Error('Thiếu tab ' + SHEETS[key]);
  const width = HEADERS[key].length;
  const header = sheet.getRange(1, 1, 1, width).getDisplayValues()[0];
  if (header.join('\u001f') !== HEADERS[key].join('\u001f')) {
    throw new Error('Tiêu đề cột không đúng tại tab ' + SHEETS[key]);
  }
  const count = Math.max(0, sheet.getLastRow() - 1);
  if (!count) return [];
  return sheet.getRange(2, 1, count, width).getValues().filter(row => row[0] !== '').map(row => {
    const item = {};
    HEADERS[key].forEach((name, index) => {
      const value = row[index];
      item[name] = value instanceof Date ? value.toISOString() : String(value == null ? '' : value);
    });
    return item;
  });
}

function syncServices_(values) {
  if (!Array.isArray(values) || values.length > 100) throw new Error('Có thể đồng bộ tối đa 100 tên dịch vụ mỗi lần');
  const ss = SpreadsheetApp.openById(CATALOG_SPREADSHEET_ID);
  const rows = readTable_(ss, 'services');
  if (rows.length > MAX_SERVICES) throw new Error('Danh mục đã đạt giới hạn dịch vụ');
  const seen = new Set(rows.map(row => key_(row.ten_dich_vu)));
  const added = [];
  const now = new Date();
  values.forEach(value => {
    const name = clean_(value, 200, 'Tên dịch vụ');
    const key = key_(name);
    if (!seen.has(key)) {
      seen.add(key);
      const service = {
        service_id: Utilities.getUuid(), ten_dich_vu: name,
        lan_dau_thay: now.toISOString(), lan_cuoi_thay: now.toISOString()
      };
      rows.push(service);
      added.push(service);
    }
  });
  if (added.length) writeTable_(ss, 'services', rows);
  return {added: added};
}

function createLabel_(value) {
  const name = clean_(value, 80, 'Tên nhãn');
  const ss = SpreadsheetApp.openById(CATALOG_SPREADSHEET_ID);
  const labels = readTable_(ss, 'labels');
  if (labels.length >= MAX_LABELS) throw new Error('Danh mục đã đạt giới hạn nhãn');
  if (labels.some(item => key_(item.ten_nhan) === key_(name))) throw new Error('Tên nhãn đã tồn tại');
  const now = new Date().toISOString();
  const label = {label_id: Utilities.getUuid(), ten_nhan: name, ngay_tao: now, cap_nhat: now};
  labels.push(label);
  writeTable_(ss, 'labels', labels);
  return {label: label};
}

function renameLabel_(idValue, nameValue) {
  const id = clean_(idValue, 64, 'Mã nhãn');
  const name = clean_(nameValue, 80, 'Tên nhãn');
  const ss = SpreadsheetApp.openById(CATALOG_SPREADSHEET_ID);
  const labels = readTable_(ss, 'labels');
  const target = labels.find(item => item.label_id === id);
  if (!target) throw new Error('Không tìm thấy nhãn');
  if (labels.some(item => item.label_id !== id && key_(item.ten_nhan) === key_(name))) throw new Error('Tên nhãn đã tồn tại');
  target.ten_nhan = name;
  target.cap_nhat = new Date().toISOString();
  writeTable_(ss, 'labels', labels);
  return {label: target};
}

function deleteLabel_(idValue) {
  const id = clean_(idValue, 64, 'Mã nhãn');
  const ss = SpreadsheetApp.openById(CATALOG_SPREADSHEET_ID);
  const labels = readTable_(ss, 'labels');
  if (!labels.some(item => item.label_id === id)) throw new Error('Không tìm thấy nhãn');
  const links = readTable_(ss, 'links').filter(item => item.label_id !== id);
  writeTable_(ss, 'links', links);
  writeTable_(ss, 'labels', labels.filter(item => item.label_id !== id));
  return {deleted: id};
}

function setLabelServices_(labelValue, serviceValues) {
  const labelId = clean_(labelValue, 64, 'Mã nhãn');
  if (!Array.isArray(serviceValues) || serviceValues.length > 100) throw new Error('Có thể gán tối đa 100 dịch vụ mỗi lần');
  const ss = SpreadsheetApp.openById(CATALOG_SPREADSHEET_ID);
  const labels = readTable_(ss, 'labels');
  const services = readTable_(ss, 'services');
  if (!labels.some(item => item.label_id === labelId)) throw new Error('Không tìm thấy nhãn');
  const validServices = new Set(services.map(item => item.service_id));
  const selected = new Set(serviceValues.map(value => clean_(value, 64, 'Mã dịch vụ')));
  selected.forEach(id => { if (!validServices.has(id)) throw new Error('Không tìm thấy dịch vụ được chọn'); });
  const links = readTable_(ss, 'links').filter(item => item.label_id !== labelId);
  selected.forEach(serviceId => links.push({label_id: labelId, service_id: serviceId}));
  if (links.length > MAX_LINKS) throw new Error('Danh mục đã đạt giới hạn liên kết');
  writeTable_(ss, 'links', links);
  return {label_id: labelId, service_ids: Array.from(selected)};
}

function syncStaff_(values) {
  if (!Array.isArray(values) || values.length > 100) throw new Error('Có thể đồng bộ tối đa 100 nhân sự mỗi lần');
  const ss = SpreadsheetApp.openById(CATALOG_SPREADSHEET_ID);
  ensureStaffTables_(ss);
  const rows = readTable_(ss, 'staff');
  const byId = new Map(rows.map(item => [item.staff_id, item]));
  const now = new Date().toISOString();
  values.forEach(value => {
    if (!value || typeof value !== 'object') throw new Error('Nhân sự không hợp lệ');
    const id = clean_(value.staff_id, 64, 'Mã nhân sự');
    if (byId.has(id)) {
      byId.get(id).cap_nhat = now;
    } else {
      byId.set(id, {staff_id: id, cap_nhat: now});
    }
  });
  const result = Array.from(byId.values());
  if (result.length > MAX_STAFF) throw new Error('Danh mục đã đạt giới hạn nhân sự');
  writeTable_(ss, 'staff', result);
  return {synced: values.length};
}

function createStaffLabel_(value) {
  const name = clean_(value, 80, 'Tên nhãn nhân sự');
  const ss = SpreadsheetApp.openById(CATALOG_SPREADSHEET_ID);
  ensureStaffTables_(ss);
  const labels = readTable_(ss, 'staff_labels');
  if (labels.length >= MAX_STAFF_LABELS) throw new Error('Danh mục đã đạt giới hạn nhãn nhân sự');
  if (labels.some(item => key_(item.ten_nhan) === key_(name))) throw new Error('Tên nhãn nhân sự đã tồn tại');
  const now = new Date().toISOString();
  const label = {staff_label_id: Utilities.getUuid(), ten_nhan: name, ngay_tao: now, cap_nhat: now};
  labels.push(label);
  writeTable_(ss, 'staff_labels', labels);
  return {label: label};
}

function renameStaffLabel_(idValue, nameValue) {
  const id = clean_(idValue, 64, 'Mã nhãn nhân sự');
  const name = clean_(nameValue, 80, 'Tên nhãn nhân sự');
  const ss = SpreadsheetApp.openById(CATALOG_SPREADSHEET_ID);
  ensureStaffTables_(ss);
  const labels = readTable_(ss, 'staff_labels');
  const target = labels.find(item => item.staff_label_id === id);
  if (!target) throw new Error('Không tìm thấy nhãn nhân sự');
  if (labels.some(item => item.staff_label_id !== id && key_(item.ten_nhan) === key_(name))) throw new Error('Tên nhãn nhân sự đã tồn tại');
  target.ten_nhan = name;
  target.cap_nhat = new Date().toISOString();
  writeTable_(ss, 'staff_labels', labels);
  return {label: target};
}

function deleteStaffLabel_(idValue) {
  const id = clean_(idValue, 64, 'Mã nhãn nhân sự');
  const ss = SpreadsheetApp.openById(CATALOG_SPREADSHEET_ID);
  ensureStaffTables_(ss);
  const labels = readTable_(ss, 'staff_labels');
  if (!labels.some(item => item.staff_label_id === id)) throw new Error('Không tìm thấy nhãn nhân sự');
  writeTable_(ss, 'staff_links', readTable_(ss, 'staff_links').filter(item => item.staff_label_id !== id));
  writeTable_(ss, 'staff_labels', labels.filter(item => item.staff_label_id !== id));
  return {deleted: id};
}

function setLabelStaff_(labelValue, staffValues) {
  const labelId = clean_(labelValue, 64, 'Mã nhãn nhân sự');
  if (!Array.isArray(staffValues) || staffValues.length > 500) throw new Error('Có thể gán tối đa 500 nhân sự mỗi lần');
  const ss = SpreadsheetApp.openById(CATALOG_SPREADSHEET_ID);
  ensureStaffTables_(ss);
  const labels = readTable_(ss, 'staff_labels');
  const staff = readTable_(ss, 'staff');
  if (!labels.some(item => item.staff_label_id === labelId)) throw new Error('Không tìm thấy nhãn nhân sự');
  const validStaff = new Set(staff.map(item => item.staff_id));
  const selected = new Set(staffValues.map(value => clean_(value, 64, 'Mã nhân sự')));
  selected.forEach(id => { if (!validStaff.has(id)) throw new Error('Không tìm thấy nhân sự được chọn'); });
  const links = readTable_(ss, 'staff_links').filter(item => item.staff_label_id !== labelId);
  selected.forEach(staffId => links.push({staff_label_id: labelId, staff_id: staffId}));
  if (links.length > MAX_STAFF_LINKS) throw new Error('Danh mục đã đạt giới hạn liên kết nhãn-nhân sự');
  writeTable_(ss, 'staff_links', links);
  return {staff_label_id: labelId, staff_ids: Array.from(selected)};
}

function writeTable_(ss, key, records) {
  const sheet = ss.getSheetByName(SHEETS[key]);
  const header = HEADERS[key];
  const rows = [header].concat(records.map(item => header.map(name => item[name] || '')));
  const existing = sheet.getLastRow();
  if (existing > 1) sheet.getRange(2, 1, existing - 1, header.length).clearContent();
  if (rows.length > 1) sheet.getRange(1, 1, rows.length, header.length).setValues(rows);
}

function checkWriteLimit_() {
  const props = PropertiesService.getScriptProperties();
  const now = Date.now();
  const minute = Math.floor(now / 60000);
  const day = Utilities.formatDate(new Date(now), 'UTC', 'yyyyMMdd');
  const minuteKey = 'writes_m_' + minute;
  const dayKey = 'writes_d_' + day;
  const minuteCount = Number(props.getProperty(minuteKey) || 0) + 1;
  const dayCount = Number(props.getProperty(dayKey) || 0) + 1;
  if (minuteCount > MAX_WRITES_PER_MINUTE || dayCount > MAX_WRITES_PER_DAY) throw new Error('API đang giới hạn số lần cập nhật; vui lòng thử lại sau');
  props.setProperties({[minuteKey]: String(minuteCount), [dayKey]: String(dayCount)});
  const oldMinute = 'writes_m_' + (minute - 2);
  props.deleteProperty(oldMinute);
}

function clean_(value, limit, field) {
  const text = String(value == null ? '' : value).normalize('NFC').trim().replace(/\s+/g, ' ');
  if (!text || text.length > limit || /[\u0000-\u001f\u007f]/.test(text)) throw new Error(field + ' không hợp lệ');
  return text;
}

function key_(value) {
  return clean_(value, 200, 'Giá trị').toLocaleLowerCase('vi');
}

function output_(value) {
  return ContentService.createTextOutput(JSON.stringify(value)).setMimeType(ContentService.MimeType.JSON);
}

function safeMessage_(error) {
  return String(error && error.message || 'Lỗi máy chủ').slice(0, 250);
}
