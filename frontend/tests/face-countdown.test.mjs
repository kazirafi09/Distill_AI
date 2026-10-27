import assert from 'node:assert/strict'
import test from 'node:test'
import { advanceFaceCountdown } from '../src/features/interviews/faceCountdown.ts'

test('countdown requires five uninterrupted seconds with exactly one face', () => {
  assert.deepEqual(advanceFaceCountdown(null, 0, 0), { startedAt: null, seconds: null, ready: false })
  assert.deepEqual(advanceFaceCountdown(null, 2, 1000), { startedAt: null, seconds: null, ready: false })
  assert.deepEqual(advanceFaceCountdown(null, 1, 2000), { startedAt: 2000, seconds: 5, ready: false })
  assert.equal(advanceFaceCountdown(2000, 1, 6999).ready, false)
  assert.equal(advanceFaceCountdown(2000, 1, 7000).ready, true)
})

test('an entering face, disappearing face or unknown count resets the entire timer', () => {
  for (const faceCount of [0, 2, 3, null]) {
    const interrupted = advanceFaceCountdown(1000, faceCount, 4500)
    assert.deepEqual(interrupted, { startedAt: null, seconds: null, ready: false })
    const restarted = advanceFaceCountdown(interrupted.startedAt, 1, 5000)
    assert.equal(restarted.seconds, 5)
    assert.equal(advanceFaceCountdown(restarted.startedAt, 1, 9000).ready, false)
    assert.equal(advanceFaceCountdown(restarted.startedAt, 1, 10000).ready, true)
  }
})
