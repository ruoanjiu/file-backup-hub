import assert from 'node:assert/strict'
import test from 'node:test'

import {
  SERVER_RECEIVER_ID,
  receiverDisplayName,
  rejectTransferConfirmation,
} from '../src/transferState.ts'

test('Server is exposed as a distinct transfer receiver', () => {
  assert.equal(SERVER_RECEIVER_ID, '__server__')
  assert.equal(receiverDisplayName(SERVER_RECEIVER_ID), '当前 Server')
})

test('reject confirmation preserves sender files and backups', () => {
  const clientCopy = rejectTransferConfirmation({}, false)
  const serverCopy = rejectTransferConfirmation({}, true)

  assert.match(clientCopy, /本机接收箱/)
  assert.match(serverCopy, /Server 收件箱/)
  assert.match(clientCopy, /不会删除发送方的原始文件/)
  assert.match(serverCopy, /不会影响历史备份/)
})
