# Client 使用说明

## 新建备份任务

1. 启动 `FileBackupClient.exe`
2. 填写 Server URL、Token、主机 ID
3. 点击“保存配置”
4. 点击“新建任务”
5. 填写任务名
6. 选择“单次任务”或“定时任务”
7. 添加文件夹或文件
8. 点击“保存任务”

任务名只能包含：

```text
A-Z a-z 0-9 _ . -
```

例如：

```text
pc1-backup-A
daily_logs
prod_logs
```

## 手动执行备份

选择任务后点击：

```text
执行备份
```

## 自动备份 Agent

- 自动备份Agent已经内置在 `FileBackupClient.exe` 中，不需要单独复制或启动其他程序。
- 启动Client界面后会自动启动Agent；设置页可以查看状态并执行启动、停止或重启。
- 新建或修改定时任务后，Agent会自动重载配置。
- 关闭窗口只会隐藏到托盘，定时备份继续运行。
- 从托盘选择“退出程序”才会同时停止Client和Agent。
- 没有启用的Server时，Agent保持停止并等待重新配对或添加Server。

## 查询远端备份

点击：

```text
查询远端备份
```

如果出现 `403 Forbidden`，通常是 `machine_id` 和 token 不匹配。

## 恢复

1. 查询远端备份
2. 选择一个 Backup ID
3. 点击“校验备份”
4. 点击“恢复备份”

恢复默认按照 manifest 中的原始路径恢复。若目标文件已经存在，Client 会先创建 rollback 快照，再覆盖目标文件。

## 回滚恢复

恢复完成后会生成 Restore ID。输入 Restore ID 后点击：

```text
回滚恢复
```

回滚会尽量恢复到执行 restore 之前的状态。

## 文件传送

1. 在“传送”页选择“发送文件”或“发送文件夹”。
2. “接收目标”可选择另一台 Client，或选择“当前 Server（保存到 Server 收件箱）”。
3. 接收方可点击“接收”，也可点击“拒绝”；拒绝后该项目不再占用待接收列表，但发送方原文件不会被删除、移动或修改。
4. 发给 Server 的传送由 Server Manager“接收”页处理，接收后保存到 Server 数据目录下的 `transfers/server-inbox/<transfer_id>/`。
