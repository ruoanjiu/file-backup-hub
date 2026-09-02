from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from server.app.runtime import (
    ServerRuntimeConfig,
    health_url,
    load_runtime_config,
    new_default_config,
    read_health,
    save_runtime_config,
    stop_server_process,
)


NAVY = "#0d2038"
BLUE = "#146ef5"
GREEN = "#18a66a"
AMBER = "#d99016"
BG = "#f4f6f9"
BORDER = "#dfe4eb"
TEXT = "#18212f"
MUTED = "#6b7789"


STYLE = f"""
QMainWindow, QWidget#root {{ background: {BG}; color: {TEXT}; }}
QFrame#sidebar {{ background: {NAVY}; border: none; }}
QLabel#brand {{ color: white; font-size: 20px; font-weight: 700; }}
QPushButton#nav {{ background: transparent; color: #d9e4f3; text-align: left; padding: 12px 18px; border: none; border-radius: 8px; font-size: 14px; }}
QPushButton#nav:hover {{ background: #173455; color: white; }}
QPushButton#nav:checked {{ background: {BLUE}; color: white; font-weight: 600; }}
QFrame#card {{ background: white; border: 1px solid {BORDER}; border-radius: 12px; }}
QLabel#pageTitle {{ font-size: 26px; font-weight: 750; }}
QLabel#sectionTitle {{ font-size: 16px; font-weight: 700; }}
QLabel#muted {{ color: {MUTED}; }}
QPushButton {{ background: white; border: 1px solid {BORDER}; border-radius: 8px; padding: 9px 15px; font-weight: 600; }}
QPushButton#primary {{ background: {BLUE}; color: white; border-color: {BLUE}; }}
QPushButton#success {{ background: {GREEN}; color: white; border-color: {GREEN}; }}
QLineEdit {{ background: white; border: 1px solid {BORDER}; border-radius: 7px; padding: 8px; min-height: 22px; }}
QTableWidget {{ background: white; border: 1px solid {BORDER}; border-radius: 8px; gridline-color: #edf0f4; }}
QHeaderView::section {{ background: #f8fafc; color: {MUTED}; border: none; border-bottom: 1px solid {BORDER}; padding: 9px; font-weight: 600; }}
"""


def api_request(
    config: ServerRuntimeConfig,
    path: str,
    *,
    method: str = "GET",
    token: str | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    url = health_url(config).removesuffix("/health") + path
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def card(title: str) -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName("card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(18, 16, 18, 16)
    title_label = QLabel(title)
    title_label.setObjectName("sectionTitle")
    layout.addWidget(title_label)
    return frame, layout


class ServerManagerWindow(QMainWindow):
    def __init__(self, config_path: Path) -> None:
        super().__init__()
        self.config_path = config_path
        self.config = load_runtime_config(config_path) if config_path.exists() else new_default_config()
        self.setWindowTitle("File Backup Server")
        self.resize(1240, 780)
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._sidebar())
        self.pages = QStackedWidget()
        outer.addWidget(self.pages, 1)
        self.pages.addWidget(self._overview_page())
        self.pages.addWidget(self._devices_page())
        self.pages.addWidget(self._storage_page())
        self.pages.addWidget(self._settings_page())
        self._load_fields()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(3000)
        self.refresh()

    def _sidebar(self) -> QWidget:
        frame = QFrame()
        frame.setObjectName("sidebar")
        frame.setFixedWidth(236)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(18, 24, 18, 20)
        brand = QLabel("◈  Backup Server")
        brand.setObjectName("brand")
        layout.addWidget(brand)
        layout.addSpacing(24)
        self.nav: list[QPushButton] = []
        for index, text in enumerate(["总览", "设备与配对", "存储", "设置"]):
            button = QPushButton(text)
            button.setObjectName("nav")
            button.setCheckable(True)
            button.clicked.connect(lambda checked=False, page=index: self.switch(page))
            layout.addWidget(button)
            self.nav.append(button)
        self.nav[0].setChecked(True)
        layout.addStretch()
        self.sidebar_status = QLabel("● 检测中")
        self.sidebar_status.setStyleSheet("color: #d9e4f3; padding: 12px;")
        self.sidebar_status.setWordWrap(True)
        layout.addWidget(self.sidebar_status)
        return frame

    def switch(self, index: int) -> None:
        self.pages.setCurrentIndex(index)
        for button_index, button in enumerate(self.nav):
            button.setChecked(button_index == index)
        if index == 1:
            self.refresh_devices()

    def _page(self, title: str, subtitle: str) -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 24, 28, 28)
        layout.setSpacing(16)
        heading = QLabel(title)
        heading.setObjectName("pageTitle")
        layout.addWidget(heading)
        note = QLabel(subtitle)
        note.setObjectName("muted")
        layout.addWidget(note)
        return page, layout

    def _overview_page(self) -> QWidget:
        page, layout = self._page("总览", "Server 作为独立后台进程运行，关闭管理窗口不会停止服务。")
        status_card, status_layout = card("运行状态")
        self.big_status = QLabel("正在检测")
        self.big_status.setStyleSheet("font-size: 24px; font-weight: 750;")
        self.endpoint_label = QLabel()
        self.endpoint_label.setObjectName("muted")
        status_layout.addWidget(self.big_status)
        status_layout.addWidget(self.endpoint_label)
        actions = QHBoxLayout()
        start = QPushButton("启动 Server")
        start.setObjectName("success")
        stop = QPushButton("停止 Server")
        open_admin = QPushButton("打开管理页面")
        actions.addWidget(start)
        actions.addWidget(stop)
        actions.addWidget(open_admin)
        actions.addStretch()
        status_layout.addLayout(actions)
        layout.addWidget(status_card)
        safety_card, safety_layout = card("安全状态")
        self.safety_label = QLabel("备份删除默认关闭 · Client独立 Token · 中转文件单独存储")
        safety_layout.addWidget(self.safety_label)
        layout.addWidget(safety_card)
        layout.addStretch()
        start.clicked.connect(self.start_server)
        stop.clicked.connect(self.stop_server)
        open_admin.clicked.connect(self.open_admin)
        return page

    def _devices_page(self) -> QWidget:
        page, layout = self._page("设备与配对", "生成五分钟有效的一次性配对码，新设备加入后可修改显示名称。")
        toolbar = QHBoxLayout()
        create = QPushButton("生成配对码")
        create.setObjectName("primary")
        refresh = QPushButton("刷新设备")
        rename = QPushButton("修改选中名称")
        toolbar.addWidget(create)
        toolbar.addWidget(refresh)
        toolbar.addWidget(rename)
        toolbar.addStretch()
        layout.addLayout(toolbar)
        pair_card, pair_layout = card("当前配对码")
        self.pairing_code = QLabel("— — —  — — —")
        self.pairing_code.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.pairing_code.setStyleSheet(f"font-size: 36px; font-weight: 800; color: {BLUE}; padding: 22px;")
        self.pairing_expiry = QLabel("配对码只能使用一次")
        self.pairing_expiry.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.pairing_expiry.setObjectName("muted")
        pair_layout.addWidget(self.pairing_code)
        pair_layout.addWidget(self.pairing_expiry)
        layout.addWidget(pair_card)
        devices_card, devices_layout = card("已授权设备")
        self.devices_table = QTableWidget(0, 4)
        self.devices_table.setHorizontalHeaderLabels(["设备名称", "Device ID", "状态", "最后在线"])
        self.devices_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.devices_table.verticalHeader().setVisible(False)
        devices_layout.addWidget(self.devices_table)
        layout.addWidget(devices_card)
        create.clicked.connect(self.create_pairing_code)
        refresh.clicked.connect(self.refresh_devices)
        rename.clicked.connect(self.rename_selected_device)
        return page

    def _storage_page(self) -> QWidget:
        page, layout = self._page("存储", "备份、清单、传送中转和 trash 使用互相独立的目录。")
        self.storage_labels: dict[str, QLabel] = {}
        for key, title in [
            ("storage", "备份包"),
            ("manifests", "Manifest"),
            ("transfers", "文件中转"),
            ("trash", "Trash"),
        ]:
            frame, body = card(title)
            label = QLabel()
            label.setObjectName("muted")
            body.addWidget(label)
            self.storage_labels[key] = label
            layout.addWidget(frame)
        layout.addStretch()
        return page

    def _settings_page(self) -> QWidget:
        page, layout = self._page("设置", "修改配置后需要重启 Server 才会生效。")
        frame, body = card("Server 配置")
        form = QFormLayout()
        self.server_id = QLineEdit()
        self.host = QLineEdit()
        self.port = QLineEdit()
        self.data_dir = QLineEdit()
        self.admin_token = QLineEdit()
        self.admin_token.setEchoMode(QLineEdit.EchoMode.Password)
        self.client_tokens = QLineEdit()
        self.client_tokens.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Server ID", self.server_id)
        form.addRow("监听地址", self.host)
        form.addRow("端口", self.port)
        data_row = QHBoxLayout()
        data_row.addWidget(self.data_dir)
        choose = QPushButton("选择")
        data_row.addWidget(choose)
        form.addRow("数据目录", data_row)
        form.addRow("Admin Token", self.admin_token)
        form.addRow("兼容 Client Tokens", self.client_tokens)
        body.addLayout(form)
        save = QPushButton("保存配置")
        save.setObjectName("primary")
        body.addWidget(save, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(frame)
        layout.addStretch()
        choose.clicked.connect(self.choose_data_dir)
        save.clicked.connect(self.save)
        return page

    def _load_fields(self) -> None:
        self.server_id.setText(self.config.server_id)
        self.host.setText(self.config.host)
        self.port.setText(str(self.config.port))
        self.data_dir.setText(str(self.config.data_dir))
        self.admin_token.setText(self.config.admin_token)
        self.client_tokens.setText(self.config.client_tokens)
        for key, label in self.storage_labels.items():
            label.setText(str(self.config.data_dir / key))

    def current_config(self) -> ServerRuntimeConfig:
        return ServerRuntimeConfig(
            server_id=self.server_id.text().strip(),
            host=self.host.text().strip() or "0.0.0.0",
            port=int(self.port.text()),
            data_dir=Path(self.data_dir.text()).expanduser(),
            admin_token=self.admin_token.text().strip(),
            client_tokens=self.client_tokens.text().strip(),
            allow_backup_delete=False,
        )

    def save(self) -> None:
        try:
            config = self.current_config()
            if not config.server_id or not config.admin_token:
                raise ValueError("Server ID 和 Admin Token 不能为空")
            save_runtime_config(self.config_path, config)
            self.config = config
            self._load_fields()
            QMessageBox.information(self, "已保存", "配置已保存，重启 Server 后生效。")
        except Exception as exc:
            QMessageBox.critical(self, "保存失败", str(exc))

    def choose_data_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择 Server 数据目录", self.data_dir.text())
        if path:
            self.data_dir.setText(path)

    def start_server(self) -> None:
        try:
            config = self.current_config()
            save_runtime_config(self.config_path, config)
            self.config = config
            if read_health(self.config):
                return
            if getattr(sys, "frozen", False):
                command = [sys.executable, "--headless", "--config", str(self.config_path)]
            else:
                command = [
                    sys.executable,
                    str(Path(__file__).resolve().parents[2] / "run_server_app.py"),
                    "--headless",
                    "--config",
                    str(self.config_path),
                ]
            log_dir = self.config.data_dir / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            log_file = (log_dir / "server.log").open("a", encoding="utf-8")
            kwargs: dict[str, Any] = {
                "stdin": subprocess.DEVNULL,
                "stdout": log_file,
                "stderr": subprocess.STDOUT,
            }
            if os.name == "nt":
                kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
            else:
                kwargs["start_new_session"] = True
            try:
                subprocess.Popen(command, **kwargs)
            finally:
                log_file.close()
            QTimer.singleShot(1200, self.refresh)
        except Exception as exc:
            QMessageBox.critical(self, "启动失败", str(exc))

    def stop_server(self) -> None:
        try:
            if not stop_server_process(self.config):
                raise RuntimeError("没有找到与当前 Server ID匹配的运行实例")
            QTimer.singleShot(900, self.refresh)
        except Exception as exc:
            QMessageBox.critical(self, "停止失败", str(exc))

    def open_admin(self) -> None:
        import webbrowser

        webbrowser.open(health_url(self.config).removesuffix("/health") + "/admin")

    def refresh(self) -> None:
        health = read_health(self.config, timeout=0.5)
        if health and health.get("server_id") == self.config.server_id:
            self.big_status.setText("● Server 正常运行")
            self.big_status.setStyleSheet(f"font-size: 24px; font-weight: 750; color: {GREEN};")
            self.sidebar_status.setText(f"● {self.config.server_id}\n运行中")
        elif health:
            self.big_status.setText("● 端口被其他 Server 占用")
            self.big_status.setStyleSheet(f"font-size: 24px; font-weight: 750; color: {AMBER};")
            self.sidebar_status.setText("● 配置冲突")
        else:
            self.big_status.setText("● Server 未运行")
            self.big_status.setStyleSheet(f"font-size: 24px; font-weight: 750; color: {AMBER};")
            self.sidebar_status.setText(f"● {self.config.server_id}\n未运行")
        self.endpoint_label.setText(
            f"本机管理：http://127.0.0.1:{self.config.port}   ·   监听：{self.config.host}:{self.config.port}"
        )

    def create_pairing_code(self) -> None:
        try:
            response = api_request(
                self.config,
                "/api/v1/pairing/codes",
                method="POST",
                token=self.config.admin_token,
                payload={"lifetime_minutes": 5},
            )
            code = str(response["code"])
            self.pairing_code.setText(f"{code[:3]}  {code[3:]}")
            self.pairing_expiry.setText(f"有效期至 {response['expires_at']} · 只能使用一次")
        except Exception as exc:
            QMessageBox.critical(self, "生成失败", f"请先启动 Server。\n\n{exc}")

    def refresh_devices(self) -> None:
        try:
            response = api_request(
                self.config,
                "/api/v1/devices",
                token=self.config.admin_token,
            )
            self.devices_table.setRowCount(0)
            for device in response.get("items", []):
                row = self.devices_table.rowCount()
                self.devices_table.insertRow(row)
                values = [
                    device.get("display_name"),
                    device.get("device_id"),
                    "已配对" if device.get("paired") else "兼容授权",
                    device.get("last_seen_at") or "—",
                ]
                for column, value in enumerate(values):
                    cell = QTableWidgetItem(str(value))
                    cell.setData(Qt.ItemDataRole.UserRole, device)
                    self.devices_table.setItem(row, column, cell)
        except Exception:
            self.devices_table.setRowCount(0)

    def rename_selected_device(self) -> None:
        row = self.devices_table.currentRow()
        if row < 0:
            QMessageBox.information(self, "请选择设备", "请先选择一台已配对设备。")
            return
        device = self.devices_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        dialog = QDialog(self)
        dialog.setWindowTitle("修改设备名称")
        form = QFormLayout(dialog)
        name = QLineEdit(str(device["display_name"]))
        form.addRow("新名称", name)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Save
        )
        form.addRow(buttons)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            api_request(
                self.config,
                f"/api/v1/devices/{device['device_id']}",
                method="PATCH",
                token=self.config.admin_token,
                payload={"display_name": name.text().strip()},
            )
            self.refresh_devices()
        except Exception as exc:
            QMessageBox.critical(self, "修改失败", str(exc))


def run_server_manager(config_path: Path) -> None:
    application = QApplication.instance() or QApplication(sys.argv)
    application.setApplicationName("File Backup Server")
    application.setStyle("Fusion")
    application.setStyleSheet(STYLE)
    window = ServerManagerWindow(config_path)
    window.show()
    raise SystemExit(application.exec())
