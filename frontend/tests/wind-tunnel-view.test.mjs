import assert from 'node:assert/strict'
import test from 'node:test'
import { batchView, generationFailure } from '../src/features/jd/windTunnelView.ts'

test('incomplete F11 shows different recovery for original and repaired configurations', () => {
  const batch = { status: 'limited', stale: false, error_code: 'SUGGESTION_ANALYSIS_INCOMPLETE', suggestions: [] }
  assert.equal(batchView(batch).retry, true)
  const old = batchView({ ...batch, needs_new_batch: true })
  assert.equal(old.retry, undefined)
  assert.match(old.message, /More suggestions/)
  assert.match(old.title, /analysis settings/)
})

test('generation errors explain document, review and persona problems separately', () => {
  assert.match(generationFailure('CV_PROSE_INVALID'), /format limits/)
  assert.match(generationFailure('CV_SUPPORT_INVALID'), /supporting evidence/)
  assert.match(generationFailure('CV_REVIEW_EVIDENCE_INVALID'), /outside this CV/)
  assert.match(generationFailure('CV_PERSONA_DIFFERENCE_INVALID'), /test scenario/)
  assert.match(generationFailure('CV_BASELINE_UNAVAILABLE'), /all-rounder/)
  assert.doesNotMatch(generationFailure('private unknown error text'), /private unknown/)
})

test('a missing keyword probe explains unsupported coverage without offering an unchanged retry', () => {
  const view = batchView({ status: 'limited', stale: false, error_code: 'SUGGESTION_ANALYSIS_INCOMPLETE',
    suggestions: [], generation_failures: { keyword_stuffer: 'CV_KEYWORD_PROBE_UNAVAILABLE' } })
  assert.match(view.message, /no technical skill criteria/)
  assert.equal(view.retry, undefined)
  assert.match(generationFailure('CV_KEYWORD_PROBE_UNAVAILABLE'), /no technical skill criteria/)
})

test('semantic rejection explains completed analysis without a technical-failure banner', () => {
  const view = batchView({ status: 'failed', stale: false, error_code: 'SUGGESTION_SEMANTIC_REVIEW_FAILED', suggestions: [],
    coverage: { expected_assessments: 2, completed_assessments: 2, complete_cohorts: 1 },
    review_rejection_reasons: ['unsupported_source', 'private model prose'] })
  assert.equal(view.tone, 'info')
  assert.equal(view.label, 'No changes approved')
  assert.match(view.title, /CV analysis completed/)
  assert.match(view.message, /not supported by the saved job/)
  assert.doesNotMatch(view.message, /private model prose/)
  assert.equal(view.retryLabel, 'Try suggestions again')
})

test('smaller approved batches disclose the three-proposal target', () => {
  const view = batchView({ status: 'completed', stale: false, error_code: null, requested_suggestions: 3,
    suggestions: [{ status: 'ready' }, { status: 'ready' }] })
  assert.equal(view.label, 'Ready')
  assert.match(view.title, /2 of 3/)
  const provider = batchView({ status: 'failed', stale: false, error_code: 'SUGGESTION_PROVIDER_FAILED', suggestions: [] })
  assert.equal(provider.tone, 'danger')
})
