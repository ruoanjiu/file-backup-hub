export type DeviceAuthorizationState = {
  paired?: boolean
  enabled?: boolean
  display_name?: string
  device_id?: string
}

export type ServerConnectionState = {
  enabled?: boolean
  name?: string
  id?: string
  status?: string
}

export function deviceAuthorizationLabel(device: DeviceAuthorizationState): string {
  if (!device.paired) return '兼容授权'
  return device.enabled === false ? '已停用' : '已配对'
}

export function serverConnectionLabel(server: ServerConnectionState): string {
  if (server.enabled === false || server.status === 'disabled') return '已退出'
  return server.status === 'ok' ? '正常' : '离线'
}

export function revokeDeviceConfirmation(device: DeviceAuthorizationState): string {
  const name = device.display_name || device.device_id || '该设备'
  return `确认撤销“${name}”吗？\n\n该设备的 Token 会立即失效，但不会删除设备记录、原始文件、历史备份、Manifest、Transfer 或 Trash 内容。停用后可用新的六位配对码重新启用同一 Device ID。`
}

export function leaveServerConfirmation(server: ServerConnectionState): string {
  const name = server.name || server.id || '此 Server'
  return `确认退出“${name}”吗？\n\nServer 将撤销这台设备的 Token，本机随后清空该 Server Token 并禁用此连接。其他 Server、备份任务、本地备份及历史数据不受影响。`
}

export function deleteServerConfirmation(server: ServerConnectionState): string {
  const name = server.name || server.id || '此 Server'
  return `确认从本机删除“${name}”吗？\n\n只会删除 Client 本地的这条已退出 Server 配置。Server 上的已停用设备记录、历史备份、Manifest、Transfer、Trash，以及本机任务和备份文件都不会删除。`
}

export function localOnlyExitConfirmation(server: ServerConnectionState): string {
  const name = server.name || server.id || '此 Server'
  return `“${name}”拒绝了当前 Bearer Token（Token 已失效或设备已被撤销）。\n\n是否仅在本机退出？\n\n确认后只会清空本机该 Server Token 并禁用连接，不会删除其他 Server、任务、本地备份或远端历史数据。随后可选择删除本地 Server 配置。`
}
