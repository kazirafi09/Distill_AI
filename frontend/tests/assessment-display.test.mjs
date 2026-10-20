import assert from 'node:assert/strict'
import { test } from 'node:test'
import { scoreLabel, stageLabel, timeLabel, errorLabel } from '../src/features/assessments/format.ts'

test('unknown scores remain distinct from assessed zero and precise averages', () => {
  assert.equal(scoreLabel(null), 'Not assessed')
  assert.equal(scoreLabel(undefined), 'Not assessed')
  assert.equal(scoreLabel(0), '0/100')
  assert.equal(scoreLabel('95.83'), '95.83/100')
  assert.equal(scoreLabel('100.00'), '100/100')
})
test('processing labels cover queued retries and parallel answer evaluation', () => {
  assert.equal(stageLabel('retry_wait'), 'Waiting to retry')
  assert.equal(stageLabel('evaluation-g2-accuracy-attempts'), 'Evaluating answers')
  assert.equal(stageLabel('model-version'), 'Checking model')
  assert.match(errorLabel('ASSESSMENT_VERSION_CHANGED'), /Start a new assessment/)
})
test('evidence times use the common interview timeline', () => {
  assert.equal(timeLabel(47500), '0:47')
  assert.equal(timeLabel(440000), '7:20')
})
