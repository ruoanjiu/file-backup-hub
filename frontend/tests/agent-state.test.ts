import assert from 'node:assert/strict'
import test from 'node:test'

import { agentStatusLabel, agentStatusTone } from '../src/agentState.ts'

test('embedded agent status distinguishes running, starting and stopped states', () => {
  assert.equal(agentStatusLabel({ status: 'RUNNING', running: true }), '自动备份运行中')
  assert.equal(agentStatusLabel({ status: 'STARTING', running: true }), '正在启动')
  assert.equal(agentStatusLabel({ status: 'STOPPED', running: false }), '已停止')
  assert.equal(agentStatusTone({ status: 'RUNNING', running: true }), 'success')
  assert.equal(agentStatusTone({ status: 'STOPPED', running: false }), 'warning')
})

test('agent status explains why automatic backup is unavailable', () => {
  assert.equal(agentStatusLabel({ status: 'NO_SERVER' }), '等待添加 Server')
  assert.equal(agentStatusLabel({ status: 'CONFIG_ERROR' }), '配置异常')
})
