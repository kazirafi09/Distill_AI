import assert from 'node:assert/strict'
import test from 'node:test'
import { screeningProgress } from '../src/features/cv/screeningProgressView.ts'

const run = { id: 'run', status: 'running', total: 2, finished: 0, completed: 0,
  progress: { stage: 'parsing', parsed: 0, criteria_completed: 0, criteria_total: 12, scored: 0,
    read_skipped: 0, criteria_skipped: 0, score_skipped: 0, completed_units: 0, total_units: 16, skipped_units: 0 } }

test('reading is visible before the first candidate is scored', () => {
  const result = screeningProgress(run)
  assert.equal(result.title, 'Reading CVs')
  assert.equal(result.stages[0].state, 'active')
  assert.equal(result.stages[1].state, 'waiting')
  assert.equal(result.percentage, 0)
})

test('saved criterion matches advance progress before a candidate finishes', () => {
  const result = screeningProgress({ ...run, progress: { ...run.progress, stage: 'matching', parsed: 1, criteria_completed: 3, completed_units: 4 } })
  assert.equal(result.percentage, 25)
  assert.equal(result.stages[1].state, 'active')
  assert.equal(result.stages[1].done, 3)
  assert.equal(result.scored, 0)
})

test('completed processing with failures shows unavailable steps rather than successful scores', () => {
  const result = screeningProgress({ ...run, status: 'completed_with_issues', finished: 2, completed: 1,
    progress: { ...run.progress, stage: null, parsed: 1, read_skipped: 1, criteria_completed: 6, criteria_skipped: 6,
      scored: 1, score_skipped: 1, completed_units: 16, skipped_units: 8 } })
  assert.equal(result.percentage, 100)
  assert.equal(result.scored, 1)
  assert.equal(result.stages[0].state, 'issue')
  assert.equal(result.stages[2].state, 'issue')
  assert.equal(result.active, false)
})

test('older responses do not invent completed parsing or criterion checks', () => {
  const result = screeningProgress({ id: 'old', status: 'running', total: 2, finished: 0, completed: 0 })
  assert.equal(result.percentage, null)
  assert.equal(result.stages[0].done, undefined)
  assert.equal(result.stages[1].done, undefined)
})
