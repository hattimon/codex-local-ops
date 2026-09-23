from __future__ import annotations

from pathlib import Path
from typing import Any

from .wizard import configure_first_run, first_run_status


def _default_root() -> str:
    status = first_run_status()
    roots = status.get("trusted_roots") or []
    if roots:
        return str(roots[0])
    return str(Path.cwd().resolve())


def run_first_run_gui() -> dict[str, Any]:
    """Open the local first-run configuration window.

    Tkinter is imported lazily so importing codexLocalOps stays headless-safe.
    The GUI only edits the existing codexLocalOps config through wizard.py.
    """
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox, ttk
    except Exception as exc:  # pragma: no cover - depends on local Python build
        return {"status": "CAPABILITY_UNAVAILABLE", "reason": f"Tkinter is unavailable: {exc}"}

    initial = first_run_status()
    result: dict[str, Any] = {"status": "CANCELLED"}

    root = tk.Tk()
    root.title("Codex Local Ops - First Run")
    root.geometry("700x470")
    root.minsize(620, 430)

    frame = ttk.Frame(root, padding=18)
    frame.pack(fill="both", expand=True)
    frame.columnconfigure(1, weight=1)

    ttk.Label(frame, text="Codex Local Ops", font=("Segoe UI", 16, "bold")).grid(
        row=0, column=0, columnspan=3, sticky="w", pady=(0, 4)
    )
    ttk.Label(
        frame,
        text="Configure trusted projects and local automation permissions.",
    ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(0, 18))

    project_var = tk.StringVar(value=_default_root())
    profile_var = tk.StringVar(value=str(initial.get("local_profile") or "DEVELOPER"))
    mode_var = tk.StringVar(value=str(initial.get("computer_mode") or "SAFE"))
    import_ssh_var = tk.BooleanVar(value=bool(initial.get("import_ssh_config", True)))
    browser_var = tk.BooleanVar(value=True)
    media_var = tk.BooleanVar(value=True)
    obs_var = tk.BooleanVar(value=True)

    ttk.Label(frame, text="Trusted project root").grid(row=2, column=0, sticky="w", padx=(0, 10), pady=6)
    project_entry = ttk.Entry(frame, textvariable=project_var)
    project_entry.grid(row=2, column=1, sticky="ew", pady=6)

    def choose_root() -> None:
        selected = filedialog.askdirectory(initialdir=project_var.get() or str(Path.cwd()))
        if selected:
            project_var.set(selected)

    ttk.Button(frame, text="Browse...", command=choose_root).grid(row=2, column=2, padx=(10, 0), pady=6)

    ttk.Label(frame, text="Local profile").grid(row=3, column=0, sticky="w", padx=(0, 10), pady=6)
    profile_box = ttk.Combobox(
        frame,
        textvariable=profile_var,
        values=("SAFE", "DEVELOPER", "FULL"),
        state="readonly",
    )
    profile_box.grid(row=3, column=1, sticky="ew", pady=6)

    ttk.Label(frame, text="Computer control").grid(row=4, column=0, sticky="w", padx=(0, 10), pady=6)
    mode_box = ttk.Combobox(
        frame,
        textvariable=mode_var,
        values=("OFF", "SAFE", "INTERACTIVE", "FULL"),
        state="readonly",
    )
    mode_box.grid(row=4, column=1, sticky="ew", pady=6)

    ttk.Separator(frame).grid(row=5, column=0, columnspan=3, sticky="ew", pady=14)
    ttk.Checkbutton(frame, text="Import SSH config", variable=import_ssh_var).grid(row=6, column=0, columnspan=3, sticky="w", pady=4)
    ttk.Checkbutton(frame, text="Enable browser automation", variable=browser_var).grid(row=7, column=0, columnspan=3, sticky="w", pady=4)
    ttk.Checkbutton(frame, text="Enable media / FFmpeg tools", variable=media_var).grid(row=8, column=0, columnspan=3, sticky="w", pady=4)
    ttk.Checkbutton(frame, text="Enable OBS integration", variable=obs_var).grid(row=9, column=0, columnspan=3, sticky="w", pady=4)

    button_row = ttk.Frame(frame)
    button_row.grid(row=10, column=0, columnspan=3, sticky="e", pady=(22, 0))

    def save() -> None:
        nonlocal result
        try:
            configured = configure_first_run(
                [project_var.get().strip()],
                local_profile=profile_var.get(),
                computer_mode=mode_var.get(),
                import_ssh_config=import_ssh_var.get(),
                browser_enabled=browser_var.get(),
                media_enabled=media_var.get(),
                obs_enabled=obs_var.get(),
            )
        except Exception as exc:
            messagebox.showerror("Codex Local Ops", str(exc), parent=root)
            return
        result = configured
        messagebox.showinfo("Codex Local Ops", "Configuration saved.", parent=root)
        root.destroy()

    ttk.Button(button_row, text="Cancel", command=root.destroy).pack(side="right", padx=(8, 0))
    ttk.Button(button_row, text="Save", command=save).pack(side="right")

    project_entry.focus_set()
    root.mainloop()
    return result


def main() -> int:
    result = run_first_run_gui()
    return 0 if result.get("status") == "OK" else 1


if __name__ == "__main__":
    raise SystemExit(main())
