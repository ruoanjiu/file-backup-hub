# 部署说明

## Server Docker 部署

1. 准备服务器。

```bash
sudo apt update
sudo apt install -y docker.io docker-compose-plugin
sudo systemctl enable --now docker
```

2. 上传项目到服务器，例如：

```text
/opt/trading-backup-hub
```

3. 创建 `.env`。

```bash
cp .env.docker.example .env
```

编辑 `.env`：

```env
SERVER_ADMIN_TOKEN=replace-with-a-long-random-admin-token
CLIENT_TOKENS=pc1:replace-with-pc1-token,pc2:replace-with-pc2-token,pc3:replace-with-pc3-token
```

4. 启动。

```bash
docker compose up --build -d trading-backup-server
docker compose ps
curl http://127.0.0.1:8000/health
```

## 公网部署建议

测试阶段可以临时开放：

```text
TCP 8000
来源：你的固定公网 IP/32
```

正式环境建议：

```text
Client -> HTTPS 443 -> Nginx/Caddy -> 127.0.0.1:8000 -> Server Container
```

并将 `docker-compose.yml` 的端口改为仅本机监听：

```yaml
ports:
  - "127.0.0.1:8000:8000"
```

## Client 分发

在 Windows 构建机运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\build-client-exe-min.ps1
```

将以下文件分发到客户端电脑：

```text
dist\TradingBackupClient.exe
```

每台电脑首次启动后配置：

- Server URL
- 该电脑的 `machine_id`
- 该电脑独立的 client token
- 备份任务和备份路径

## 定时备份

Client 的定时器只在 Client 程序运行时生效：

- 窗口最小化到托盘：继续运行
- 退出程序：不会运行
- 电脑关机或睡眠：不会运行

如需开机自动运行，可将 exe 快捷方式放入：

```text
shell:startup
```
