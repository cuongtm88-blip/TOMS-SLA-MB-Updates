"""Tk UI for local service filters and shared service labels."""
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

import service_catalog


def open_service_window(parent, client, settings, on_save):
    try:
        catalog = client.get()
    except service_catalog.CatalogError as exc:
        messagebox.showerror("Dịch vụ", str(exc), parent=parent)
        return

    window = tk.Toplevel(parent)
    window.title("Dịch vụ và nhãn")
    window.geometry("800x560")
    window.transient(parent)
    notebook = ttk.Notebook(window)
    notebook.pack(fill="both", expand=True, padx=10, pady=10)

    filter_tab = ttk.Frame(notebook, padding=10)
    labels_tab = ttk.Frame(notebook, padding=10)
    notebook.add(filter_tab, text="Lọc dịch vụ (riêng máy này)")
    notebook.add(labels_tab, text="Quản lý nhãn (dùng chung)")

    mode = tk.StringVar(value=settings.get("service_filter_mode", "all"))
    ttk.Label(filter_tab, text="Dịch vụ được tô sáng gồm dịch vụ chọn trực tiếp và dịch vụ thuộc nhãn đang chọn.").pack(anchor="w")
    modes = ttk.Frame(filter_tab)
    modes.pack(fill="x", pady=6)
    ttk.Radiobutton(modes, text="Tất cả dịch vụ", variable=mode, value="all").pack(side="left", padx=5)
    ttk.Radiobutton(modes, text="Chỉ dịch vụ đã chọn", variable=mode, value="custom").pack(side="left", padx=5)
    selectors = ttk.Frame(filter_tab)
    selectors.pack(fill="both", expand=True)

    service_box = ttk.LabelFrame(selectors, text="Dịch vụ")
    service_box.pack(side="left", fill="both", expand=True, padx=(0, 6))
    service_list = tk.Listbox(service_box, selectmode="multiple", exportselection=False)
    service_scroll = ttk.Scrollbar(service_box, orient="vertical", command=service_list.yview)
    service_list.configure(yscrollcommand=service_scroll.set)
    service_list.pack(side="left", fill="both", expand=True, padx=4, pady=4)
    service_scroll.pack(side="right", fill="y")

    label_box = ttk.LabelFrame(selectors, text="Nhãn (chọn nhãn để lọc mọi dịch vụ thuộc nhãn)")
    label_box.pack(side="left", fill="both", expand=True, padx=(6, 0))
    label_list = tk.Listbox(label_box, selectmode="multiple", exportselection=False)
    label_scroll = ttk.Scrollbar(label_box, orient="vertical", command=label_list.yview)
    label_list.configure(yscrollcommand=label_scroll.set)
    label_list.pack(side="left", fill="both", expand=True, padx=4, pady=4)
    label_scroll.pack(side="right", fill="y")

    service_ids = [str(item["service_id"]) for item in catalog["services"]]
    label_ids = [str(item["label_id"]) for item in catalog["labels"]]
    links_by_label = {}
    for link in catalog["links"]:
        links_by_label.setdefault(str(link["label_id"]), set()).add(str(link["service_id"]))
    selected_services = settings.get("selected_service_ids")
    direct_service_ids = set(map(str, selected_services or []))
    if selected_services is None and mode.get() == "all":
        direct_service_ids = set(service_ids)
    selected_labels = set(map(str, settings.get("selected_service_label_ids", [])))
    for item in catalog["services"]:
        service_list.insert("end", item["ten_dich_vu"])
    for item in catalog["labels"]:
        label_list.insert("end", item["ten_nhan"])
    for index, item in enumerate(catalog["labels"]):
        if str(item["label_id"]) in selected_labels:
            label_list.select_set(index)

    displayed_service_ids = set()
    selection_syncing = {"active": False}

    def selected_filter_labels():
        return {label_ids[index] for index in label_list.curselection()}

    def filter_label_service_ids():
        labels = selected_filter_labels()
        return set().union(*(links_by_label.get(label_id, set()) for label_id in labels)) if labels else set()

    def refresh_filter_service_marks():
        selection_syncing["active"] = True
        members = filter_label_service_ids()
        visible = direct_service_ids | members
        service_list.selection_clear(0, "end")
        for index, ident in enumerate(service_ids):
            if ident in visible:
                service_list.selection_set(index)
        displayed_service_ids.clear()
        displayed_service_ids.update(visible)
        selection_syncing["active"] = False

    def on_filter_service_select(_event=None):
        if selection_syncing["active"]:
            return
        now_selected = {service_ids[index] for index in service_list.curselection()}
        added = now_selected - displayed_service_ids
        removed = displayed_service_ids - now_selected
        direct_service_ids.update(added)
        direct_service_ids.difference_update(removed - filter_label_service_ids())
        refresh_filter_service_marks()

    service_list.bind("<<ListboxSelect>>", on_filter_service_select)
    label_list.bind("<<ListboxSelect>>", lambda _event: refresh_filter_service_marks())
    refresh_filter_service_marks()

    membership = ttk.LabelFrame(labels_tab, text="Dịch vụ thuộc nhãn")
    membership.pack(fill="both", expand=True, pady=(8, 0))
    ttk.Label(labels_tab, text="Tạo, đổi tên hoặc xóa nhãn. Việc này cập nhật danh mục chung cho cả ba ứng dụng.").pack(anchor="w")
    label_rows = ttk.Frame(labels_tab)
    label_rows.pack(fill="both", expand=True)
    managed_labels = tk.Listbox(label_rows, exportselection=False, width=28)
    managed_labels.pack(side="left", fill="y", padx=(0, 8), pady=4)
    member_list = tk.Listbox(membership, selectmode="multiple", exportselection=False)
    member_scroll = ttk.Scrollbar(membership, orient="vertical", command=member_list.yview)
    member_list.configure(yscrollcommand=member_scroll.set)
    member_list.pack(side="left", fill="both", expand=True, padx=4, pady=4)
    member_scroll.pack(side="right", fill="y")
    for item in catalog["labels"]:
        managed_labels.insert("end", item["ten_nhan"])
    for item in catalog["services"]:
        member_list.insert("end", item["ten_dich_vu"])

    def refresh_members(_event=None):
        member_list.selection_clear(0, "end")
        selected = managed_labels.curselection()
        if selected:
            for index in range(len(service_ids)):
                if service_ids[index] in links_by_label.get(label_ids[selected[0]], set()):
                    member_list.selection_set(index)

    managed_labels.bind("<<ListboxSelect>>", refresh_members)

    def reload_catalog():
        nonlocal catalog, label_ids, service_ids, links_by_label
        try:
            keep_labels = selected_filter_labels()
            catalog = client.get()
            label_ids = [str(item["label_id"]) for item in catalog["labels"]]
            service_ids = [str(item["service_id"]) for item in catalog["services"]]
            links_by_label = {}
            for link in catalog["links"]:
                links_by_label.setdefault(str(link["label_id"]), set()).add(str(link["service_id"]))
            managed_labels.delete(0, "end")
            member_list.delete(0, "end")
            service_list.delete(0, "end")
            label_list.delete(0, "end")
            for item in catalog["labels"]:
                managed_labels.insert("end", item["ten_nhan"])
                label_list.insert("end", item["ten_nhan"])
            for item in catalog["services"]:
                member_list.insert("end", item["ten_dich_vu"])
                service_list.insert("end", item["ten_dich_vu"])
            direct_service_ids.intersection_update(service_ids)
            for index, ident in enumerate(label_ids):
                if ident in keep_labels:
                    label_list.select_set(index)
            refresh_filter_service_marks()
        except service_catalog.CatalogError as exc:
            messagebox.showerror("Dịch vụ", str(exc), parent=window)

    def create_label():
        name = simpledialog.askstring("Tạo nhãn", "Tên nhãn mới:", parent=window)
        if not name:
            return
        try:
            client.create_label(name)
            reload_catalog()
        except service_catalog.CatalogError as exc:
            messagebox.showerror("Tạo nhãn", str(exc), parent=window)

    def rename_label():
        selected = managed_labels.curselection()
        if not selected:
            messagebox.showwarning("Đổi tên", "Hãy chọn nhãn cần đổi tên.", parent=window)
            return
        old = managed_labels.get(selected[0])
        name = simpledialog.askstring("Đổi tên nhãn", "Tên mới:", initialvalue=old, parent=window)
        if not name:
            return
        try:
            client.rename_label(label_ids[selected[0]], name)
            reload_catalog()
        except service_catalog.CatalogError as exc:
            messagebox.showerror("Đổi tên nhãn", str(exc), parent=window)

    def delete_label():
        selected = managed_labels.curselection()
        if not selected:
            messagebox.showwarning("Xóa nhãn", "Hãy chọn nhãn cần xóa.", parent=window)
            return
        name = managed_labels.get(selected[0])
        if not messagebox.askyesno("Xóa nhãn", f"Xóa nhãn '{name}' và các liên kết? Dịch vụ vẫn được giữ.", parent=window):
            return
        try:
            client.delete_label(label_ids[selected[0]])
            reload_catalog()
        except service_catalog.CatalogError as exc:
            messagebox.showerror("Xóa nhãn", str(exc), parent=window)

    def save_members():
        selected = managed_labels.curselection()
        if not selected:
            messagebox.showwarning("Lưu thành viên", "Hãy chọn một nhãn.", parent=window)
            return
        ids = [service_ids[index] for index in member_list.curselection()]
        try:
            client.set_label_services(label_ids[selected[0]], ids)
            reload_catalog()
            messagebox.showinfo("Nhãn", "Đã cập nhật nhóm dịch vụ dùng chung.", parent=window)
        except service_catalog.CatalogError as exc:
            messagebox.showerror("Nhãn", str(exc), parent=window)

    manage_buttons = ttk.Frame(labels_tab)
    manage_buttons.pack(fill="x", pady=5)
    ttk.Button(manage_buttons, text="Tạo nhãn", command=create_label).pack(side="left", padx=3)
    ttk.Button(manage_buttons, text="Đổi tên", command=rename_label).pack(side="left", padx=3)
    ttk.Button(manage_buttons, text="Xóa nhãn", command=delete_label).pack(side="left", padx=3)
    ttk.Button(manage_buttons, text="Lưu dịch vụ vào nhãn", command=save_members).pack(side="left", padx=3)

    def save_selection():
        ids = [ident for ident in service_ids if ident in direct_service_ids]
        groups = [label_ids[index] for index in label_list.curselection()]
        value = {"service_filter_mode": mode.get(), "selected_service_ids": ids,
                 "selected_service_label_ids": groups}
        on_save(value)
        window.destroy()

    bottom = ttk.Frame(window)
    bottom.pack(fill="x", padx=10, pady=(0, 10))
    ttk.Button(bottom, text="Lưu lựa chọn trên máy này", command=save_selection).pack(side="right", padx=4)
    ttk.Button(bottom, text="Đóng", command=window.destroy).pack(side="right", padx=4)
