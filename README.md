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

## 设备配对与文件传送

- Server Manager 可生成五分钟有效、只能使用一次的六位配对码和二维码。
- 配对后的设备获得独立动态 Token；显示名称可修改，内部 `device_id` 不变。
- Client 与 Server App 使用统一设备列表。
- 当前文件传送采用 Server 中转，可在局域网或 HTTPS/Tailscale 网络中使用。
- 发送目标既可以是其他 Client，也可以是所选 Server；发给 Server 的内容由管理员在 Server Manager“接收”页接收并保存到 `transfers/server-inbox/<transfer_id>/`。
- Client 和 Server 都可以拒绝待接收传送；拒绝后项目会从待接收列表消失，但不会删除发送方原文件，也不会改动正式备份。
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

Server Manager 启动的是独立后台进程；点击关闭按钮会把管理窗口隐藏到 Windows 托盘，不会停止 Server。Server 和 Client 的托盘菜单都可以重新打开窗口或真正退出程序。Server“存储”页的四张目录卡片可以直接在资源管理器中打开对应目录，右上角铃铛用于显示服务离线、待接收传送和异常备份副本等状态提醒。默认数据目录为：

- Windows：`C:\ProgramData\FileBackupServer`
- macOS：`/Users/Shared/FileBackupServer`

Server Manager 的“设置”页可以修改 Server ID 和数据目录，并可使用原生目录选择器。保存时，正在运行的 Server 会自动停止并按新配置启动。修改数据目录只影响后续读写，不会自动迁移旧目录中的数据库、备份包或中转文件；修改 Server ID 后，已有 Client 可能需要更新配置或重新配对。

新建桌面 Server 默认不再生成 `client-1` 静态兼容授权。设备应使用 Server Manager 生成的六位一次性配对码加入；已有配置中手工设置的兼容 Token 仍可继续使用，升级 EXE 不会自动撤销。

Server 管理员可以在“设备与配对”页撤销已配对设备。撤销采用软停用：只把设备标记为 `enabled=false`，旧 Token 立即失效，但不会删除 Client 行、原始文件、历史备份、Manifest、Transfer 或 Trash。已停用设备可使用新的六位配对码重新配对同一个 Device ID，并获得新 Token。Client 也可在“设置”中退出指定 Server；Server 确认自撤销后，本机只清空该 Server Token 并禁用该连接，其他 Server、任务和本地备份不变。退出成功后可以进一步删除本机的 Server 配置；即使删除最后一条 Server，任务和本地备份仍会保留，并可通过“添加 Server”重新加入。

如果 Client 调用自撤销时收到 HTTP 401/403，说明 Bearer Token 已失效或设备已被 Server 撤销。此时界面会再次确认是否“仅在本机退出”；只有用户二次确认后才清空本地 Token并禁用连接。网络超时和 Server 5xx 不会触发该兜底。

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

Client 的定时备份 Agent 已作为 `FileBackupClient.exe --agent` 内置模式运行，不再生成或分发第二个 Agent 可执行文件。启动 Client 界面时会自动启动内置 Agent；关闭窗口到托盘不会停止自动备份，只有托盘“退出程序”才会同时停止 Agent。构建产物位于 `dist/`。当前 macOS 构建是本机 arm64、临时 ad-hoc 签名；正式分发前仍需 Developer ID 签名、公证和 DMG/PKG。Windows 正式分发前需要在 Windows x64 构建机验证并进行代码签名。

构建脚本会先在 `frontend/` 执行 `npm ci && npm run build`，再把生成的静态资源与 Python/pywebview 一起打包。
Windows `FileBackupClient.exe` 和 `FileBackupServer.exe` 都会内置Microsoft WebView2 Fixed Runtime x64，目标电脑无需另装Python、Node或WebView2；代价是两个图形界面单文件的体积和首次启动解压时间都会明显增加。

## 测试

```bash
python -m pytest
```

测试覆盖单 Server 原有流程、双 Server 副本、失败补传、源文件保持不变、指定/自动回退恢复、设备配对与改名、Server 中转传送、接收路径防覆盖、SHA256 校验、跨机器鉴权，以及远端软删除后 bundle/manifest 完整进入 trash。
