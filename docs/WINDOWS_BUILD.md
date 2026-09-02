# Windows x64 编译说明

## 1. 系统要求

- Windows 10或Windows 11 x64；
- Python 3.11 x64，并安装 Python Launcher `py.exe`；
- Node.js x64 的当前 LTS版本及 npm；
- Microsoft Edge WebView2 Runtime。Windows 10/11通常已经安装；
- 可访问 PyPI和npm registry的网络。

安装 Python 时建议勾选：

```text
Install launcher for all users
Add python.exe to PATH
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
5. 构建Client、后台Agent和Server；
6. 生成三个EXE的SHA256。

## 4. 输出文件

成功后位于：

```text
dist\FileBackupClient.exe
dist\FileBackupClientAgent.exe
dist\FileBackupServer.exe
dist\SHA256SUMS.txt
```

用途：

- `FileBackupClient.exe`：Vue桌面管理界面；
- `FileBackupClientAgent.exe`：后台定时备份与outbox补传；
- `FileBackupServer.exe`：Server Manager和后台Server入口。

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

## 6. 当前边界

- 这些EXE尚未进行商业代码签名，Windows SmartScreen或杀毒软件可能提示未知发布者；
- 不要关闭或绕过杀毒软件，先核对 `SHA256SUMS.txt` 并保留源码包；
- Windows Service、Task Scheduler自动注册和托盘图标仍属于下一阶段；
- 当前文件传送通过Server中转，尚未加入局域网直传和分块断点续传；
- 编译完成后必须在测试目录执行备份、校验、接收、恢复到新目录和重启持久性检查，再接入真实数据。

## 7. 单独重新编译

只编译Client和Agent：

```powershell
.\scripts\build-client-exe-min.ps1
```

只编译Server：

```powershell
.\scripts\build-server-windows.ps1
```
