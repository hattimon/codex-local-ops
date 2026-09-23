from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import load_config, save_config
from .diagnostics import run_diagnostics
from .ssh_ops import add_host, hosts, import_hosts, remove_host
from .wizard import configure_first_run


def _tk():
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox, ttk
    except Exception as exc:
        raise RuntimeError(f"Tk GUI is unavailable: {exc}") from exc
    return tk, ttk, filedialog, messagebox


class LocalOpsManager:
    def __init__(self) -> None:
        tk, ttk, filedialog, messagebox = _tk()
        self.tk = tk
        self.ttk = ttk
        self.filedialog = filedialog
        self.messagebox = messagebox
        self.root = tk.Tk()
        self.root.title("Codex Local Ops Manager")
        self.root.geometry("980x680")
        self.root.minsize(840, 560)
        self.cfg = load_config()
        self._build()
        self.refresh_all()

    def _build(self) -> None:
        ttk = self.ttk
        top = ttk.Frame(self.root, padding=10)
        top.pack(fill="x")
        self.status_var = self.tk.StringVar()
        ttk.Label(top, text="Codex Local Ops Manager", font=("TkDefaultFont", 15, "bold")).pack(side="left")
        ttk.Label(top, textvariable=self.status_var).pack(side="right")

        self.tabs = ttk.Notebook(self.root)
        self.tabs.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self._build_overview()
        self._build_roots()
        self._build_ssh()
        self._build_settings()
        self._build_diagnostics()

    def _new_tab(self, title: str):
        frame = self.ttk.Frame(self.tabs, padding=12)
        self.tabs.add(frame, text=title)
        return frame

    def _build_overview(self) -> None:
        frame = self._new_tab("Overview")
        self.overview = self.tk.Text(frame, wrap="word", height=20)
        self.overview.pack(fill="both", expand=True)
        self.ttk.Button(frame, text="Refresh", command=self.refresh_all).pack(anchor="e", pady=(8, 0))

    def _build_roots(self) -> None:
        frame = self._new_tab("Trusted Roots")
        self.root_list = self.tk.Listbox(frame, height=18)
        self.root_list.pack(fill="both", expand=True)
        buttons = self.ttk.Frame(frame)
        buttons.pack(fill="x", pady=(8, 0))
        self.ttk.Button(buttons, text="Add folder", command=self.add_root).pack(side="left")
        self.ttk.Button(buttons, text="Remove selected", command=self.remove_root).pack(side="left", padx=8)
        self.ttk.Button(buttons, text="Save", command=self.save_roots).pack(side="right")

    def _build_ssh(self) -> None:
        frame = self._new_tab("SSH Hosts")
        columns = ("id", "hostname", "user", "port", "profile", "trusted")
        self.ssh_tree = self.ttk.Treeview(frame, columns=columns, show="headings", height=14)
        for col in columns:
            self.ssh_tree.heading(col, text=col.upper())
            self.ssh_tree.column(col, width=120, stretch=True)
        self.ssh_tree.pack(fill="both", expand=True)
        actions = self.ttk.Frame(frame)
        actions.pack(fill="x", pady=(8, 6))
        self.ttk.Button(actions, text="Import ~/.ssh/config", command=self.import_ssh).pack(side="left")
        self.ttk.Button(actions, text="Remove selected", command=self.remove_ssh).pack(side="left", padx=8)
        self.ttk.Button(actions, text="Refresh", command=self.refresh_ssh).pack(side="right")

        form = self.ttk.LabelFrame(frame, text="Add / update host", padding=8)
        form.pack(fill="x")
        self.ssh_vars: dict[str, Any] = {}
        fields = [("id", "ID"), ("hostname", "Hostname"), ("user", "User"), ("port", "Port")]
        for idx, (key, label) in enumerate(fields):
            self.ttk.Label(form, text=label).grid(row=0, column=idx, sticky="w")
            var = self.tk.StringVar(value="22" if key == "port" else "")
            self.ssh_vars[key] = var
            self.ttk.Entry(form, textvariable=var, width=18).grid(row=1, column=idx, padx=(0, 8), sticky="ew")
        self.ssh_profile = self.tk.StringVar(value="READ_ONLY")
        self.ttk.Label(form, text="Permission").grid(row=2, column=0, sticky="w", pady=(8, 0))
        self.ttk.Combobox(form, textvariable=self.ssh_profile, values=("READ_ONLY", "OPERATIONS", "FULL"), state="readonly", width=16).grid(row=3, column=0, sticky="w")
        self.ssh_trusted = self.tk.BooleanVar(value=False)
        self.ttk.Checkbutton(form, text="Trusted host", variable=self.ssh_trusted).grid(row=3, column=1, sticky="w")
        self.ttk.Button(form, text="Save host", command=self.save_ssh).grid(row=3, column=3, sticky="e")

    def _build_settings(self) -> None:
        frame = self._new_tab("Settings / First Run")
        self.local_profile = self.tk.StringVar()
        self.computer_mode = self.tk.StringVar()
        self.import_ssh_var = self.tk.BooleanVar()
        self.browser_var = self.tk.BooleanVar()
        self.media_var = self.tk.BooleanVar()
        self.obs_var = self.tk.BooleanVar()
        rows = [
            ("Local execution profile", self.local_profile, ("SAFE", "DEVELOPER", "FULL")),
            ("Computer control", self.computer_mode, ("OFF", "SAFE", "INTERACTIVE", "FULL")),
        ]
        for row, (label, var, values) in enumerate(rows):
            self.ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=6)
            self.ttk.Combobox(frame, textvariable=var, values=values, state="readonly", width=22).grid(row=row, column=1, sticky="w")
        checks = [
            ("Import ~/.ssh/config", self.import_ssh_var),
            ("Enable browser automation", self.browser_var),
            ("Enable media tools", self.media_var),
            ("Enable OBS integration", self.obs_var),
        ]
        for offset, (label, var) in enumerate(checks, start=3):
            self.ttk.Checkbutton(frame, text=label, variable=var).grid(row=offset, column=0, columnspan=2, sticky="w", pady=4)
        self.ttk.Button(frame, text="Save settings and complete First Run", command=self.save_settings).grid(row=8, column=0, columnspan=2, sticky="w", pady=(16, 0))

    def _build_diagnostics(self) -> None:
        frame = self._new_tab("Diagnostics")
        self.diag = self.tk.Text(frame, wrap="none")
        self.diag.pack(fill="both", expand=True)
        self.ttk.Button(frame, text="Run diagnostics", command=self.refresh_diagnostics).pack(anchor="e", pady=(8, 0))

    def refresh_all(self) -> None:
        self.cfg = load_config()
        completed = bool(self.cfg.get("first_run", {}).get("completed", False))
        self.status_var.set("First Run: COMPLETE" if completed else "First Run: REQUIRED")
        self.overview.delete("1.0", "end")
        summary = {
            "first_run_completed": completed,
            "trusted_roots": self.cfg.get("projects", {}).get("trusted_roots", []),
            "local_profile": self.cfg.get("permissions", {}).get("local_profile"),
            "computer_control": self.cfg.get("computer_control", {}).get("mode"),
            "ssh_hosts": len(hosts()),
            "browser": self.cfg.get("browser", {}),
            "media": self.cfg.get("media", {}),
            "obs": {k: v for k, v in self.cfg.get("obs", {}).items() if "password" not in k.lower()},
        }
        self.overview.insert("1.0", json.dumps(summary, indent=2, ensure_ascii=False))
        self.root_list.delete(0, "end")
        for item in self.cfg.get("projects", {}).get("trusted_roots", []):
            self.root_list.insert("end", item)
        self.local_profile.set(str(self.cfg.get("permissions", {}).get("local_profile", "SAFE")).upper())
        self.computer_mode.set(str(self.cfg.get("computer_control", {}).get("mode", "SAFE")).upper())
        self.import_ssh_var.set(bool(self.cfg.get("ssh", {}).get("import_ssh_config", True)))
        self.browser_var.set(bool(self.cfg.get("browser", {}).get("enabled", True)))
        self.media_var.set(bool(self.cfg.get("media", {}).get("enabled", True)))
        self.obs_var.set(bool(self.cfg.get("obs", {}).get("enabled", True)))
        self.refresh_ssh()
        if not completed:
            self.tabs.select(3)

    def add_root(self) -> None:
        path = self.filedialog.askdirectory(title="Choose trusted project root")
        if path and path not in self.root_list.get(0, "end"):
            self.root_list.insert("end", str(Path(path).resolve()))

    def remove_root(self) -> None:
        selected = list(self.root_list.curselection())
        for idx in reversed(selected):
            self.root_list.delete(idx)

    def save_roots(self) -> None:
        roots = list(self.root_list.get(0, "end"))
        if not roots:
            self.messagebox.showerror("Trusted Roots", "At least one trusted project root is required.")
            return
        cfg = load_config()
        cfg.setdefault("projects", {})["trusted_roots"] = [str(Path(x).expanduser().resolve()) for x in roots]
        save_config(cfg)
        self.refresh_all()

    def refresh_ssh(self) -> None:
        for iid in self.ssh_tree.get_children():
            self.ssh_tree.delete(iid)
        for item in hosts():
            values = (
                item.get("id") or item.get("alias"), item.get("hostname"), item.get("user"), item.get("port", 22),
                item.get("permission_profile", "READ_ONLY"), bool(item.get("trusted", False)),
            )
            self.ssh_tree.insert("", "end", values=values)

    def import_ssh(self) -> None:
        result = import_hosts(persist=True)
        self.messagebox.showinfo("SSH import", f"Imported {len(result.get('hosts', []))} host entries.")
        self.refresh_ssh()

    def remove_ssh(self) -> None:
        for iid in self.ssh_tree.selection():
            values = self.ssh_tree.item(iid, "values")
            if values:
                remove_host(str(values[0]))
        self.refresh_ssh()

    def save_ssh(self) -> None:
        try:
            item = {
                "id": self.ssh_vars["id"].get().strip(),
                "hostname": self.ssh_vars["hostname"].get().strip(),
                "user": self.ssh_vars["user"].get().strip(),
                "port": int(self.ssh_vars["port"].get() or 22),
                "permission_profile": self.ssh_profile.get(),
                "trusted": self.ssh_trusted.get(),
            }
            item["name"] = item["id"]
            item["alias"] = item["id"]
            add_host(item)
        except Exception as exc:
            self.messagebox.showerror("SSH host", str(exc))
            return
        self.refresh_ssh()

    def save_settings(self) -> None:
        roots = list(self.root_list.get(0, "end"))
        try:
            configure_first_run(
                roots,
                local_profile=self.local_profile.get(),
                computer_mode=self.computer_mode.get(),
                import_ssh_config=self.import_ssh_var.get(),
                browser_enabled=self.browser_var.get(),
                media_enabled=self.media_var.get(),
                obs_enabled=self.obs_var.get(),
            )
        except Exception as exc:
            self.messagebox.showerror("First Run", str(exc))
            return
        self.messagebox.showinfo("First Run", "Configuration saved.")
        self.refresh_all()

    def refresh_diagnostics(self) -> None:
        self.diag.delete("1.0", "end")
        self.diag.insert("1.0", json.dumps(run_diagnostics(), indent=2, ensure_ascii=False, default=str))

    def run(self) -> None:
        self.root.mainloop()


def run_manager() -> dict[str, Any]:
    try:
        app = LocalOpsManager()
        app.run()
        return {"status": "OK"}
    except Exception as exc:
        return {
            "status": "CAPABILITY_UNAVAILABLE",
            "platform": "gui",
            "feature": "manager",
            "reason": str(exc),
            "suggested_setup": "Run on a desktop session with Tk available. Headless Local Ops remains fully usable.",
        }
