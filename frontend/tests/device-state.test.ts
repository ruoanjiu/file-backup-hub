import assert from 'node:assert/strict'
import test from 'node:test'

import {
  deviceAuthorizationLabel,
  deleteServerConfirmation,
  leaveServerConfirmation,
  localOnlyExitConfirmation,
  revokeDeviceConfirmation,
  serverConnectionLabel,
} from '../src/deviceState.ts'

test('device status distinguishes active, revoked and legacy authorization', () => {
  assert.equal(deviceAuthorizationLabel({ paired: true, enabled: true }), '已配对')
  assert.equal(deviceAuthorizationLabel({ paired: true, enabled: false }), '已停用')
  assert.equal(deviceAuthorizationLabel({ paired: false, enabled: true }), '兼容授权')
})

test('server status distinguishes disabled connection', () => {
  assert.equal(serverConnectionLabel({ enabled: false, status: 'disabled' }), '已退出')
  assert.equal(serverConnectionLabel({ enabled: true, status: 'ok' }), '正常')
  assert.equal(serverConnectionLabel({ enabled: true, status: 'offline' }), '离线')
})

test('confirmation copy explicitly preserves backup data and other servers', () => {
  const revoke = revokeDeviceConfirmation({ display_name: '办公室电脑' })
  const leave = leaveServerConfirmation({ name: 'Server A' })
  const remove = deleteServerConfirmation({ name: 'Server A' })
  const localOnly = localOnlyExitConfirmation({ name: 'Server A' })

  assert.match(revoke, /不会删除设备记录/)
  assert.match(revoke, /历史备份、Manifest、Transfer 或 Trash/)
  assert.match(leave, /其他 Server、备份任务、本地备份及历史数据不受影响/)
  assert.match(remove, /只会删除 Client 本地/)
  assert.match(remove, /已停用设备记录、历史备份、Manifest、Transfer、Trash/)
  assert.match(localOnly, /Bearer Token.*已失效或设备已被撤销/)
  assert.match(localOnly, /只会清空本机该 Server Token并禁用连接|只会清空本机该 Server Token 并禁用连接/)
})
