# File Backup Hub

File Backup Hub 是一个面向 Windows 与 macOS 的多 Server 文件备份与恢复系统。Client 延续原项目的文件扫描、`manifest.json`、`bundle.tar.gz`、逐文件 SHA256、整包 SHA256、原路径恢复和 rollback 机制；同一个备份包可以复制到多台独立 Server。

桌面前端采用 Vue 3 + TypeScript + Vite，使用 pywebview 嵌入 Windows WebView2/macOS WKWebView。Node.js 仅用于构建前端，用户运行 App 时不需要安装 Node；备份、配对、文件传送和恢复仍由 Python 核心执行。

## 当前安全边界

- 日常备份 Agent 只读取和复制源文件，不删除、移动、重命名或修改源文件。
- Client 的可写目录（data、tmp、outbox、rollback）不能放在任何备份源目录中。
- Client 不提供删除远端备份的入口。
- Server 删除 API 默认关闭；启用后也只有管理员 Token 可以操作，并将 bundle 与 manifest 分别移入 trash。
- 恢复是用户手动操作，恢复前校验备份并创建 rollback 快照。
- 文件传送由发送者选择本机源文件、接收者选择本机保存目录；发送者不能指定对方绝对路径。
- 收到同名内容时自动生成新名称，不覆盖已有文件。
- 设备移除采用“解除配对/撤销授权”：只停用 Token 并保留设备历史，不删除 Client 原始文件或 Server 历史备份。

## 设备配对与文件传送

- Server Manager 可生成五分钟有效、只能使用一次的六位配对码和二维码。
- 配对后的设备获得独立动态 Token；显示名称可修改，内部 `device_id` 不变。
- Server 管理员可软撤销设备，旧 Token 立即失效；同一 `device_id` 可用新的六位码重新配对。
- 已停用的设备可进一步“移除记录”使卡片从列表消失；该操作只删除设备登记，不删除任何备份、Manifest、传输文件或 Client 原始文件。
- Client 可退出最后一台 Server；此时备份会明确失败而不会误报成功，用户可重新配对、删除本地 Server 配置或添加新 Server。
- Client 与 Server App 使用统一设备列表。
- 当前文件传送采用 Server 中转，可在局域网或 HTTPS/Tailscale 网络中使用。
- Client 接收目标可选其他设备或“当前 Server”；Server Manager 的接收页校验后保存到 `transfers/server-inbox/<transfer_id>/`。
- Client 和 Server 均可拒绝待接收内容；拒绝后不再显示于待处理列表，发送方原文件和 Server 中转包均保留。
- 发送和接收都校验整包 SHA256、manifest 与逐文件 SHA256。
- 中转内容存放在独立 `transfers/` 目录，不进入正式备份目录。

## 双 Server 工作方式

Client 只扫描和打包一次，然后把同一个 `backup_id`、manifest 和 bundle 上传到所有启用的 Server：

```text
Client
  ├── Server A: COMPLETED
  └── Server B: COMPLETED
       => SUCCESS

Client
  ├── Server A: COMPLETED
  └── Server B: FAILED
       => DEGRADED，bundle 保留在本地 outbox，可执行 retry
```

恢复时可以指定 `--server server-a` 或 `--server server-b`；使用 `--server auto` 时，Client 会在副本信息一致的前提下自动回退到可用副本。

## 配置

Windows 示例见 `client/examples/config.example.yaml`，macOS 示例见 `client/examples/config.macos.example.yaml`。旧版单个 `server:` 配置仍可读取；新配置使用：

```yaml
servers:
  - id: server-a
    name: Server A
    base_url: https://backup-a.example.com
    token: REPLACE_ME
    enabled: true
  - id: server-b
    name: Server B
    base_url: https://backup-b.example.com
    token: REPLACE_ME
    enabled: true

backup:
  required_copies: 2
  retry_count: 3
  retry_interval_seconds: 5
  keep_local_until_all_uploaded: true
```

## 常用命令

```bash
python -m client.app.cli backup --all --config /path/to/config.yaml
python -m client.app.cli retry --backup-id BACKUP_ID --config /path/to/config.yaml
python -m client.app.cli list --server all --config /path/to/config.yaml
python -m client.app.cli verify --backup-id BACKUP_ID --server auto --config /path/to/config.yaml
python -m client.app.cli restore --backup-id BACKUP_ID --server server-b --config /path/to/config.yaml
python -m client.app.cli rollback --restore-id RESTORE_ID --config /path/to/config.yaml
python -m client.app.cli pair --server server-a --code 683291 --name "办公室电脑" --config /path/to/config.yaml
python -m client.app.cli devices --server server-a --config /path/to/config.yaml
python -m client.app.cli send /path/to/file --to office-pc --server auto --config /path/to/config.yaml
python -m client.app.cli inbox --server server-a --config /path/to/config.yaml
python -m client.app.cli receive --transfer-id TRANSFER_ID --server server-a --config /path/to/config.yaml
```

## Server 运行方式

Docker：

```bash
cp .env.docker.example .env
docker compose up --build -d file-backup-server
```

桌面管理 App 源码运行：

```bash
python run_server_app.py
```

Server Manager 启动的是独立后台进程。macOS Server App 会在启动时自动启动 Server，并在系统菜单栏常驻软件图标；菜单中可查看运行状态、打开管理界面、启动或停止 Server。关闭管理窗口只会隐藏窗口，不会停止 Server 或退出菜单栏。默认数据目录为：

Server 界面的存储卡片可直接在 Finder/Windows 资源管理器中打开受管目录。顶部铃铛是应用内通知，用于显示待接收文件、Server 离线等需要处理的状态。

Server 设置页可修改 Server ID 和数据目录。保存前会校验 ID、绝对路径、根目录、文件路径和写入权限，并备份当前配置；Server 正在运行时会安全停止后重启，失败则自动恢复旧配置。切换数据目录不会自动迁移或删除旧数据。

- Windows：`C:\ProgramData\FileBackupServer`
- macOS：`/Users/Shared/FileBackupServer`

## App 构建

Windows：

```powershell
.\scripts\build-client-exe-min.ps1
.\scripts\build-server-windows.ps1
```

macOS：

```bash
./scripts/build-client-macos.sh
./scripts/build-server-macos.sh
```

Client 构建同时生成后台 Agent：Windows 为 `FileBackupClientAgent.exe`，macOS 为 `FileBackupClientAgent`。构建产物位于 `dist/`。当前 macOS 构建是本机 arm64、临时 ad-hoc 签名；正式分发前仍需 Developer ID 签名、公证和 DMG/PKG。Windows 正式分发前需要在 Windows x64 构建机验证并进行代码签名。

macOS Client 和 Server 均具有菜单栏常驻图标；关闭窗口只隐藏管理界面。Client 管理器和独立 Agent 是两个进程，退出管理器不会代替 Agent 启停管理。

构建脚本会先在 `frontend/` 执行 `npm ci && npm run build`，再把生成的静态资源与 Python/pywebview 一起打包。

## 测试

```bash
python -m pytest
```

测试覆盖单 Server 原有流程、双 Server 副本、失败补传、源文件保持不变、指定/自动回退恢复、设备配对与改名、Server 中转传送、接收路径防覆盖、SHA256 校验、跨机器鉴权，以及远端软删除后 bundle/manifest 完整进入 trash。
