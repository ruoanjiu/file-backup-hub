# Windows x64 编译说明

## 1. 系统要求

- Windows 10或Windows 11 x64；
- Python 3.11 x64；建议安装 Python Launcher `py.exe`；
- Node.js x64 的当前 LTS版本及 npm；
- Microsoft Edge WebView2 Fixed Version x64运行时：Client和Server EXE都会内置；
- 可访问 PyPI和npm registry的网络。

安装 Python 时建议勾选：

```text
Install launcher for all users
Add python.exe to PATH
```

如果本机已有 Python 3.11 x64、但没有 `py.exe`，可以在构建前指定：

```powershell
$env:FILEBACKUP_PYTHON311 = "D:\path\to\python.exe"
```

Client和Server构建都需要微软WebView2 Fixed Version x64运行时。默认从
`build-tools\webview2-fixed-x64` 自动查找，也可以指定：

```powershell
$env:FILEBACKUP_WEBVIEW2_RUNTIME_DIR = "D:\path\to\Microsoft.WebView2.FixedVersionRuntime.x64"
```

## 2. 解压位置

建议解压到短路径：

```text
C:\FileBackupBuild
```

不要直接在压缩包内部运行，也尽量不要放在OneDrive同步目录、包含超长路径的目录或没有写权限的目录。

## 3. 一键编译

打开 PowerShell：

```powershell
cd C:\FileBackupBuild
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\build-windows-all.ps1
```

脚本会自动完成：

1. 检查 Python 3.11 x64、Node和npm；
2. 执行 `npm ci` 和 Vue生产构建；
3. 创建隔离的Client和Server Python构建环境；
4. 安装依赖与PyInstaller；
5. 构建内置后台Agent模式的Client和Server；
6. 生成两个EXE的SHA256。

## 4. 输出文件

成功后位于：

```text
dist\FileBackupClient.exe
dist\FileBackupServer.exe
dist\SHA256SUMS.txt
```

用途：

- `FileBackupClient.exe`：Vue桌面管理界面，并通过同一EXE的`--agent`模式执行定时备份与outbox补传；
- `FileBackupServer.exe`：Server Manager和后台Server入口。

`FileBackupClient.exe` 和 `FileBackupServer.exe` 都内置Python、各自依赖、Vue前端以及WebView2 Fixed Runtime，
目标电脑不需要安装Python、Node或WebView2。两个单文件体积都会明显增大，首次启动需要先解压运行时。
Client 不再需要单独的 `FileBackupClientAgent.exe`。界面启动时自动启动内置Agent子进程，设置页可查看、启动、停止或重启；关闭窗口到托盘后Agent继续运行。

## 5. 首次测试

先启动：

```text
dist\FileBackupServer.exe
```

设置Server ID、监听地址、端口和数据目录后启动Server。默认数据建议使用：

```text
C:\ProgramData\FileBackupServer
```

然后启动：

```text
dist\FileBackupClient.exe
```

使用Server Manager生成的六位配对码完成首次设置。

新建 Server 默认不创建静态 `client-1` 兼容 Token，设备统一通过一次性配对码授权。旧配置中显式保存的兼容 Token 会保留，避免升级时意外断开现有客户端。

设备撤销采用软停用并保留全部历史数据；同一 Device ID 可用新配对码重新启用。Client 的“退出此 Server”只影响选中的 Server 连接，不会删除任务、outbox 或本地备份。退出后可以删除本地 Server 配置；删除最后一条配置时 Client 仍保留任务和本地数据，并提供“添加 Server”入口。

若旧 Token 已失效而自撤销返回 401/403，Client 会提供二次确认的“仅在本机退出”兜底。该兜底不会用于网络超时或 Server 5xx。

Server ID 和数据目录可在 Server Manager 的“设置”页修改。保存时会自动重启正在运行的 Server。切换数据目录不会搬迁旧数据；如需保留旧备份历史，应先自行迁移并核对数据后再切换路径。

## 6. 当前边界

- 这些EXE尚未进行商业代码签名，Windows SmartScreen或杀毒软件可能提示未知发布者；
- 不要关闭或绕过杀毒软件，先核对 `SHA256SUMS.txt` 并保留源码包；
- Client 与 Server Manager 均提供 Windows 托盘图标；点击窗口关闭按钮只会隐藏到托盘，必须通过托盘菜单“退出程序”才会真正退出；
- Client托盘“退出程序”会同时停止内置Agent；仅关闭或隐藏窗口不会停止自动备份；
- Windows Service 和 Task Scheduler 自动注册仍属于下一阶段；
- 当前文件传送通过Server中转，尚未加入局域网直传和分块断点续传；
- 文件可发送给其他Client或直接发送到Server收件箱；Client和Server都可明确拒绝待接收传送，拒绝不会删除发送方原文件；
- 编译完成后必须在测试目录执行备份、校验、接收、恢复到新目录和重启持久性检查，再接入真实数据。

## 7. 单独重新编译

只编译带内置Agent的Client：

```powershell
.\scripts\build-client-exe-min.ps1
```

只编译Server：

```powershell
.\scripts\build-server-windows.ps1
```
