# Client 使用说明

## 新建备份任务

1. 启动 `TradingBackupClient.exe`
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
