# File Backup Hub

File Backup Hub 是一个面向多台 Windows 客户端的文件备份与恢复系统。Server 提供 FastAPI API、Web 管理页面和 Docker 部署方式；Client 提供 Tkinter 桌面界面、托盘后台运行、单次备份、每日定时备份、恢复和回滚。

## 功能

- 多客户端按 `machine_id` 隔离备份数据
- 支持文件夹和单文件混合备份
- 支持单次备份和每日定时备份
- Server 端保存备份包、manifest 和 SQLite 元数据
- Web 管理页面可查询、下载和删除远端备份
- Client 端支持校验、恢复和回滚恢复
- Windows Client 可打包为单文件 `.exe`

## 架构

```text
Windows Client 1/2/3
  - 选择备份路径
  - 生成 manifest
  - 打包 tar.gz
  - 上传到 Server

FastAPI Server
  - Bearer Token 鉴权
  - SQLite 元数据
  - 文件存储
  - Web Admin 页面

Docker Volume
  - /data/file-backup/db
  - /data/file-backup/storage
  - /data/file-backup/manifests
```

## 快速启动 Server

复制并编辑环境变量：

```bash
cp .env.docker.example .env
```

`.env` 示例：

```env
SERVER_ADMIN_TOKEN=replace-with-a-long-random-admin-token
CLIENT_TOKENS=pc1:replace-with-pc1-token,pc2:replace-with-pc2-token,pc3:replace-with-pc3-token
```

`CLIENT_TOKENS` 使用 `machine_id:token`，多个客户端用逗号分隔：

```env
CLIENT_TOKENS=pc1:pc1-random-token,pc2:pc2-random-token,pc3:pc3-random-token
```

启动：

```bash
docker compose up --build -d file-backup-server
```

检查：

```bash
curl http://127.0.0.1:8000/health
```

Web Admin：

```text
http://127.0.0.1:8000/admin
```

使用 `.env` 中的 `SERVER_ADMIN_TOKEN` 登录查询备份。

## Client 配置

Windows 默认配置文件路径：

```text
C:\ProgramData\FileBackupClient\config.yaml
```

示例见：

```text
client/examples/config.example.yaml
```

每台电脑必须使用独立身份：

```yaml
client:
  machine_id: "pc1"
  timezone: "Asia/Shanghai"
  data_dir: "C:/ProgramData/FileBackupClient"
  temp_dir: "C:/ProgramData/FileBackupClient/tmp"

server:
  base_url: "https://backup.example.com"
  token: "replace-with-pc1-token"
  timeout_seconds: 60
  verify_tls: true
```

`machine_id` 必须和 Server 的 `CLIENT_TOKENS` 左侧一致。

## 运行 Client GUI

```bash
python -m client.app.cli gui
```

或：

```bash
python run_client_gui.py
```

## 打包 Windows Client

推荐使用最小化打包脚本：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\build-client-exe-min.ps1
```

输出：

```text
dist\FileBackupClient.exe
```

## 测试

```bash
pip install -r requirements.txt
pytest
```

## 生产部署建议

- 不要把 `8000` 端口直接暴露到公网
- 使用 Nginx/Caddy 反向代理到 HTTPS `443`
- Server `.env` 不要提交到 GitHub
- 每台 Client 使用不同 token
- Admin Token 使用强随机字符串
- 通过云厂商安全组限制访问来源 IP
- 定期备份 Docker volume 或启用云盘快照

更多部署和安全细节见：

- [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)
- [docs/SECURITY.md](docs/SECURITY.md)

## 许可证

本项目使用 [MIT License](LICENSE)。
