"""Client for the shared, anonymous Google Apps Script service catalog."""
from __future__ import annotations

import re
import unicodedata

import requests


MAX_SERVICE_NAME = 200
MAX_LABEL_NAME = 80


class CatalogError(RuntimeError):
    pass


def _clean_text(value, limit, field):
    value = unicodedata.normalize("NFC", str(value or "")).strip()
    value = re.sub(r"\s+", " ", value)
    if not value or len(value) > limit or any(ord(char) < 32 for char in value):
        raise CatalogError(f"{field} không hợp lệ")
    return value


def service_key(value):
    return _clean_text(value, MAX_SERVICE_NAME, "Tên dịch vụ").casefold()


def validate_catalog(data):
    if not isinstance(data, dict) or data.get("ok") is not True:
        message = data.get("error") if isinstance(data, dict) else None
        raise CatalogError(str(message or "API danh sách dịch vụ trả dữ liệu không hợp lệ"))
    services = data.get("services")
    labels = data.get("labels")
    links = data.get("links")
    if not all(isinstance(value, list) for value in (services, labels, links)):
        raise CatalogError("API danh sách dịch vụ thiếu dữ liệu")
    for item in services:
        if not isinstance(item, dict) or not item.get("service_id"):
            raise CatalogError("Danh mục có dịch vụ sai cấu trúc")
        _clean_text(item.get("ten_dich_vu"), MAX_SERVICE_NAME, "Tên dịch vụ")
    for item in labels:
        if not isinstance(item, dict) or not item.get("label_id"):
            raise CatalogError("Danh mục có nhãn sai cấu trúc")
        _clean_text(item.get("ten_nhan"), MAX_LABEL_NAME, "Tên nhãn")
    staff = data.get("staff", [])
    staff_labels = data.get("staff_labels", [])
    staff_links = data.get("staff_links", [])
    if not all(isinstance(value, list) for value in (staff, staff_labels, staff_links)):
        raise CatalogError("API danh mục thiếu dữ liệu nhân sự")
    for item in staff:
        if not isinstance(item, dict) or not item.get("staff_id"):
            raise CatalogError("Danh mục có nhân sự sai cấu trúc")
    for item in staff_labels:
        if not isinstance(item, dict) or not item.get("staff_label_id"):
            raise CatalogError("Danh mục có nhãn nhân sự sai cấu trúc")
        _clean_text(item.get("ten_nhan"), MAX_LABEL_NAME, "Tên nhãn nhân sự")
    valid_service_ids = {str(item["service_id"]) for item in services}
    valid_label_ids = {str(item["label_id"]) for item in labels}
    valid_staff_ids = {str(item["staff_id"]) for item in staff}
    valid_staff_label_ids = {str(item["staff_label_id"]) for item in staff_labels}
    for item in links:
        if (not isinstance(item, dict)
                or str(item.get("service_id")) not in valid_service_ids
                or str(item.get("label_id")) not in valid_label_ids):
            raise CatalogError("Danh mục có liên kết nhãn-dịch vụ sai cấu trúc")
    for item in staff_links:
        if (not isinstance(item, dict)
                or str(item.get("staff_id")) not in valid_staff_ids
                or str(item.get("staff_label_id")) not in valid_staff_label_ids):
            raise CatalogError("Danh mục có liên kết nhãn-nhân sự sai cấu trúc")
    return {
        "ok": True,
        "services": services,
        "labels": labels,
        "links": links,
        "staff": staff,
        "staff_labels": staff_labels,
        "staff_links": staff_links,
    }


class ServiceCatalog:
    def __init__(self, endpoint, session=requests, timeout=15):
        self.endpoint = str(endpoint or "").strip()
        self.session = session
        self.timeout = timeout

    def _check_endpoint(self):
        if not self.endpoint.startswith("https://script.google.com/macros/s/") or not self.endpoint.endswith("/exec"):
            raise CatalogError("Chưa cấu hình Google Apps Script API dịch vụ")

    def get(self):
        self._check_endpoint()
        try:
            response = self.session.get(self.endpoint, timeout=self.timeout)
            response.raise_for_status()
            return validate_catalog(response.json())
        except CatalogError:
            raise
        except (requests.RequestException, ValueError) as exc:
            raise CatalogError("Không tải được danh mục dịch vụ online") from exc

    def post(self, payload):
        self._check_endpoint()
        try:
            response = self.session.post(self.endpoint, json=payload, timeout=self.timeout)
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict) or data.get("ok") is not True:
                error = data.get("error") if isinstance(data, dict) else None
                raise CatalogError(str(error or "API từ chối cập nhật danh mục dịch vụ"))
            return data
        except CatalogError:
            raise
        except (requests.RequestException, ValueError) as exc:
            raise CatalogError("Không cập nhật được danh mục dịch vụ online") from exc

    def sync_services(self, names):
        clean = list(dict.fromkeys(_clean_text(name, MAX_SERVICE_NAME, "Tên dịch vụ") for name in names))
        if not clean:
            return []
        added = []
        for start in range(0, len(clean), 100):
            result = self.post({"action": "sync_services", "services": clean[start:start + 100]})
            batch = result.get("added", [])
            if not isinstance(batch, list):
                raise CatalogError("API trả về danh sách dịch vụ mới sai cấu trúc")
            added.extend(batch)
        return added

    def create_label(self, name):
        return self.post({"action": "create_label", "name": _clean_text(name, MAX_LABEL_NAME, "Tên nhãn")})

    def rename_label(self, label_id, name):
        return self.post({"action": "rename_label", "label_id": str(label_id),
                          "name": _clean_text(name, MAX_LABEL_NAME, "Tên nhãn")})

    def delete_label(self, label_id):
        return self.post({"action": "delete_label", "label_id": str(label_id)})

    def set_label_services(self, label_id, service_ids):
        return self.post({"action": "set_label_services", "label_id": str(label_id),
                          "service_ids": list(dict.fromkeys(map(str, service_ids)))[:100]})

    def sync_staff(self, staff):
        clean = []
        for item in staff:
            staff_id = _clean_text(item.get("staff_id"), 64, "Mã nhân sự")
            clean.append({"staff_id": staff_id})
        result = []
        for start in range(0, min(len(clean), 500), 50):
            response = self.post({"action": "sync_staff", "staff": clean[start:start + 50]})
            synced = response.get("synced")
            if not isinstance(synced, int) or synced < 0:
                raise CatalogError("API trả về kết quả đồng bộ nhân sự sai cấu trúc")
            result.append(synced)
        return result

    def create_staff_label(self, name):
        return self.post({"action": "create_staff_label", "name": _clean_text(name, MAX_LABEL_NAME, "Tên nhãn nhân sự")})

    def rename_staff_label(self, label_id, name):
        return self.post({"action": "rename_staff_label", "staff_label_id": str(label_id),
                          "name": _clean_text(name, MAX_LABEL_NAME, "Tên nhãn nhân sự")})

    def delete_staff_label(self, label_id):
        return self.post({"action": "delete_staff_label", "staff_label_id": str(label_id)})

    def set_label_staff(self, label_id, staff_ids):
        return self.post({"action": "set_label_staff", "staff_label_id": str(label_id),
                          "staff_ids": list(dict.fromkeys(map(str, staff_ids)))[:500]})


def staff_ids_for_labels(catalog, label_ids):
    labels = set(map(str, label_ids or []))
    return {str(link["staff_id"]) for link in catalog.get("staff_links", [])
            if str(link["staff_label_id"]) in labels}


def filter_selected_services(frame, service_ids, selected_label_ids, catalog):
    """Filter OneBSS rows by locally selected services and live label members."""
    import pandas as pd

    if frame.empty:
        return frame.copy()
    selected = set(map(str, service_ids or []))
    selected_labels = set(map(str, selected_label_ids or []))
    by_id = {str(item["service_id"]): item for item in catalog["services"]}
    for link in catalog["links"]:
        if str(link["label_id"]) in selected_labels:
            selected.add(str(link["service_id"]))
    selected_names = {
        service_key(by_id[ident]["ten_dich_vu"])
        for ident in selected if ident in by_id
    }
    if "loaihinh_tb" not in frame.columns:
        raise CatalogError("Bảng OneBSS thiếu cột Loại hình thuê bao")
    if not selected_names:
        return frame.iloc[0:0].copy()
    mask = frame["loaihinh_tb"].fillna("").map(
        lambda value: service_key(value) if str(value).strip() else ""
    ).isin(selected_names)
    return frame.loc[mask].copy().reset_index(drop=True)


def filter_all_services(frame, catalog):
    """Filter only against the current online catalog (used to report new names)."""
    ids = [str(item["service_id"]) for item in catalog["services"]]
    return filter_selected_services(frame, ids, [], catalog)
