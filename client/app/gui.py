from __future__ import annotations

import json
import queue
import threading
from pathlib import Path
from typing import Any
from tkinter import BOTH, END, LEFT, RIGHT, X, filedialog, messagebox
import tkinter as tk
from tkinter import ttk

import yaml

try:
    import pystray
    from PIL import Image, ImageDraw
except ImportError:  # pragma: no cover - optional runtime GUI feature
    pystray = None
    Image = None
    ImageDraw = None

from client.app.backup import run_backup_for_task
from client.app.config import (
    AppConfig,
    default_client_data_dir,
    default_config_path,
    load_config,
)
from client.app.local_db import LocalDb
from client.app.restore import rollback_restore, run_restore, run_verify
from client.app.scheduler import BackupTaskScheduler
from client.app.uploader import BackupServerClient, list_backups_across_servers


DEFAULT_EXCLUDES = ["*.tmp", "~$*.xlsx", "__pycache__/*"]
WINDOW_WIDTH = 1280
WINDOW_HEIGHT = 820
WINDOW_ASPECT_WIDTH = 64
WINDOW_ASPECT_HEIGHT = 41

APP_BG = "#f4f6f8"
HEADER_BG = "#172033"
CARD_BG = "#ffffff"
LINE = "#d8dee6"
TEXT = "#1f2933"
MUTED = "#667085"
PRIMARY = "#1f6feb"
PRIMARY_DARK = "#185abc"
DANGER = "#c7352b"
SUCCESS = "#2f855a"
UI_FONT = ("Segoe UI", 9)
TITLE_FONT = ("Segoe UI", 10, "bold")


class BackupGui:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("文件备份与恢复客户端")
        self.root.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}")
        self.root.minsize(WINDOW_WIDTH, WINDOW_HEIGHT)
        self.root.aspect(
            WINDOW_ASPECT_WIDTH,
            WINDOW_ASPECT_HEIGHT,
            WINDOW_ASPECT_WIDTH,
            WINDOW_ASPECT_HEIGHT,
        )

        self.config: AppConfig | None = None
        self.scheduler: BackupTaskScheduler | None = None
        self.tray_icon: Any | None = None
        self._exiting = False
        self.backup_items: list[dict] = []
        self.log_queue: queue.Queue[str] = queue.Queue()

        self.config_path_var = tk.StringVar(value=str(default_config_path()))
        self.server_url_var = tk.StringVar(value="http://127.0.0.1:8000")
        self.token_var = tk.StringVar(value="REPLACE_WITH_CLIENT_TOKEN")
        self.machine_id_var = tk.StringVar(value="pc1")
        self.data_dir_var = tk.StringVar(value=str(default_client_data_dir()))
        self.connection_status_var = tk.StringVar(value="未连接")
        self.task_name_var = tk.StringVar()
        self.task_type_var = tk.StringVar(value="once")
        self.schedule_time_var = tk.StringVar(value="04:00")
        self.backup_id_var = tk.StringVar()
        self.restore_id_var = tk.StringVar()
        self.restore_server_var = tk.StringVar(value="auto")

        self._apply_style()
        self._build_ui()
        self._load_config_if_exists()
        self._sync_schedule_state()
        self.root.protocol("WM_DELETE_WINDOW", self._hide_to_tray)
        self.root.bind("<Unmap>", self._on_window_unmap)
        self.root.after(100, self._drain_log_queue)

    def run(self) -> None:
        self.root.mainloop()

    def _on_window_unmap(self, event: tk.Event) -> None:
        if event.widget == self.root and not self._exiting:
            self.root.after(100, self._hide_if_minimized)

    def _hide_if_minimized(self) -> None:
        if not self._exiting and self.root.state() == "iconic":
            self._hide_to_tray()

    def _hide_to_tray(self) -> None:
        if self._exiting:
            return
        if pystray is None or Image is None or ImageDraw is None:
            self.root.iconify()
            messagebox.showwarning(
                "缺少托盘依赖",
                "未安装 pystray / pillow，暂时只能最小化到任务栏。",
            )
            return
        self._ensure_tray_icon()
        self.root.withdraw()

    def _show_from_tray(self) -> None:
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def _ensure_tray_icon(self) -> None:
        if self.tray_icon is not None:
            return
        image = self._create_tray_image()
        menu = pystray.Menu(
            pystray.MenuItem(
                "打开窗口",
                lambda: self.root.after(0, self._show_from_tray),
                default=True,
            ),
            pystray.MenuItem("启动定时器", lambda: self.root.after(0, self._start_scheduler_clicked)),
            pystray.MenuItem("停止定时器", lambda: self.root.after(0, self._stop_scheduler_clicked)),
            pystray.MenuItem("退出程序", lambda: self.root.after(0, self._exit_app)),
        )
        self.tray_icon = pystray.Icon(
            "FileBackupClient",
            image,
            "文件备份与恢复客户端",
            menu,
        )
        threading.Thread(target=self.tray_icon.run, daemon=True).start()

    def _create_tray_image(self) -> Any:
        image = Image.new("RGBA", (64, 64), (30, 96, 168, 255))
        draw = ImageDraw.Draw(image)
        draw.rectangle((14, 12, 50, 52), fill=(255, 255, 255, 255))
        draw.rectangle((20, 18, 44, 24), fill=(30, 96, 168, 255))
        draw.polygon((18, 36, 30, 48, 48, 28, 42, 22, 30, 36, 24, 30), fill=(36, 158, 92, 255))
        return image

    def _exit_app(self) -> None:
        self._exiting = True
        if self.scheduler is not None:
            self.scheduler.stop()
            self.scheduler = None
        if self.tray_icon is not None:
            self.tray_icon.stop()
            self.tray_icon = None
        self.root.destroy()

    def _apply_style(self) -> None:
        self.root.configure(bg=APP_BG)
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure(".", font=UI_FONT)
        style.configure("TFrame", background=APP_BG)
        style.configure("Card.TFrame", background=CARD_BG)
        style.configure("Section.TFrame", background=CARD_BG)
        style.configure("TLabel", background=CARD_BG, foreground=TEXT, font=UI_FONT)
        style.configure("Muted.TLabel", background=CARD_BG, foreground=MUTED, font=("Segoe UI", 8))
        style.configure("Title.TLabel", background=CARD_BG, foreground=TEXT, font=TITLE_FONT)
        style.configure("Header.TLabel", background=HEADER_BG, foreground="#ffffff", font=("Segoe UI", 12, "bold"))
        style.configure("Status.TLabel", background=HEADER_BG, foreground="#dbeafe", font=("Segoe UI", 9))
        style.configure(
            "TEntry",
            fieldbackground="#ffffff",
            bordercolor=LINE,
            lightcolor=LINE,
            darkcolor=LINE,
            padding=(6, 4),
        )
        style.configure("TButton", padding=(8, 4), background="#ffffff", foreground=TEXT, bordercolor=LINE)
        style.map("TButton", background=[("active", "#f2f4f7")])
        style.configure("Primary.TButton", background=PRIMARY, foreground="#ffffff", bordercolor=PRIMARY)
        style.map("Primary.TButton", background=[("active", PRIMARY_DARK)], foreground=[("active", "#ffffff")])
        style.configure("Danger.TButton", background="#ffffff", foreground=DANGER, bordercolor="#e3a4a0")
        style.map("Danger.TButton", background=[("active", "#fff4f2")], foreground=[("active", DANGER)])
        style.configure("TRadiobutton", background=CARD_BG, foreground=TEXT)

    def _build_ui(self) -> None:
        header = tk.Frame(self.root, bg=HEADER_BG, height=52)
        header.pack(fill=X)
        header.pack_propagate(False)
        tk.Label(
            header,
            text="文件备份与恢复客户端",
            bg=HEADER_BG,
            fg="#ffffff",
            font=("Segoe UI", 12, "bold"),
        ).pack(side=LEFT, padx=18)
        tk.Label(
            header,
            textvariable=self.connection_status_var,
            bg=HEADER_BG,
            fg="#dbeafe",
            font=("Segoe UI", 9),
        ).pack(side=RIGHT, padx=18)

        shell = tk.Frame(self.root, bg=APP_BG)
        shell.pack(fill=BOTH, expand=True, padx=12, pady=10)
        shell.columnconfigure(0, weight=1, uniform="cols")
        shell.columnconfigure(1, weight=1, uniform="cols")
        shell.rowconfigure(1, weight=1)

        self._build_connection_panel(shell)

        left = tk.Frame(shell, bg=APP_BG)
        right = tk.Frame(shell, bg=APP_BG)
        left.grid(row=1, column=0, sticky="nsew", padx=(0, 6), pady=(10, 0))
        right.grid(row=1, column=1, sticky="nsew", padx=(6, 0), pady=(10, 0))
        left.rowconfigure(0, weight=1)
        right.rowconfigure(0, weight=1)

        self._build_task_panel(left)
        self._build_backup_panel(right)
        self._build_log_panel(shell)

    def _card(self, parent: tk.Misc, title: str) -> tuple[tk.Frame, tk.Frame]:
        card = tk.Frame(parent, bg=CARD_BG, highlightbackground=LINE, highlightthickness=1)
        tk.Label(card, text=title, bg=CARD_BG, fg=TEXT, font=TITLE_FONT).pack(
            anchor="w",
            padx=11,
            pady=(7, 3),
        )
        body = tk.Frame(card, bg=CARD_BG)
        body.pack(fill=BOTH, expand=True, padx=11, pady=(0, 8))
        return card, body

    def _make_listbox(self, parent: tk.Misc, height: int) -> tk.Listbox:
        return tk.Listbox(
            parent,
            height=height,
            bg="#ffffff",
            fg=TEXT,
            selectbackground=PRIMARY,
            selectforeground="#ffffff",
            borderwidth=1,
            highlightthickness=1,
            highlightbackground=LINE,
            relief="flat",
            font=UI_FONT,
            activestyle="none",
        )

    def _build_connection_panel(self, parent: tk.Frame) -> None:
        card, frame = self._card(parent, "连接设置")
        card.grid(row=0, column=0, columnspan=2, sticky="ew")

        for column in (1, 5):
            frame.columnconfigure(column, weight=1)

        ttk.Label(frame, text="配置文件").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=3)
        ttk.Entry(frame, textvariable=self.config_path_var, width=48).grid(
            row=0,
            column=1,
            columnspan=2,
            sticky="ew",
            padx=(0, 8),
            pady=3,
        )
        ttk.Button(frame, text="浏览", command=self._browse_config).grid(row=0, column=3, sticky="ew", padx=(0, 14), pady=3)

        ttk.Label(frame, text="Server URL").grid(row=0, column=4, sticky="w", padx=(0, 8), pady=3)
        ttk.Entry(frame, textvariable=self.server_url_var, width=42).grid(
            row=0,
            column=5,
            sticky="ew",
            pady=3,
        )

        ttk.Label(frame, text="Token").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=3)
        ttk.Entry(frame, textvariable=self.token_var, width=48, show="*").grid(
            row=1,
            column=1,
            columnspan=3,
            sticky="ew",
            padx=(0, 14),
            pady=3,
        )

        ttk.Label(frame, text="主机 ID").grid(row=1, column=4, sticky="w", padx=(0, 8), pady=3)
        ttk.Entry(frame, textvariable=self.machine_id_var, width=42).grid(row=1, column=5, sticky="ew", pady=3)

        ttk.Label(frame, text="本地数据目录").grid(row=2, column=0, sticky="w", padx=(0, 8), pady=3)
        ttk.Entry(frame, textvariable=self.data_dir_var, width=48).grid(
            row=2,
            column=1,
            columnspan=2,
            sticky="ew",
            padx=(0, 8),
            pady=3,
        )
        ttk.Button(frame, text="浏览", command=lambda: self._browse_dir_into(self.data_dir_var)).grid(
            row=2,
            column=3,
            sticky="ew",
            padx=(0, 14),
            pady=3,
        )

        buttons = ttk.Frame(frame, style="Card.TFrame")
        buttons.grid(row=2, column=4, columnspan=2, sticky="e", pady=3)
        ttk.Button(buttons, text="加载配置", command=self._load_config_clicked).pack(side=LEFT, padx=(0, 6))
        ttk.Button(buttons, text="保存配置", command=self._save_config_clicked).pack(side=LEFT, padx=(0, 6))
        ttk.Button(buttons, text="管理 Servers", command=self._manage_servers_clicked).pack(side=LEFT, padx=(0, 6))
        ttk.Button(buttons, text="测试全部", command=self._test_server_clicked, style="Primary.TButton").pack(side=LEFT)

    def _build_task_panel(self, parent: tk.Frame) -> None:
        card, frame = self._card(parent, "备份任务")
        card.grid(row=0, column=0, sticky="nsew")
        parent.columnconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(5, weight=1)

        self.task_list = self._make_listbox(frame, height=3)
        self.task_list.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        self.task_list.bind("<<ListboxSelect>>", self._task_selected)

        task_buttons = ttk.Frame(frame, style="Card.TFrame")
        task_buttons.grid(row=1, column=0, sticky="w", pady=(0, 8))
        ttk.Button(task_buttons, text="新建任务", command=self._new_task_clicked).pack(side=LEFT, padx=(0, 6))
        ttk.Button(task_buttons, text="保存任务", command=self._save_task_clicked, style="Primary.TButton").pack(
            side=LEFT,
            padx=(0, 6),
        )
        ttk.Button(task_buttons, text="删除任务", command=self._delete_task_clicked, style="Danger.TButton").pack(side=LEFT)

        tk.Label(frame, text="任务设置", bg=CARD_BG, fg=MUTED, font=("Segoe UI", 9, "bold")).grid(
            row=2,
            column=0,
            sticky="w",
            pady=(0, 3),
        )
        form = ttk.Frame(frame, style="Card.TFrame")
        form.grid(row=3, column=0, sticky="ew", pady=(0, 8))
        form.columnconfigure(1, weight=1)
        self._row(form, "任务名", self.task_name_var, 0, width=42)

        ttk.Label(form, text="任务类型").grid(row=1, column=0, sticky="w", padx=6, pady=4)
        type_frame = ttk.Frame(form, style="Card.TFrame")
        type_frame.grid(row=1, column=1, sticky="w", padx=6, pady=4)
        ttk.Radiobutton(
            type_frame,
            text="单次任务",
            value="once",
            variable=self.task_type_var,
            command=self._sync_schedule_state,
        ).pack(side=LEFT, padx=(0, 12))
        ttk.Radiobutton(
            type_frame,
            text="定时任务",
            value="scheduled",
            variable=self.task_type_var,
            command=self._sync_schedule_state,
        ).pack(side=LEFT)

        ttk.Label(form, text="备份时间").grid(row=2, column=0, sticky="w", padx=6, pady=4)
        self.schedule_entry = ttk.Entry(form, textvariable=self.schedule_time_var, width=16)
        self.schedule_entry.grid(row=2, column=1, sticky="w", padx=6, pady=4)
        ttk.Label(form, text="格式 HH:MM，仅定时任务使用", style="Muted.TLabel").grid(
            row=2,
            column=2,
            sticky="w",
            padx=6,
            pady=4,
        )

        tk.Label(frame, text="备份路径", bg=CARD_BG, fg=MUTED, font=("Segoe UI", 9, "bold")).grid(
            row=4,
            column=0,
            sticky="w",
            pady=(0, 3),
        )
        self.source_list = self._make_listbox(frame, height=5)
        self.source_list.grid(row=5, column=0, sticky="nsew", pady=(0, 6))
        path_buttons = ttk.Frame(frame, style="Card.TFrame")
        path_buttons.grid(row=6, column=0, sticky="w", pady=(0, 8))
        ttk.Button(path_buttons, text="添加文件夹", command=self._add_source_dir).pack(side=LEFT, padx=(0, 6))
        ttk.Button(path_buttons, text="添加文件", command=self._add_source_file).pack(side=LEFT, padx=(0, 6))
        ttk.Button(path_buttons, text="移除选中", command=self._remove_source).pack(side=LEFT)

        run_buttons = ttk.Frame(frame, style="Card.TFrame")
        run_buttons.grid(row=7, column=0, sticky="w")
        ttk.Button(
            run_buttons,
            text="执行备份",
            command=self._run_selected_task_clicked,
            style="Primary.TButton",
        ).pack(side=LEFT, padx=(0, 6))
        ttk.Button(run_buttons, text="启动定时器", command=self._start_scheduler_clicked).pack(side=LEFT, padx=(0, 6))
        ttk.Button(run_buttons, text="停止定时器", command=self._stop_scheduler_clicked).pack(side=LEFT)

    def _build_backup_panel(self, parent: tk.Frame) -> None:
        card, frame = self._card(parent, "备份记录")
        card.grid(row=0, column=0, sticky="nsew")
        parent.columnconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)

        buttons = ttk.Frame(frame, style="Card.TFrame")
        buttons.grid(row=0, column=0, sticky="w", pady=(0, 8))
        ttk.Button(
            buttons,
            text="查询远端备份",
            command=self._list_backups_clicked,
            style="Primary.TButton",
        ).pack(side=LEFT, padx=(0, 6))

        self.backup_list = self._make_listbox(frame, height=12)
        self.backup_list.grid(row=1, column=0, sticky="nsew", pady=(0, 12))
        self.backup_list.bind("<<ListboxSelect>>", self._backup_selected_from_list)

        tk.Label(frame, text="恢复", bg=CARD_BG, fg=MUTED, font=("Segoe UI", 9, "bold")).grid(
            row=2,
            column=0,
            sticky="w",
            pady=(0, 6),
        )
        restore = ttk.Frame(frame, style="Card.TFrame")
        restore.grid(row=3, column=0, sticky="ew")
        restore.columnconfigure(1, weight=1)
        self._row(restore, "Backup ID", self.backup_id_var, 0, width=48)
        self._row(restore, "Restore ID", self.restore_id_var, 1, width=48)
        ttk.Label(restore, text="恢复来源").grid(row=2, column=0, sticky="w", padx=6, pady=4)
        self.restore_server_combo = ttk.Combobox(
            restore,
            textvariable=self.restore_server_var,
            values=["auto"],
            state="readonly",
            width=32,
        )
        self.restore_server_combo.grid(row=2, column=1, sticky="ew", padx=6, pady=4)
        restore_buttons = ttk.Frame(restore, style="Card.TFrame")
        restore_buttons.grid(row=3, column=0, columnspan=3, sticky="w", padx=6, pady=6)
        ttk.Button(restore_buttons, text="校验备份", command=self._verify_clicked).pack(side=LEFT, padx=(0, 6))
        ttk.Button(restore_buttons, text="恢复备份", command=self._restore_clicked, style="Primary.TButton").pack(
            side=LEFT,
            padx=(0, 6),
        )
        ttk.Button(restore_buttons, text="回滚恢复", command=self._rollback_clicked).pack(side=LEFT)

    def _build_log_panel(self, parent: tk.Frame) -> None:
        card, frame = self._card(parent, "日志")
        card.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        frame.columnconfigure(0, weight=1)
        scrollbar = ttk.Scrollbar(frame)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.log_text = tk.Text(
            frame,
            height=8,
            wrap="word",
            yscrollcommand=scrollbar.set,
            bg="#ffffff",
            fg=TEXT,
            insertbackground=TEXT,
            borderwidth=1,
            highlightthickness=1,
            highlightbackground=LINE,
            relief="flat",
            font=("Consolas", 9),
        )
        self.log_text.grid(row=0, column=0, sticky="ew")
        scrollbar.config(command=self.log_text.yview)

    def _row(self, parent: ttk.Frame, label: str, variable: tk.StringVar, row: int, width: int = 70) -> ttk.Entry:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=4, pady=3)
        entry = ttk.Entry(parent, textvariable=variable, width=width)
        entry.grid(row=row, column=1, sticky="ew", padx=4, pady=3)
        parent.columnconfigure(1, weight=1)
        return entry

    def _browse_config(self) -> None:
        path = filedialog.asksaveasfilename(
            title="选择配置文件",
            defaultextension=".yaml",
            filetypes=[("YAML", "*.yaml *.yml"), ("All files", "*.*")],
        )
        if path:
            self.config_path_var.set(path)

    def _browse_dir_into(self, variable: tk.StringVar) -> None:
        path = filedialog.askdirectory()
        if path:
            variable.set(path.replace("\\", "/"))

    def _add_source_dir(self) -> None:
        path = filedialog.askdirectory(title="选择要备份的文件夹")
        if path:
            self.source_list.insert(END, path.replace("\\", "/"))

    def _add_source_file(self) -> None:
        paths = filedialog.askopenfilenames(title="选择要备份的文件")
        for path in paths:
            self.source_list.insert(END, path.replace("\\", "/"))

    def _remove_source(self) -> None:
        for index in reversed(self.source_list.curselection()):
            self.source_list.delete(index)

    def _sync_schedule_state(self) -> None:
        if getattr(self, "schedule_entry", None) is None:
            return
        state = "normal" if self.task_type_var.get() == "scheduled" else "disabled"
        self.schedule_entry.configure(state=state)

    def _log(self, message: str) -> None:
        self.log_queue.put(message)

    def _drain_log_queue(self) -> None:
        while True:
            try:
                message = self.log_queue.get_nowait()
            except queue.Empty:
                break
            self.log_text.insert(END, message + "\n")
            self.log_text.see(END)
        self.root.after(100, self._drain_log_queue)

    def _run_worker(self, title: str, func) -> None:
        def worker() -> None:
            self._log(f"[{title}] 开始")
            try:
                result = func()
                if result is not None:
                    self._log(json.dumps(result, ensure_ascii=False, indent=2, default=str))
                self._log(f"[{title}] 完成")
            except Exception as exc:
                self._log(f"[{title}] 失败: {exc}")
                if title == "测试连接":
                    self.root.after(0, lambda: self.connection_status_var.set("连接失败"))
                error_message = str(exc)
                self.root.after(0, lambda: messagebox.showerror(title, error_message))

        threading.Thread(target=worker, daemon=True).start()

    def _load_config_if_exists(self) -> None:
        if Path(self.config_path_var.get()).exists():
            self._load_config_clicked()

    def _load_config_clicked(self) -> None:
        try:
            self.config = load_config(Path(self.config_path_var.get()))
            self.server_url_var.set(self.config.server.base_url)
            self.token_var.set(self.config.server.token)
            self.machine_id_var.set(self.config.client.machine_id)
            self.data_dir_var.set(str(self.config.client.data_dir))
            self.restore_server_combo.configure(
                values=["auto", *[server.id for server in self.config.enabled_servers()]]
            )
            self.restore_server_var.set("auto")
            self._fill_task_list()
            self.connection_status_var.set("配置已加载")
            self._log(f"已加载配置: {self.config_path_var.get()}")
        except Exception as exc:
            messagebox.showerror("加载配置失败", str(exc))

    def _fill_task_list(self, selected_task_name: str | None = None) -> None:
        self.task_list.delete(0, END)
        if self.config is None:
            return
        selected_index = 0
        for index, task in enumerate(self.config.tasks):
            label = f"{task.name} | {'定时 ' + task.schedule_time if task.schedule_enabled else '单次'}"
            self.task_list.insert(END, label)
            if selected_task_name and task.name == selected_task_name:
                selected_index = index
        if self.config.tasks:
            self.task_list.selection_clear(0, END)
            self.task_list.selection_set(selected_index)
            self.task_list.see(selected_index)
            self._load_task_to_form(self.config.tasks[selected_index].name)

    def _task_selected(self, _: object) -> None:
        selection = self.task_list.curselection()
        if not selection:
            return
        task_name = self.task_list.get(selection[0]).split("|", 1)[0].strip()
        self._load_task_to_form(task_name)

    def _load_task_to_form(self, task_name: str) -> None:
        if self.config is None:
            return
        task = self.config.get_task(task_name)
        self.task_name_var.set(task.name)
        self.task_type_var.set("scheduled" if task.schedule_enabled else "once")
        self.schedule_time_var.set(task.schedule_time or "04:00")
        self.source_list.delete(0, END)
        for root in task.roots:
            self.source_list.insert(END, str(root.path))
        self._sync_schedule_state()

    def _new_task_clicked(self) -> None:
        self.task_list.selection_clear(0, END)
        self.task_name_var.set("new_task")
        self.task_type_var.set("once")
        self.schedule_time_var.set("04:00")
        self.source_list.delete(0, END)
        self._sync_schedule_state()

    def _save_config_clicked(self) -> None:
        path = Path(self.config_path_var.get())
        path.parent.mkdir(parents=True, exist_ok=True)
        data = self._config_to_dict()
        if not data["tasks"]:
            try:
                data["tasks"].append(self._task_from_form())
            except ValueError:
                pass
        path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
        self._load_config_clicked()

    def _save_task_clicked(self) -> None:
        task = self._task_from_form()
        data = self._config_to_dict()
        data["tasks"] = [item for item in data["tasks"] if item["name"] != task["name"]]
        data["tasks"].append(task)
        path = Path(self.config_path_var.get())
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
        self.config = load_config(path)
        self.server_url_var.set(self.config.server.base_url)
        self.token_var.set(self.config.server.token)
        self.machine_id_var.set(self.config.client.machine_id)
        self.data_dir_var.set(str(self.config.client.data_dir))
        self._fill_task_list(task["name"])
        self.connection_status_var.set("配置已加载")
        self._log(f"已保存任务: {task['name']}")

    def _delete_task_clicked(self) -> None:
        task_name = self.task_name_var.get().strip()
        if not task_name:
            return
        if not messagebox.askyesno("确认删除任务", f"删除任务 {task_name}？已上传的备份不会被删除。"):
            return
        data = self._config_to_dict()
        data["tasks"] = [item for item in data["tasks"] if item["name"] != task_name]
        Path(self.config_path_var.get()).write_text(
            yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        self._load_config_clicked()

    def _config_to_dict(self) -> dict:
        data_dir = self.data_dir_var.get().strip()
        tasks = []
        servers = []
        if self.config is not None:
            for task in self.config.tasks:
                tasks.append(
                    {
                        "name": task.name,
                        "enabled": task.enabled,
                        "schedule_enabled": task.schedule_enabled,
                        "schedule_time": task.schedule_time,
                        "roots": [
                            {
                                "path": str(root.path),
                                "source_type": root.source_type,
                                "recursive": root.recursive,
                                "include": root.include,
                                "exclude": root.exclude,
                            }
                            for root in task.roots
                        ],
                    }
                )
            for index, server in enumerate(self.config.servers):
                servers.append(
                    {
                        "id": server.id,
                        "name": server.name,
                        "base_url": (
                            self.server_url_var.get().strip()
                            if index == 0
                            else server.base_url
                        ),
                        "token": self.token_var.get().strip() if index == 0 else server.token,
                        "timeout_seconds": server.timeout_seconds,
                        "verify_tls": (
                            not self.server_url_var.get().lower().startswith("http://")
                            if index == 0
                            else server.verify_tls
                        ),
                        "enabled": server.enabled,
                    }
                )
        if not servers:
            servers.append(
                {
                    "id": "server-1",
                    "name": "Server 1",
                    "base_url": self.server_url_var.get().strip(),
                    "token": self.token_var.get().strip(),
                    "timeout_seconds": 60,
                    "verify_tls": not self.server_url_var.get().lower().startswith("http://"),
                    "enabled": True,
                }
            )
        return {
            "client": {
                "machine_id": self.machine_id_var.get().strip(),
                "display_name": (
                    self.config.client.display_name
                    if self.config is not None
                    else self.machine_id_var.get().strip()
                ),
                "timezone": "Asia/Shanghai",
                "data_dir": data_dir,
                "temp_dir": str(Path(data_dir) / "tmp"),
                "outbox_dir": str(Path(data_dir) / "outbox"),
            },
            "servers": servers,
            "backup": {
                "schedule_enabled": False,
                "archive_format": "tar.gz",
                "copy_stability_check": True,
                "stability_check_interval_seconds": 1,
                "required_copies": len([server for server in servers if server["enabled"]]),
                "keep_local_until_all_uploaded": True,
            },
            "restore": {
                "create_rollback_snapshot": True,
                "allowed_roots": [source for source in self._all_source_roots(tasks)] or [data_dir],
                "rollback_dir": str(Path(data_dir) / "rollback"),
                "require_same_machine_id": True,
            },
            "transfer": {
                "inbox_dir": str(
                    self.config.transfer.inbox_dir
                    if self.config is not None
                    else Path.home() / "Downloads" / "FileBackup Inbox"
                ),
                "temp_dir": str(
                    self.config.transfer.temp_dir
                    if self.config is not None
                    else Path(data_dir) / "transfer-tmp"
                ),
                "allowed_send_roots": (
                    [str(path) for path in self.config.transfer.allowed_send_roots]
                    if self.config is not None
                    else []
                ),
                "require_confirmation": True,
                "overwrite_existing": False,
            },
            "tasks": tasks,
        }

    def _task_from_form(self) -> dict:
        name = self.task_name_var.get().strip()
        if not name:
            raise ValueError("任务名不能为空")
        sources = [self.source_list.get(index).strip() for index in range(self.source_list.size())]
        if not sources:
            raise ValueError("请至少添加一个备份路径")
        return {
            "name": name,
            "enabled": True,
            "schedule_enabled": self.task_type_var.get() == "scheduled",
            "schedule_time": self.schedule_time_var.get().strip() or "04:00",
            "roots": [
                {
                    "path": source,
                    "source_type": "auto",
                    "recursive": True,
                    "include": ["*"],
                    "exclude": DEFAULT_EXCLUDES,
                }
                for source in sources
            ],
        }

    def _all_source_roots(self, tasks: list[dict]) -> list[str]:
        roots: list[str] = []
        for task in tasks:
            for root in task.get("roots", []):
                raw_path = root.get("path")
                if not raw_path:
                    continue
                path = Path(raw_path)
                roots.append(str(path if path.suffix == "" else path.parent))
        return sorted(set(roots))

    def _require_config(self) -> AppConfig:
        if self.config is None:
            self.config = load_config(Path(self.config_path_var.get()))
        return self.config

    def _manage_servers_clicked(self) -> None:
        data = self._config_to_dict()
        servers = data["servers"]
        window = tk.Toplevel(self.root)
        window.title("管理备份 Servers")
        window.geometry("820x430")
        frame = ttk.Frame(window, padding=14)
        frame.pack(fill="both", expand=True)
        server_list = tk.Listbox(frame, width=28, activestyle="none")
        server_list.grid(row=0, column=0, rowspan=8, sticky="nsew", padx=(0, 12))
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(1, weight=1)

        server_id_var = tk.StringVar()
        name_var = tk.StringVar()
        url_var = tk.StringVar()
        token_var = tk.StringVar()
        enabled_var = tk.BooleanVar(value=True)
        fields = [
            ("Server ID", server_id_var, False),
            ("名称", name_var, False),
            ("URL", url_var, False),
            ("Token", token_var, True),
        ]
        for row, (label, variable, secret) in enumerate(fields):
            ttk.Label(frame, text=label).grid(row=row, column=1, sticky="w", pady=6)
            ttk.Entry(frame, textvariable=variable, show="*" if secret else "").grid(
                row=row,
                column=2,
                sticky="ew",
                pady=6,
            )
        ttk.Checkbutton(frame, text="启用", variable=enabled_var).grid(
            row=4,
            column=1,
            columnspan=2,
            sticky="w",
            pady=6,
        )

        def refresh(selected: int = 0) -> None:
            server_list.delete(0, END)
            for server in servers:
                marker = "启用" if server.get("enabled", True) else "停用"
                server_list.insert(END, f"{server['id']} | {marker}")
            if servers:
                selected = min(max(selected, 0), len(servers) - 1)
                server_list.selection_set(selected)
                load_selected()

        def load_selected(_: object | None = None) -> None:
            selection = server_list.curselection()
            if not selection:
                return
            server = servers[selection[0]]
            server_id_var.set(server["id"])
            name_var.set(server.get("name") or server["id"])
            url_var.set(server["base_url"])
            token_var.set(server["token"])
            enabled_var.set(bool(server.get("enabled", True)))

        def current_server() -> dict:
            server_id = server_id_var.get().strip()
            url = url_var.get().strip()
            token = token_var.get().strip()
            if not server_id or not url or not token:
                raise ValueError("Server ID、URL 和 Token 不能为空")
            return {
                "id": server_id,
                "name": name_var.get().strip() or server_id,
                "base_url": url.rstrip("/"),
                "token": token,
                "timeout_seconds": 60,
                "verify_tls": not url.lower().startswith("http://"),
                "enabled": enabled_var.get(),
            }

        def save_selected() -> None:
            selection = server_list.curselection()
            item = current_server()
            if selection:
                index = selection[0]
                if any(
                    server["id"] == item["id"]
                    for other_index, server in enumerate(servers)
                    if other_index != index
                ):
                    raise ValueError(f"Server ID 重复: {item['id']}")
                servers[index] = item
                refresh(index)
            else:
                if any(server["id"] == item["id"] for server in servers):
                    raise ValueError(f"Server ID 重复: {item['id']}")
                servers.append(item)
                refresh(len(servers) - 1)

        def new_server() -> None:
            server_list.selection_clear(0, END)
            server_id_var.set(f"server-{len(servers) + 1}")
            name_var.set(f"Server {len(servers) + 1}")
            url_var.set("http://")
            token_var.set("")
            enabled_var.set(True)

        def remove_selected() -> None:
            selection = server_list.curselection()
            if not selection:
                return
            if len(servers) <= 1:
                raise ValueError("至少保留一台 Server")
            servers.pop(selection[0])
            refresh(0)

        def apply_all() -> None:
            save_selected()
            enabled_count = sum(bool(server.get("enabled", True)) for server in servers)
            if enabled_count < 1:
                raise ValueError("至少启用一台 Server")
            data["backup"]["required_copies"] = enabled_count
            path = Path(self.config_path_var.get())
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )
            window.destroy()
            self._load_config_clicked()

        server_list.bind("<<ListboxSelect>>", load_selected)
        buttons = ttk.Frame(frame)
        buttons.grid(row=6, column=1, columnspan=2, sticky="w", pady=(16, 0))
        ttk.Button(buttons, text="新建", command=lambda: self._safe_gui_action(new_server)).pack(side=LEFT, padx=(0, 6))
        ttk.Button(buttons, text="保存当前", command=lambda: self._safe_gui_action(save_selected)).pack(side=LEFT, padx=(0, 6))
        ttk.Button(buttons, text="移除", command=lambda: self._safe_gui_action(remove_selected)).pack(side=LEFT, padx=(0, 6))
        ttk.Button(buttons, text="应用并关闭", command=lambda: self._safe_gui_action(apply_all)).pack(side=LEFT)
        refresh(0)

    def _safe_gui_action(self, action) -> None:
        try:
            action()
        except Exception as exc:
            messagebox.showerror("操作失败", str(exc))

    def _test_server_clicked(self) -> None:
        self.connection_status_var.set("连接检测中")

        def task() -> dict[str, Any]:
            config = self._require_config()
            results: dict[str, Any] = {}
            for server in config.enabled_servers():
                health = BackupServerClient(server).health()
                if health.get("server_id") and health["server_id"] != server.id:
                    raise ValueError(
                        f"{server.id} 实际连接到不同 Server: {health['server_id']}"
                    )
                results[server.id] = health
            self.root.after(0, lambda: self.connection_status_var.set("全部已连接"))
            return results

        self._run_worker("测试连接", task)

    def _run_selected_task_clicked(self) -> None:
        task_name = self.task_name_var.get().strip()
        try:
            self._save_task_clicked()
        except Exception as exc:
            messagebox.showerror("保存任务失败", str(exc))
            return

        def task() -> dict:
            config = self._require_config()
            task = config.get_task(task_name)
            return run_backup_for_task(config, task).__dict__

        self._run_worker("执行当前任务", task)

    def _start_scheduler_clicked(self) -> None:
        self._save_config_clicked()
        config = self._require_config()
        if self.scheduler is not None:
            self.scheduler.stop()
        self.scheduler = BackupTaskScheduler(config, self._log)
        self.scheduler.start()
        self._log("定时器已启动。Client 保持打开时，到点会自动备份定时任务。")

    def _stop_scheduler_clicked(self) -> None:
        if self.scheduler is not None:
            self.scheduler.stop()
            self.scheduler = None
        self._log("定时器已停止")

    def _list_backups_clicked(self) -> None:
        def task() -> dict:
            config = self._require_config()
            response = list_backups_across_servers(
                config,
                server_id="all",
                machine_id=config.client.machine_id,
                task_name=None,
                limit=200,
            )
            self.backup_items = response.get("items", [])
            self.root.after(0, self._fill_backup_list)
            return response

        self._run_worker("查询远端备份", task)

    def _fill_backup_list(self) -> None:
        self.backup_list.delete(0, END)
        for item in self.backup_items:
            self.backup_list.insert(
                END,
                f"{item['backup_id']} | {item['task_name']} | {item['copy_status']} | {item['created_at']}",
            )

    def _backup_selected_from_list(self, _: object) -> None:
        selection = self.backup_list.curselection()
        if not selection:
            return
        self.backup_id_var.set(self.backup_items[selection[0]]["backup_id"])

    def _verify_clicked(self) -> None:
        self._run_worker(
            "校验备份",
            lambda: run_verify(
                self._require_config(),
                self.backup_id_var.get().strip(),
                server_id=self.restore_server_var.get(),
            ).__dict__,
        )

    def _restore_clicked(self) -> None:
        if not messagebox.askyesno("确认恢复", "恢复会覆盖目标文件，执行前会创建 rollback 快照。继续吗？"):
            return

        def task() -> dict:
            result = run_restore(
                self._require_config(),
                self.backup_id_var.get().strip(),
                server_id=self.restore_server_var.get(),
            )
            self.root.after(0, lambda: self.restore_id_var.set(result.restore_id))
            return result.__dict__

        self._run_worker("恢复备份", task)

    def _rollback_clicked(self) -> None:
        if not messagebox.askyesno("确认回滚", "回滚会恢复到本次 restore 之前的状态。继续吗？"):
            return
        self._run_worker(
            "回滚恢复",
            lambda: rollback_restore(self._require_config(), self.restore_id_var.get().strip()).__dict__,
        )


def main() -> None:
    BackupGui().run()


if __name__ == "__main__":
    main()
