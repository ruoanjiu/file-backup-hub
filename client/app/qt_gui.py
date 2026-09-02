from __future__ import annotations

import json
import re
import socket
import sys
from pathlib import Path
from typing import Any, Callable

import yaml
from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTime, Qt, Signal
from PySide6.QtGui import QColor, QDragEnterEvent, QDropEvent, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from client.app.backup import run_backup_for_task
from client.app.config import (
    AppConfig,
    ServerSection,
    default_client_data_dir,
    default_config_path,
    load_config,
)
from client.app.restore import run_restore, run_verify
from client.app.transfer import receive_transfer, send_transfer
from client.app.uploader import BackupServerClient, list_backups_across_servers


NAVY = "#0d2038"
BLUE = "#146ef5"
GREEN = "#18a66a"
AMBER = "#d99016"
TEXT = "#18212f"
MUTED = "#6b7789"
SURFACE = "#ffffff"
BG = "#f4f6f9"
BORDER = "#dfe4eb"


APP_STYLE = f"""
QMainWindow, QWidget#root {{ background: {BG}; color: {TEXT}; }}
QFrame#sidebar {{ background: {NAVY}; border: none; }}
QLabel#brand {{ color: white; font-size: 20px; font-weight: 700; }}
QPushButton#nav {{ background: transparent; color: #d9e4f3; text-align: left; padding: 12px 18px; border: none; border-radius: 8px; font-size: 14px; }}
QPushButton#nav:hover {{ background: #173455; color: white; }}
QPushButton#nav:checked {{ background: {BLUE}; color: white; font-weight: 600; }}
QFrame#card {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 12px; }}
QLabel#pageTitle {{ font-size: 26px; font-weight: 750; color: {TEXT}; }}
QLabel#sectionTitle {{ font-size: 16px; font-weight: 700; color: {TEXT}; }}
QLabel#muted {{ color: {MUTED}; }}
QPushButton {{ background: white; color: {TEXT}; border: 1px solid {BORDER}; border-radius: 8px; padding: 9px 15px; font-weight: 600; }}
QPushButton:hover {{ border-color: {BLUE}; color: {BLUE}; }}
QPushButton#primary {{ background: {BLUE}; color: white; border: 1px solid {BLUE}; }}
QPushButton#primary:hover {{ background: #0e5fd8; }}
QPushButton#success {{ background: {GREEN}; color: white; border: 1px solid {GREEN}; }}
QLineEdit, QComboBox, QTimeEdit {{ background: white; border: 1px solid {BORDER}; border-radius: 7px; padding: 8px; min-height: 22px; }}
QListWidget, QTableWidget {{ background: white; border: 1px solid {BORDER}; border-radius: 8px; gridline-color: #edf0f4; outline: none; }}
QListWidget::item {{ padding: 10px; border-bottom: 1px solid #eef1f5; }}
QListWidget::item:selected {{ background: #eaf2ff; color: {TEXT}; }}
QHeaderView::section {{ background: #f8fafc; color: {MUTED}; border: none; border-bottom: 1px solid {BORDER}; padding: 9px; font-weight: 600; }}
"""


class WorkerSignals(QObject):
    result = Signal(object)
    error = Signal(str)
    finished = Signal()


class Worker(QRunnable):
    def __init__(self, function: Callable[[], Any]) -> None:
        super().__init__()
        self.function = function
        self.signals = WorkerSignals()

    def run(self) -> None:
        try:
            self.signals.result.emit(self.function())
        except Exception as exc:
            self.signals.error.emit(str(exc))
        finally:
            self.signals.finished.emit()


class DropZone(QFrame):
    paths_dropped = Signal(list)

    def __init__(self) -> None:
        super().__init__()
        self.setAcceptDrops(True)
        self.setMinimumHeight(190)
        self.setStyleSheet(
            f"QFrame {{ background: #f8fbff; border: 2px dashed #78aaf7; border-radius: 14px; }}"
        )
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon = QLabel("＋")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setStyleSheet(f"color: {BLUE}; font-size: 44px; font-weight: 700; border: none;")
        title = QLabel("拖拽文件或文件夹到此处")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size: 18px; font-weight: 700; border: none;")
        note = QLabel("发送只读取源文件，不删除、不移动、不修改")
        note.setObjectName("muted")
        note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        note.setStyleSheet(f"color: {MUTED}; border: none;")
        layout.addWidget(icon)
        layout.addWidget(title)
        layout.addWidget(note)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        paths = [Path(url.toLocalFile()) for url in event.mimeData().urls() if url.isLocalFile()]
        if paths:
            self.paths_dropped.emit(paths)
        event.acceptProposedAction()


def card(title: str | None = None) -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName("card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(18, 16, 18, 16)
    layout.setSpacing(11)
    if title:
        label = QLabel(title)
        label.setObjectName("sectionTitle")
        layout.addWidget(label)
    return frame, layout


class BackupTaskDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("新建备份任务")
        self.resize(660, 480)
        self.name_edit = QLineEdit("daily_backup")
        self.sources = QListWidget()
        self.schedule_enabled = QCheckBox("每天自动备份")
        self.schedule_time = QTimeEdit(QTime(4, 0))
        form = QFormLayout()
        form.addRow("任务名称", self.name_edit)
        form.addRow("备份时间", self.schedule_time)
        root = QVBoxLayout(self)
        root.addLayout(form)
        root.addWidget(self.schedule_enabled)
        source_header = QHBoxLayout()
        source_header.addWidget(QLabel("要备份的文件和文件夹"))
        add_folder = QPushButton("添加文件夹")
        add_files = QPushButton("添加文件")
        source_header.addStretch()
        source_header.addWidget(add_folder)
        source_header.addWidget(add_files)
        root.addLayout(source_header)
        root.addWidget(self.sources)
        safety = QLabel("这些来源只会被读取和复制，备份程序不会删除或修改原文件。")
        safety.setStyleSheet(f"color: {GREEN};")
        root.addWidget(safety)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Save
        )
        root.addWidget(buttons)
        add_folder.clicked.connect(self.add_folder)
        add_files.clicked.connect(self.add_files)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

    def add_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择备份文件夹")
        if path:
            self.sources.addItem(path)

    def add_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "选择备份文件")
        for path in paths:
            self.sources.addItem(path)

    def task_data(self) -> dict[str, Any]:
        sources = [self.sources.item(index).text() for index in range(self.sources.count())]
        if not self.name_edit.text().strip() or not sources:
            raise ValueError("请填写任务名称并至少选择一个文件或文件夹")
        return {
            "name": self.name_edit.text().strip(),
            "enabled": True,
            "schedule_enabled": self.schedule_enabled.isChecked(),
            "schedule_time": self.schedule_time.time().toString("HH:mm"),
            "roots": [
                {
                    "path": source,
                    "source_type": "auto",
                    "recursive": True,
                    "include": ["*"],
                    "exclude": ["*.tmp", "~$*.xlsx", "__pycache__/*"],
                }
                for source in sources
            ],
        }


class FirstRunDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("首次设置 File Backup")
        self.resize(660, 520)
        hostname = re.sub(r"[^A-Za-z0-9_.-]", "-", socket.gethostname()).strip("-")
        self.server_url = QLineEdit("http://127.0.0.1:8000")
        self.server_id = QLineEdit("server-a")
        self.pairing_code = QLineEdit()
        self.pairing_code.setPlaceholderText("Server Manager 中生成的六位数字")
        self.device_id = QLineEdit(hostname or "device-1")
        self.display_name = QLineEdit(socket.gethostname() or "我的设备")
        self.data_dir = QLineEdit(str(default_client_data_dir()))
        self.inbox_dir = QLineEdit(str(Path.home() / "Downloads" / "FileBackup Inbox"))
        root = QVBoxLayout(self)
        title = QLabel("加入已有备份空间")
        title.setStyleSheet("font-size: 22px; font-weight: 750;")
        root.addWidget(title)
        note = QLabel("先在 Server Manager 生成一次性配对码，然后在这里完成设备加入。")
        note.setObjectName("muted")
        root.addWidget(note)
        form = QFormLayout()
        form.addRow("Server URL", self.server_url)
        form.addRow("Server ID", self.server_id)
        form.addRow("六位配对码", self.pairing_code)
        form.addRow("Device ID", self.device_id)
        form.addRow("设备名称", self.display_name)
        form.addRow("本地数据目录", self.data_dir)
        form.addRow("默认接收箱", self.inbox_dir)
        root.addLayout(form)
        safety = QLabel("备份和传送只读取源文件；接收路径由本机决定。")
        safety.setStyleSheet(f"color: {GREEN};")
        root.addWidget(safety)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("配对并完成")
        root.addWidget(buttons)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

    def values(self) -> dict[str, str]:
        values = {
            "server_url": self.server_url.text().strip().rstrip("/"),
            "server_id": self.server_id.text().strip(),
            "pairing_code": self.pairing_code.text().strip(),
            "device_id": self.device_id.text().strip(),
            "display_name": self.display_name.text().strip(),
            "data_dir": self.data_dir.text().strip(),
            "inbox_dir": self.inbox_dir.text().strip(),
        }
        if not all(values.values()) or len(values["pairing_code"]) != 6:
            raise ValueError("所有字段都必须填写，配对码必须是六位数字")
        return values


class SendDialog(QDialog):
    def __init__(
        self,
        paths: list[Path],
        devices: list[dict[str, Any]],
        servers: list[Any],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.paths = paths
        self.setWindowTitle("发送文件")
        self.resize(620, 420)
        root = QVBoxLayout(self)
        root.addWidget(QLabel("准备发送"))
        file_list = QListWidget()
        for path in paths:
            file_list.addItem(str(path))
        root.addWidget(file_list)
        form = QFormLayout()
        self.device_combo = QComboBox()
        for device in devices:
            self.device_combo.addItem(
                f"{device.get('display_name')}  ·  {device.get('device_id')}",
                device.get("device_id"),
            )
        self.server_combo = QComboBox()
        self.server_combo.addItem("自动选择", "auto")
        for server in servers:
            self.server_combo.addItem(server.name, server.id)
        form.addRow("接收设备", self.device_combo)
        form.addRow("中转 Server", self.server_combo)
        root.addLayout(form)
        note = QLabel("接收路径由对方设备本地决定；发送者不能指定对方的绝对路径。")
        note.setObjectName("muted")
        root.addWidget(note)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("发送")
        root.addWidget(buttons)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

    @property
    def receiver_device_id(self) -> str:
        return str(self.device_combo.currentData() or "")

    @property
    def server_id(self) -> str:
        return str(self.server_combo.currentData() or "auto")


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("File Backup")
        self.resize(1480, 920)
        self.setMinimumSize(1180, 760)
        self.config_path = default_config_path()
        self.config: AppConfig | None = None
        self.devices: list[dict[str, Any]] = []
        self.backups: list[dict[str, Any]] = []
        self.thread_pool = QThreadPool.globalInstance()
        self.root = QWidget()
        self.root.setObjectName("root")
        self.setCentralWidget(self.root)
        outer = QHBoxLayout(self.root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._build_sidebar())
        self.pages = QStackedWidget()
        outer.addWidget(self.pages, 1)
        self._build_pages()
        self._load_config()

    def _build_sidebar(self) -> QWidget:
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(236)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(18, 24, 18, 20)
        brand = QLabel("◈  File Backup")
        brand.setObjectName("brand")
        layout.addWidget(brand)
        layout.addSpacing(22)
        self.nav_buttons: list[QPushButton] = []
        for index, text in enumerate(["总览", "备份", "文件传送", "恢复", "设备", "设置"]):
            button = QPushButton(text)
            button.setObjectName("nav")
            button.setCheckable(True)
            button.clicked.connect(lambda checked=False, page=index: self._switch_page(page))
            layout.addWidget(button)
            self.nav_buttons.append(button)
        layout.addStretch()
        self.global_status = QLabel("● 正在读取配置")
        self.global_status.setStyleSheet("color: #d9e4f3; padding: 12px;")
        self.global_status.setWordWrap(True)
        layout.addWidget(self.global_status)
        self.nav_buttons[0].setChecked(True)
        return sidebar

    def _switch_page(self, index: int) -> None:
        self.pages.setCurrentIndex(index)
        for button_index, button in enumerate(self.nav_buttons):
            button.setChecked(button_index == index)
        if index in {0, 2, 4}:
            self.refresh_dashboard()

    def _page_shell(self, title: str, subtitle: str) -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(28, 24, 28, 28)
        layout.setSpacing(16)
        heading = QLabel(title)
        heading.setObjectName("pageTitle")
        layout.addWidget(heading)
        note = QLabel(subtitle)
        note.setObjectName("muted")
        layout.addWidget(note)
        scroll.setWidget(body)
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.addWidget(scroll)
        return page, layout

    def _build_pages(self) -> None:
        self.pages.addWidget(self._build_overview_page())
        self.pages.addWidget(self._build_backup_page())
        self.pages.addWidget(self._build_transfer_page())
        self.pages.addWidget(self._build_restore_page())
        self.pages.addWidget(self._build_devices_page())
        self.pages.addWidget(self._build_settings_page())

    def _build_overview_page(self) -> QWidget:
        page, layout = self._page_shell("总览", "双 Server 备份状态、设备和文件传送集中在一个页面。")
        self.server_cards_row = QHBoxLayout()
        layout.addLayout(self.server_cards_row)
        middle = QHBoxLayout()
        self.drop_zone = DropZone()
        self.drop_zone.paths_dropped.connect(self.open_send_dialog)
        middle.addWidget(self.drop_zone, 3)
        device_card, device_layout = card("设备")
        self.quick_devices = QListWidget()
        self.quick_devices.setMinimumWidth(300)
        device_layout.addWidget(self.quick_devices)
        middle.addWidget(device_card, 1)
        layout.addLayout(middle)
        transfer_card, transfer_layout = card("传输队列与接收箱")
        self.overview_transfer_table = self._table(["方向", "设备", "状态", "大小"])
        transfer_layout.addWidget(self.overview_transfer_table)
        layout.addWidget(transfer_card)
        return page

    def _build_backup_page(self) -> QWidget:
        page, layout = self._page_shell("备份", "创建只读备份任务；同一个包自动复制到所有启用的 Server。")
        toolbar = QHBoxLayout()
        new_button = QPushButton("新建备份任务")
        new_button.setObjectName("primary")
        run_button = QPushButton("立即备份")
        refresh_button = QPushButton("刷新")
        toolbar.addWidget(new_button)
        toolbar.addWidget(run_button)
        toolbar.addWidget(refresh_button)
        toolbar.addStretch()
        layout.addLayout(toolbar)
        backup_card, backup_layout = card("备份任务")
        self.task_list = QListWidget()
        backup_layout.addWidget(self.task_list)
        layout.addWidget(backup_card)
        self.backup_result = QLabel("选择任务后可以立即备份。")
        self.backup_result.setObjectName("muted")
        layout.addWidget(self.backup_result)
        new_button.clicked.connect(self.new_backup_task)
        run_button.clicked.connect(self.run_selected_backup)
        refresh_button.clicked.connect(self._fill_tasks)
        return page

    def _build_transfer_page(self) -> QWidget:
        page, layout = self._page_shell(
            "文件传送",
            "拖入文件并选择设备；当前版本自动选择可用 Server 中转，局域网直传将在下一阶段加入。",
        )
        toolbar = QHBoxLayout()
        send_file = QPushButton("发送文件")
        send_file.setObjectName("primary")
        send_folder = QPushButton("发送文件夹")
        refresh = QPushButton("刷新接收箱")
        receive = QPushButton("接收选中")
        receive.setObjectName("success")
        toolbar.addWidget(send_file)
        toolbar.addWidget(send_folder)
        toolbar.addWidget(refresh)
        toolbar.addWidget(receive)
        toolbar.addStretch()
        layout.addLayout(toolbar)
        transfer_card, transfer_layout = card("接收箱")
        self.inbox_table = self._table(["Transfer ID", "发送设备", "状态", "文件数", "大小", "Server"])
        transfer_layout.addWidget(self.inbox_table)
        layout.addWidget(transfer_card)
        send_file.clicked.connect(self.choose_send_files)
        send_folder.clicked.connect(self.choose_send_folder)
        refresh.clicked.connect(self.refresh_dashboard)
        receive.clicked.connect(self.receive_selected_transfer)
        return page

    def _build_restore_page(self) -> QWidget:
        page, layout = self._page_shell("恢复", "默认恢复到新目录；也可以选择指定 Server 或自动回退。")
        toolbar = QHBoxLayout()
        refresh = QPushButton("查询备份")
        verify = QPushButton("校验选中备份")
        restore = QPushButton("恢复到新目录")
        restore.setObjectName("primary")
        self.restore_server_combo = QComboBox()
        toolbar.addWidget(refresh)
        toolbar.addWidget(QLabel("恢复来源"))
        toolbar.addWidget(self.restore_server_combo)
        toolbar.addWidget(verify)
        toolbar.addWidget(restore)
        toolbar.addStretch()
        layout.addLayout(toolbar)
        restore_card, restore_layout = card("备份版本")
        self.backup_table = self._table(["Backup ID", "任务", "时间", "副本状态", "文件数"])
        restore_layout.addWidget(self.backup_table)
        layout.addWidget(restore_card)
        refresh.clicked.connect(self.refresh_backups)
        verify.clicked.connect(self.verify_selected_backup)
        restore.clicked.connect(self.restore_selected_backup)
        return page

    def _build_devices_page(self) -> QWidget:
        page, layout = self._page_shell("设备", "设备名称可以修改，内部 device_id 始终保持不变。")
        toolbar = QHBoxLayout()
        refresh = QPushButton("刷新设备")
        pair = QPushButton("输入配对码")
        pair.setObjectName("primary")
        rename = QPushButton("修改本机名称")
        toolbar.addWidget(refresh)
        toolbar.addWidget(pair)
        toolbar.addWidget(rename)
        toolbar.addStretch()
        layout.addLayout(toolbar)
        device_card, device_layout = card("我的设备")
        self.devices_table = self._table(["设备名称", "Device ID", "配对状态", "最后在线"])
        device_layout.addWidget(self.devices_table)
        layout.addWidget(device_card)
        refresh.clicked.connect(self.refresh_dashboard)
        pair.clicked.connect(self.pair_device_dialog)
        rename.clicked.connect(self.rename_this_device)
        return page

    def _build_settings_page(self) -> QWidget:
        page, layout = self._page_shell("设置", "接收路径只能由本机决定；发送者无法远程指定。")
        settings_card, settings_layout = card("本机目录")
        form = QFormLayout()
        self.inbox_edit = QLineEdit()
        choose_inbox = QPushButton("选择")
        inbox_row = QHBoxLayout()
        inbox_row.addWidget(self.inbox_edit)
        inbox_row.addWidget(choose_inbox)
        form.addRow("默认接收箱", inbox_row)
        self.config_path_label = QLabel(str(self.config_path))
        form.addRow("配置文件", self.config_path_label)
        settings_layout.addLayout(form)
        save = QPushButton("保存设置")
        save.setObjectName("primary")
        settings_layout.addWidget(save, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(settings_card)
        choose_inbox.clicked.connect(self.choose_inbox)
        save.clicked.connect(self.save_settings)
        server_card, server_layout = card("备份 Servers")
        server_toolbar = QHBoxLayout()
        add_server = QPushButton("配对并添加 Server")
        add_server.setObjectName("primary")
        remove_server = QPushButton("移除选中配置")
        server_toolbar.addWidget(add_server)
        server_toolbar.addWidget(remove_server)
        server_toolbar.addStretch()
        server_layout.addLayout(server_toolbar)
        self.settings_server_table = self._table(["名称", "Server ID", "URL", "状态"])
        server_layout.addWidget(self.settings_server_table)
        layout.addWidget(server_card)
        add_server.clicked.connect(self.add_server_pairing)
        remove_server.clicked.connect(self.remove_selected_server)
        return page

    def _table(self, headers: list[str]) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        table.verticalHeader().setVisible(False)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        return table

    def _load_config(self) -> None:
        if not self.config_path.exists():
            self._first_run_setup()
            if not self.config_path.exists():
                self.global_status.setText("● 尚未完成配置")
                return
        try:
            self.config = load_config(self.config_path)
            self.global_status.setText(f"● {self.config.client.display_name}\n配置已加载")
            self.inbox_edit.setText(str(self.config.transfer.inbox_dir))
            self.restore_server_combo.clear()
            self.restore_server_combo.addItem("自动选择", "auto")
            for server in self.config.enabled_servers():
                self.restore_server_combo.addItem(server.name, server.id)
            self._fill_settings_servers()
            self._fill_tasks()
            self.refresh_dashboard()
        except Exception as exc:
            self.global_status.setText("● 尚未完成配置")
            QMessageBox.warning(
                self,
                "需要配置",
                f"无法加载配置：\n{self.config_path}\n\n{exc}\n\n可以先使用项目中的示例配置。",
            )

    def _first_run_setup(self) -> None:
        dialog = FirstRunDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            values = dialog.values()
            temporary_server = ServerSection(
                base_url=values["server_url"],
                token="PAIRING_PENDING",
                timeout_seconds=15,
                verify_tls=values["server_url"].lower().startswith("https://"),
                id=values["server_id"],
                name="Server A",
            )
            response = BackupServerClient(temporary_server).pair_device(
                values["pairing_code"],
                values["device_id"],
                values["display_name"],
            )
            data_dir = Path(values["data_dir"]).expanduser()
            config = {
                "client": {
                    "machine_id": values["device_id"],
                    "display_name": values["display_name"],
                    "timezone": "Asia/Shanghai",
                    "data_dir": str(data_dir),
                    "temp_dir": str(data_dir / "tmp"),
                    "outbox_dir": str(data_dir / "outbox"),
                },
                "servers": [
                    {
                        "id": values["server_id"],
                        "name": "Server A",
                        "base_url": values["server_url"],
                        "token": response["token"],
                        "timeout_seconds": 60,
                        "verify_tls": values["server_url"].lower().startswith("https://"),
                        "enabled": True,
                    }
                ],
                "backup": {
                    "required_copies": 1,
                    "retry_count": 3,
                    "retry_interval_seconds": 5,
                    "copy_stability_check": True,
                    "keep_local_until_all_uploaded": True,
                },
                "restore": {
                    "allowed_roots": [],
                    "rollback_dir": str(data_dir / "rollback"),
                    "require_same_machine_id": True,
                },
                "transfer": {
                    "inbox_dir": values["inbox_dir"],
                    "temp_dir": str(data_dir / "transfer-tmp"),
                    "allowed_send_roots": [],
                    "require_confirmation": True,
                    "overwrite_existing": False,
                },
                "tasks": [],
            }
            self._write_raw_config(config)
        except Exception as exc:
            QMessageBox.critical(self, "首次设置失败", str(exc))

    def _fill_tasks(self) -> None:
        self.task_list.clear()
        if not self.config:
            return
        for task in self.config.tasks:
            mode = f"每天 {task.schedule_time}" if task.schedule_enabled else "手动"
            self.task_list.addItem(f"{task.name}    ·    {mode}    ·    {len(task.roots)} 个来源")

    def _fill_settings_servers(self) -> None:
        self.settings_server_table.setRowCount(0)
        if not self.config:
            return
        for server in self.config.servers:
            row = self.settings_server_table.rowCount()
            self.settings_server_table.insertRow(row)
            values = [server.name, server.id, server.base_url, "启用" if server.enabled else "停用"]
            for column, value in enumerate(values):
                cell = QTableWidgetItem(str(value))
                cell.setData(Qt.ItemDataRole.UserRole, server.id)
                self.settings_server_table.setItem(row, column, cell)

    def run_worker(
        self,
        function: Callable[[], Any],
        on_result: Callable[[Any], None] | None = None,
        busy_text: str = "正在处理…",
    ) -> None:
        self.global_status.setText(f"● {busy_text}")
        worker = Worker(function)
        if on_result:
            worker.signals.result.connect(on_result)
        worker.signals.error.connect(lambda message: QMessageBox.critical(self, "操作失败", message))
        worker.signals.finished.connect(
            lambda: self.global_status.setText(
                f"● {self.config.client.display_name if self.config else 'File Backup'}\n已就绪"
            )
        )
        self.thread_pool.start(worker)

    def refresh_dashboard(self) -> None:
        if not self.config:
            return

        def load() -> dict[str, Any]:
            health: list[dict[str, Any]] = []
            devices: list[dict[str, Any]] = []
            inbox: list[dict[str, Any]] = []
            for server in self.config.enabled_servers():
                client = BackupServerClient(server)
                try:
                    item = client.health()
                    item["configured_id"] = server.id
                    item["name"] = server.name
                    health.append(item)
                    if not devices:
                        devices = client.list_devices().get("items", [])
                    inbox.extend(
                        {**transfer, "server_id": server.id}
                        for transfer in client.list_transfer_inbox().get("items", [])
                    )
                except Exception as exc:
                    health.append(
                        {
                            "configured_id": server.id,
                            "name": server.name,
                            "status": "offline",
                            "error": str(exc),
                        }
                    )
            return {"health": health, "devices": devices, "inbox": inbox}

        self.run_worker(load, self._apply_dashboard, "正在检查设备和 Server…")

    def _apply_dashboard(self, data: dict[str, Any]) -> None:
        while self.server_cards_row.count():
            item = self.server_cards_row.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for server in data.get("health", []):
            frame, layout = card(server.get("name") or server.get("configured_id"))
            online = server.get("status") == "ok"
            status = QLabel("● 正常" if online else "● 无法连接")
            status.setStyleSheet(f"color: {GREEN if online else AMBER}; font-weight: 700;")
            layout.addWidget(status)
            detail = QLabel(
                f"Server ID：{server.get('server_id') or server.get('configured_id')}"
            )
            detail.setObjectName("muted")
            layout.addWidget(detail)
            self.server_cards_row.addWidget(frame)
        self.devices = data.get("devices", [])
        self.quick_devices.clear()
        self.devices_table.setRowCount(0)
        for device in self.devices:
            self.quick_devices.addItem(
                f"●  {device.get('display_name')}\n    {device.get('device_id')}"
            )
            row = self.devices_table.rowCount()
            self.devices_table.insertRow(row)
            values = [
                device.get("display_name"),
                device.get("device_id"),
                "已配对" if device.get("paired") else "静态授权",
                device.get("last_seen_at") or "—",
            ]
            for column, value in enumerate(values):
                self.devices_table.setItem(row, column, QTableWidgetItem(str(value)))
        self._fill_inbox(data.get("inbox", []))

    def _fill_inbox(self, items: list[dict[str, Any]]) -> None:
        unique: dict[str, dict[str, Any]] = {}
        for item in items:
            unique.setdefault(str(item["transfer_id"]), item)
        transfers = list(unique.values())
        self.inbox_table.setRowCount(0)
        self.overview_transfer_table.setRowCount(0)
        for transfer in transfers:
            row = self.inbox_table.rowCount()
            self.inbox_table.insertRow(row)
            values = [
                transfer.get("transfer_id"),
                transfer.get("sender_device_id"),
                transfer.get("status"),
                transfer.get("file_count"),
                self._format_size(int(transfer.get("total_size") or 0)),
                transfer.get("server_id"),
            ]
            for column, value in enumerate(values):
                cell = QTableWidgetItem(str(value))
                cell.setData(Qt.ItemDataRole.UserRole, transfer)
                self.inbox_table.setItem(row, column, cell)
            overview_row = self.overview_transfer_table.rowCount()
            self.overview_transfer_table.insertRow(overview_row)
            for column, value in enumerate(
                ["接收", transfer.get("sender_device_id"), transfer.get("status"), values[4]]
            ):
                self.overview_transfer_table.setItem(overview_row, column, QTableWidgetItem(str(value)))

    def refresh_backups(self) -> None:
        if not self.config:
            return
        self.run_worker(
            lambda: list_backups_across_servers(
                self.config,
                server_id="all",
                machine_id=self.config.client.machine_id,
                limit=200,
            ),
            self._apply_backups,
            "正在查询备份…",
        )

    def _apply_backups(self, response: dict[str, Any]) -> None:
        self.backups = response.get("items", [])
        self.backup_table.setRowCount(0)
        for item in self.backups:
            row = self.backup_table.rowCount()
            self.backup_table.insertRow(row)
            values = [
                item.get("backup_id"),
                item.get("task_name"),
                item.get("created_at"),
                item.get("copy_status"),
                item.get("file_count"),
            ]
            for column, value in enumerate(values):
                cell = QTableWidgetItem(str(value))
                cell.setData(Qt.ItemDataRole.UserRole, item)
                self.backup_table.setItem(row, column, cell)

    def open_send_dialog(self, paths: list[Path]) -> None:
        if not self.config:
            return
        available = [
            device
            for device in self.devices
            if device.get("device_id") != self.config.client.machine_id and device.get("enabled", True)
        ]
        if not available:
            QMessageBox.information(self, "没有接收设备", "请先配对另一台设备。")
            return
        dialog = SendDialog(paths, available, self.config.enabled_servers(), self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        receiver = dialog.receiver_device_id
        server_id = dialog.server_id
        self.run_worker(
            lambda: send_transfer(
                self.config,
                paths,
                receiver,
                server_id=server_id,
            ),
            lambda result: self._show_result("发送结果", result.__dict__),
            "正在发送文件…",
        )

    def choose_send_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "选择要发送的文件")
        if paths:
            self.open_send_dialog([Path(path) for path in paths])

    def choose_send_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择要发送的文件夹")
        if path:
            self.open_send_dialog([Path(path)])

    def receive_selected_transfer(self) -> None:
        if not self.config:
            return
        row = self.inbox_table.currentRow()
        if row < 0:
            QMessageBox.information(self, "请选择传输", "请先选择接收箱中的一条记录。")
            return
        transfer = self.inbox_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        destination = QFileDialog.getExistingDirectory(
            self,
            "选择接收目录",
            str(self.config.transfer.inbox_dir),
        )
        if not destination:
            return
        self.run_worker(
            lambda: receive_transfer(
                self.config,
                str(transfer["transfer_id"]),
                server_id=str(transfer["server_id"]),
                destination=Path(destination),
            ),
            lambda result: self._show_result("接收结果", result.__dict__),
            "正在接收并校验文件…",
        )

    def new_backup_task(self) -> None:
        if not self.config:
            return
        dialog = BackupTaskDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            task = dialog.task_data()
            raw = self._read_raw_config()
            raw.setdefault("tasks", []).append(task)
            self._write_raw_config(raw)
            self._load_config()
        except Exception as exc:
            QMessageBox.critical(self, "保存失败", str(exc))

    def run_selected_backup(self) -> None:
        if not self.config:
            return
        row = self.task_list.currentRow()
        if row < 0:
            QMessageBox.information(self, "请选择任务", "请先选择一个备份任务。")
            return
        task = self.config.tasks[row]
        self.run_worker(
            lambda: run_backup_for_task(self.config, task),
            lambda result: self._backup_finished(result),
            "正在生成双 Server 备份…",
        )

    def _backup_finished(self, result: Any) -> None:
        self.backup_result.setText(
            f"{result.task_name}：{result.status} · {result.file_count} 个文件 · {self._format_size(result.total_size)}"
        )
        self._show_result("备份结果", result.__dict__)

    def selected_backup(self) -> dict[str, Any] | None:
        row = self.backup_table.currentRow()
        if row < 0:
            QMessageBox.information(self, "请选择备份", "请先选择一个备份版本。")
            return None
        return self.backup_table.item(row, 0).data(Qt.ItemDataRole.UserRole)

    def verify_selected_backup(self) -> None:
        backup = self.selected_backup()
        if not backup or not self.config:
            return
        server_id = str(self.restore_server_combo.currentData() or "auto")
        self.run_worker(
            lambda: run_verify(self.config, str(backup["backup_id"]), server_id=server_id),
            lambda result: self._show_result("校验成功", result.__dict__),
            "正在校验备份…",
        )

    def restore_selected_backup(self) -> None:
        backup = self.selected_backup()
        if not backup or not self.config:
            return
        destination = QFileDialog.getExistingDirectory(self, "选择恢复到的新目录")
        if not destination:
            return
        server_id = str(self.restore_server_combo.currentData() or "auto")

        def restore() -> Any:
            copies = backup.get("copies") or []
            selected_copy = next(
                (copy for copy in copies if copy.get("server_id") == server_id),
                copies[0] if copies else None,
            )
            if selected_copy is None:
                raise ValueError("没有可用的备份副本")
            source_server = self.config.get_server(str(selected_copy["server_id"]))
            manifest = BackupServerClient(source_server).download_manifest(str(backup["backup_id"]))
            path_maps = []
            for root in manifest.get("roots", []):
                path_maps.append(f"{root}={Path(destination) / Path(root).name}")
            return run_restore(
                self.config,
                str(backup["backup_id"]),
                server_id=server_id,
                path_maps=path_maps,
            )

        self.run_worker(
            restore,
            lambda result: self._show_result("恢复结果", result.__dict__),
            "正在恢复到新目录…",
        )

    def pair_device_dialog(self) -> None:
        if not self.config:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("输入配对码")
        form = QFormLayout(dialog)
        server_combo = QComboBox()
        for server in self.config.enabled_servers():
            server_combo.addItem(server.name, server.id)
        code = QLineEdit()
        code.setPlaceholderText("六位数字")
        name = QLineEdit(self.config.client.display_name)
        form.addRow("Server", server_combo)
        form.addRow("配对码", code)
        form.addRow("设备名称", name)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok
        )
        form.addRow(buttons)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        server_id = str(server_combo.currentData())

        def pair() -> dict[str, Any]:
            server = self.config.get_server(server_id)
            return BackupServerClient(server).pair_device(
                code.text().strip(),
                self.config.client.machine_id,
                name.text().strip(),
            )

        def paired(response: dict[str, Any]) -> None:
            raw = self._read_raw_config()
            raw.setdefault("client", {})["display_name"] = name.text().strip()
            for server in raw.get("servers", []):
                if server.get("id") == server_id:
                    server["token"] = response["token"]
            self._write_raw_config(raw)
            self._load_config()
            QMessageBox.information(self, "配对成功", "设备已加入，Token 已保存到本机配置。")

        self.run_worker(pair, paired, "正在配对设备…")

    def rename_this_device(self) -> None:
        if not self.config:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("修改设备名称")
        form = QFormLayout(dialog)
        name = QLineEdit(self.config.client.display_name)
        server_combo = QComboBox()
        for server in self.config.enabled_servers():
            server_combo.addItem(server.name, server.id)
        form.addRow("新名称", name)
        form.addRow("同步到", server_combo)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Save
        )
        form.addRow(buttons)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        server_id = str(server_combo.currentData())

        def rename() -> dict[str, Any]:
            return BackupServerClient(self.config.get_server(server_id)).rename_device(
                self.config.client.machine_id,
                name.text().strip(),
            )

        def renamed(response: dict[str, Any]) -> None:
            raw = self._read_raw_config()
            raw.setdefault("client", {})["display_name"] = response["display_name"]
            self._write_raw_config(raw)
            self._load_config()

        self.run_worker(rename, renamed, "正在修改设备名称…")

    def choose_inbox(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择默认接收箱", self.inbox_edit.text())
        if path:
            self.inbox_edit.setText(path)

    def save_settings(self) -> None:
        try:
            raw = self._read_raw_config()
            raw.setdefault("transfer", {})["inbox_dir"] = self.inbox_edit.text().strip()
            self._write_raw_config(raw)
            self._load_config()
        except Exception as exc:
            QMessageBox.critical(self, "保存失败", str(exc))

    def add_server_pairing(self) -> None:
        if not self.config:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("配对并添加 Server")
        form = QFormLayout(dialog)
        server_id = QLineEdit(f"server-{len(self.config.servers) + 1}")
        server_name = QLineEdit(f"Server {len(self.config.servers) + 1}")
        server_url = QLineEdit("http://")
        code = QLineEdit()
        code.setPlaceholderText("六位配对码")
        form.addRow("Server ID", server_id)
        form.addRow("名称", server_name)
        form.addRow("URL", server_url)
        form.addRow("配对码", code)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("配对并添加")
        form.addRow(buttons)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        def pair() -> dict[str, Any]:
            temporary = ServerSection(
                base_url=server_url.text().strip().rstrip("/"),
                token="PAIRING_PENDING",
                timeout_seconds=15,
                verify_tls=server_url.text().strip().lower().startswith("https://"),
                id=server_id.text().strip(),
                name=server_name.text().strip(),
            )
            return BackupServerClient(temporary).pair_device(
                code.text().strip(),
                self.config.client.machine_id,
                self.config.client.display_name,
            )

        def paired(response: dict[str, Any]) -> None:
            raw = self._read_raw_config()
            servers = raw.setdefault("servers", [])
            if any(item.get("id") == server_id.text().strip() for item in servers):
                QMessageBox.critical(self, "添加失败", "Server ID 已存在")
                return
            servers.append(
                {
                    "id": server_id.text().strip(),
                    "name": server_name.text().strip() or server_id.text().strip(),
                    "base_url": server_url.text().strip().rstrip("/"),
                    "token": response["token"],
                    "timeout_seconds": 60,
                    "verify_tls": server_url.text().strip().lower().startswith("https://"),
                    "enabled": True,
                }
            )
            raw.setdefault("backup", {})["required_copies"] = len(
                [item for item in servers if item.get("enabled", True)]
            )
            self._write_raw_config(raw)
            self._load_config()

        self.run_worker(pair, paired, "正在配对第二台 Server…")

    def remove_selected_server(self) -> None:
        if not self.config:
            return
        row = self.settings_server_table.currentRow()
        if row < 0:
            QMessageBox.information(self, "请选择 Server", "请先选择一个 Server 配置。")
            return
        if len(self.config.servers) <= 1:
            QMessageBox.warning(self, "不能移除", "至少保留一台 Server。")
            return
        server_id = str(
            self.settings_server_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        )
        if QMessageBox.question(
            self,
            "移除配置",
            f"只从本机移除 {server_id} 的连接配置，不会删除 Server 上的任何备份。继续吗？",
        ) != QMessageBox.StandardButton.Yes:
            return
        raw = self._read_raw_config()
        raw["servers"] = [item for item in raw.get("servers", []) if item.get("id") != server_id]
        raw.setdefault("backup", {})["required_copies"] = len(
            [item for item in raw["servers"] if item.get("enabled", True)]
        )
        self._write_raw_config(raw)
        self._load_config()

    def _read_raw_config(self) -> dict[str, Any]:
        return yaml.safe_load(self.config_path.read_text(encoding="utf-8")) or {}

    def _write_raw_config(self, data: dict[str, Any]) -> None:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(
            yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )

    def _show_result(self, title: str, value: Any) -> None:
        QMessageBox.information(
            self,
            title,
            json.dumps(value, ensure_ascii=False, indent=2, default=str),
        )
        self.refresh_dashboard()

    @staticmethod
    def _format_size(value: int) -> str:
        size = float(value)
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if size < 1024 or unit == "TB":
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{value} B"


def main() -> None:
    application = QApplication(sys.argv)
    application.setApplicationName("File Backup")
    application.setStyle("Fusion")
    application.setFont(QFont("Arial", 11))
    application.setStyleSheet(APP_STYLE)
    window = MainWindow()
    window.show()
    raise SystemExit(application.exec())


if __name__ == "__main__":
    main()
