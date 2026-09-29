"""Tk UI for local service filters and shared service labels."""
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

import service_catalog


def open_service_window(parent, client, settings, on_save, local_staff=None):
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
    staff_tab = ttk.Frame(notebook, padding=10)
    staff_labels_tab = ttk.Frame(notebook, padding=10)
    notebook.add(filter_tab, text="Lọc dịch vụ (riêng máy này)")
    notebook.add(labels_tab, text="Quản lý nhãn dịch vụ")
    notebook.add(staff_tab, text="Lọc nhân sự (riêng máy này)")
    notebook.add(staff_labels_tab, text="Quản lý nhãn nhân sự")

    mode = tk.StringVar(value=settings.get("service_filter_mode", "all"))
    ttk.Label(filter_tab, text="Dịch vụ được tô sáng gồm dịch vụ chọn trực tiếp và dịch vụ thuộc nhãn đang chọn.").pack(anchor="w")
    modes = ttk.Frame(filter_tab)
    modes.pack(fill="x", pady=6)
    ttk.Radiobutton(modes, text="Tất cả dịch vụ", variable=mode, value="all").pack(side="left", padx=5)
    ttk.Radiobutton(modes, text="Chỉ dịch vụ đã chọn", variable=mode, value="custom").pack(side="left", padx=5)
    selectors = ttk.Frame(filter_tab)
    selectors.pack(fill="both", expand=True)

    filter_actions = ttk.Frame(filter_tab)
    filter_actions.pack(fill="x", pady=(8, 0))

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
    # Earlier builds saved every service as explicitly selected in "all" mode.
    # Treat that legacy value as the old default, otherwise it masks label membership.
    if mode.get() == "all" and direct_service_ids == set(service_ids):
        direct_service_ids.clear()
    selected_labels = set(map(str, settings.get("selected_service_label_ids", [])))
    if selected_labels:
        mode.set("custom")
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
        if added or removed:
            mode.set("custom")
        refresh_filter_service_marks()

    def on_filter_labels(_event=None):
        if label_list.curselection():
            mode.set("custom")
        refresh_filter_service_marks()

    service_list.bind("<<ListboxSelect>>", on_filter_service_select)
    label_list.bind("<<ListboxSelect>>", on_filter_labels)
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

    local_staff_by_id = {str(item["staff_id"]): item for item in (local_staff or [])}
    staff = []
    staff_labels = list(catalog.get("staff_labels", []))
    staff_ids = []
    def resolve_staff(api_staff):
        ids = list(dict.fromkeys([str(item["staff_id"]) for item in api_staff]
                                + list(local_staff_by_id)))
        people = []
        for ident in ids:
            person = local_staff_by_id.get(ident)
            people.append(person or {"staff_id": ident, "ten_nhan_su": "Chưa có lịch trực trên máy này · " + ident[:8]})
        return ids, people
    staff_ids, staff = resolve_staff(catalog.get("staff", []))
    staff_label_ids = [str(item["staff_label_id"]) for item in staff_labels]
    staff_links_by_label = {}
    for link in catalog.get("staff_links", []):
        staff_links_by_label.setdefault(str(link["staff_label_id"]), set()).add(str(link["staff_id"]))

    selected_staff_labels = set(map(str, settings.get("selected_staff_label_ids", [])))
    staff_actions = ttk.Frame(staff_tab)
    staff_actions.pack(fill="x", side="bottom", pady=(8, 0))
    staff_filter_box = ttk.LabelFrame(staff_tab, text="Nhân sự đang được gán vào nhãn đã chọn")
    staff_filter_box.pack(side="left", fill="both", expand=True, padx=(0, 6), pady=8)
    staff_filter_list = tk.Listbox(staff_filter_box, exportselection=False)
    staff_filter_scroll = ttk.Scrollbar(staff_filter_box, orient="vertical", command=staff_filter_list.yview)
    staff_filter_list.configure(yscrollcommand=staff_filter_scroll.set)
    staff_filter_list.pack(side="left", fill="both", expand=True, padx=4, pady=4)
    staff_filter_scroll.pack(side="right", fill="y")
    staff_label_box = ttk.LabelFrame(staff_tab, text="Nhãn nhân sự (chọn nhãn để gửi tin riêng cho người đang trực thuộc nhãn)")
    staff_label_box.pack(side="left", fill="both", expand=True, padx=(6, 0), pady=8)
    staff_filter_labels = tk.Listbox(staff_label_box, selectmode="multiple", exportselection=False)
    staff_label_scroll = ttk.Scrollbar(staff_label_box, orient="vertical", command=staff_filter_labels.yview)
    staff_filter_labels.configure(yscrollcommand=staff_label_scroll.set)
    staff_filter_labels.pack(side="left", fill="both", expand=True, padx=4, pady=4)
    staff_label_scroll.pack(side="right", fill="y")
    ttk.Label(staff_tab, text="Khi không chọn nhãn, gửi riêng cho toàn bộ nhân sự đang trực. Nhãn chỉ giới hạn người nhận riêng; Group vẫn nhận theo cấu hình.").pack(anchor="w", before=staff_filter_box)

    def refresh_staff_filter():
        staff_filter_list.delete(0, "end")
        staff_filter_labels.delete(0, "end")
        members = set()
        selected = {staff_label_ids[i] for i in staff_filter_labels.curselection()} if staff_label_ids else set()
        for label_id in selected:
            members.update(staff_links_by_label.get(label_id, set()))
        # Preserve chosen label IDs while rebuilding the display.
        for label_id in selected_staff_labels:
            if label_id in staff_label_ids:
                staff_filter_labels.select_set(staff_label_ids.index(label_id))
                members.update(staff_links_by_label.get(label_id, set()))
        for item in staff_labels:
            staff_filter_labels.insert("end", item["ten_nhan"])
        for label_id in selected_staff_labels:
            if label_id in staff_label_ids:
                staff_filter_labels.select_set(staff_label_ids.index(label_id))
        for person in staff:
            staff_filter_list.insert("end", person["ten_nhan_su"])
            if str(person["staff_id"]) in members:
                staff_filter_list.selection_set("end")

    def on_staff_filter_labels(_event=None):
        selected_staff_labels.clear()
        selected_staff_labels.update(staff_label_ids[i] for i in staff_filter_labels.curselection())
        members = set().union(*(staff_links_by_label.get(label_id, set()) for label_id in selected_staff_labels)) if selected_staff_labels else set()
        staff_filter_list.selection_clear(0, "end")
        for index, ident in enumerate(staff_ids):
            if ident in members:
                staff_filter_list.selection_set(index)

    staff_filter_labels.bind("<<ListboxSelect>>", on_staff_filter_labels)
    for item in staff_labels:
        staff_filter_labels.insert("end", item["ten_nhan"])
    for index, item in enumerate(staff_labels):
        if str(item["staff_label_id"]) in selected_staff_labels:
            staff_filter_labels.select_set(index)
    # Build the personnel rows immediately. Calling only the selection handler
    # leaves the listbox empty until the online catalog is reloaded.
    refresh_staff_filter()

    ttk.Label(staff_labels_tab, text="Danh sách được đồng bộ từ lịch trực đã nhập. Nhãn nhân sự dùng chung trên các máy; Telegram Chat ID không được tải lên danh mục.").pack(anchor="w")
    staff_manage = ttk.Frame(staff_labels_tab)
    staff_manage.pack(fill="both", expand=True, pady=6)
    managed_staff_labels = tk.Listbox(staff_manage, exportselection=False, width=28)
    managed_staff_labels.pack(side="left", fill="y", padx=(0, 8))
    staff_member_box = ttk.LabelFrame(staff_manage, text="Nhân sự thuộc nhãn")
    staff_member_box.pack(side="left", fill="both", expand=True)
    staff_member_list = tk.Listbox(staff_member_box, selectmode="multiple", exportselection=False)
    staff_member_scroll = ttk.Scrollbar(staff_member_box, orient="vertical", command=staff_member_list.yview)
    staff_member_list.configure(yscrollcommand=staff_member_scroll.set)
    staff_member_list.pack(side="left", fill="both", expand=True, padx=4, pady=4)
    staff_member_scroll.pack(side="right", fill="y")
    for item in staff_labels:
        managed_staff_labels.insert("end", item["ten_nhan"])
    for item in staff:
        staff_member_list.insert("end", item["ten_nhan_su"])

    def refresh_staff_members(_event=None):
        staff_member_list.selection_clear(0, "end")
        selected = managed_staff_labels.curselection()
        if selected:
            for index, ident in enumerate(staff_ids):
                if ident in staff_links_by_label.get(staff_label_ids[selected[0]], set()):
                    staff_member_list.selection_set(index)

    managed_staff_labels.bind("<<ListboxSelect>>", refresh_staff_members)

    def reload_catalog():
        nonlocal catalog, label_ids, service_ids, links_by_label, staff, staff_labels, staff_ids, staff_label_ids, staff_links_by_label
        try:
            keep_labels = selected_filter_labels()
            catalog = client.get()
            label_ids = [str(item["label_id"]) for item in catalog["labels"]]
            service_ids = [str(item["service_id"]) for item in catalog["services"]]
            links_by_label = {}
            for link in catalog["links"]:
                links_by_label.setdefault(str(link["label_id"]), set()).add(str(link["service_id"]))
            staff_ids, staff = resolve_staff(catalog.get("staff", []))
            staff_labels = list(catalog.get("staff_labels", []))
            staff_label_ids = [str(item["staff_label_id"]) for item in staff_labels]
            staff_links_by_label = {}
            for link in catalog.get("staff_links", []):
                staff_links_by_label.setdefault(str(link["staff_label_id"]), set()).add(str(link["staff_id"]))
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
            managed_staff_labels.delete(0, "end")
            staff_member_list.delete(0, "end")
            for item in staff_labels:
                managed_staff_labels.insert("end", item["ten_nhan"])
                staff_filter_labels.insert("end", item["ten_nhan"])
            for item in staff:
                staff_member_list.insert("end", item["ten_nhan_su"])
                staff_filter_list.insert("end", item["ten_nhan_su"])
            direct_service_ids.intersection_update(service_ids)
            for index, ident in enumerate(label_ids):
                if ident in keep_labels:
                    label_list.select_set(index)
            refresh_filter_service_marks()
            selected_staff_labels.intersection_update(staff_label_ids)
            refresh_staff_filter()
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

    def create_staff_label():
        name = simpledialog.askstring("Tạo nhãn nhân sự", "Tên nhãn mới:", parent=window)
        if name:
            try:
                client.create_staff_label(name)
                reload_catalog()
            except service_catalog.CatalogError as exc:
                messagebox.showerror("Tạo nhãn nhân sự", str(exc), parent=window)

    def rename_staff_label():
        selected = managed_staff_labels.curselection()
        if not selected:
            messagebox.showwarning("Đổi tên", "Hãy chọn nhãn nhân sự.", parent=window)
            return
        name = simpledialog.askstring("Đổi tên nhãn", "Tên mới:", initialvalue=managed_staff_labels.get(selected[0]), parent=window)
        if name:
            try:
                client.rename_staff_label(staff_label_ids[selected[0]], name)
                reload_catalog()
            except service_catalog.CatalogError as exc:
                messagebox.showerror("Đổi tên nhãn nhân sự", str(exc), parent=window)

    def delete_staff_label():
        selected = managed_staff_labels.curselection()
        if not selected:
            messagebox.showwarning("Xóa nhãn", "Hãy chọn nhãn nhân sự.", parent=window)
            return
        name = managed_staff_labels.get(selected[0])
        if not messagebox.askyesno("Xóa nhãn", f"Xóa nhãn nhân sự '{name}' và các liên kết?", parent=window):
            return
        try:
            client.delete_staff_label(staff_label_ids[selected[0]])
            selected_staff_labels.discard(staff_label_ids[selected[0]])
            reload_catalog()
        except service_catalog.CatalogError as exc:
            messagebox.showerror("Xóa nhãn nhân sự", str(exc), parent=window)

    def save_staff_members():
        selected = managed_staff_labels.curselection()
        if not selected:
            messagebox.showwarning("Lưu thành viên", "Hãy chọn một nhãn nhân sự.", parent=window)
            return
        ids = [staff_ids[index] for index in staff_member_list.curselection()]
        try:
            client.set_label_staff(staff_label_ids[selected[0]], ids)
            reload_catalog()
            messagebox.showinfo("Nhãn nhân sự", "Đã cập nhật thành viên nhãn dùng chung.", parent=window)
        except service_catalog.CatalogError as exc:
            messagebox.showerror("Nhãn nhân sự", str(exc), parent=window)

    staff_manage_buttons = ttk.Frame(staff_labels_tab)
    staff_manage_buttons.pack(fill="x", pady=5)
    ttk.Button(staff_manage_buttons, text="Tạo nhãn", command=create_staff_label).pack(side="left", padx=3)
    ttk.Button(staff_manage_buttons, text="Đổi tên", command=rename_staff_label).pack(side="left", padx=3)
    ttk.Button(staff_manage_buttons, text="Xóa nhãn", command=delete_staff_label).pack(side="left", padx=3)
    ttk.Button(staff_manage_buttons, text="Lưu nhân sự vào nhãn", command=save_staff_members).pack(side="left", padx=3)

    def save_selection():
        ids = [ident for ident in service_ids if ident in direct_service_ids]
        groups = [label_ids[index] for index in label_list.curselection()]
        value = {"service_filter_mode": mode.get(), "selected_service_ids": ids,
                 "selected_service_label_ids": groups,
                 "selected_staff_label_ids": sorted(selected_staff_labels)}
        on_save(value)
        messagebox.showinfo("Dịch vụ và nhân sự", "Đã lưu lựa chọn lọc riêng trên máy này.", parent=window)

    ttk.Button(filter_actions, text="Lưu lựa chọn dịch vụ trên máy này", command=save_selection).pack(side="right", padx=4)
    ttk.Button(staff_actions, text="Lưu lựa chọn nhân sự trên máy này", command=save_selection).pack(side="right", padx=4)

    bottom = ttk.Frame(window)
    bottom.pack(fill="x", padx=10, pady=(0, 10))
    ttk.Button(bottom, text="Đóng", command=window.destroy).pack(side="right", padx=4)
