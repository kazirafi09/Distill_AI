import assert from 'node:assert/strict'
import { test } from 'node:test'
import { candidateHistory, cvCoverage, findingsFor, formatScoreValue, numericScore, savedReport } from '../src/features/assessments/assessmentView.ts'

test('coverage uses CV decisions rather than interview weaknesses for the same criterion', () => {
  const sources = [{ kind: 'cv', criterion_ids: ['python'], decision: 'met' },
    { kind: 'interview', criterion_ids: ['python'], score_100: 0, decision: 'unmet' }]
  assert.deepEqual(cvCoverage(sources, 'python'), { label: 'Documented in CV', tone: 'strengths' })
  assert.equal(cvCoverage(sources, 'unasked').label, 'CV detail unavailable')
  assert.equal(cvCoverage([{ kind: 'cv', criterion_ids: ['python'], decision: 'partially_met' }], 'python').label, 'Partially documented')
})

test('saved run history does not include another candidate or substitute a missing selected run', () => {
  const selected = { id: 'current', application_id: 'a', synthetic: false, created_at: '2026-10-22' }
  const runs = [{ ...selected, id: 'other', application_id: 'b' }, { ...selected, id: 'older', created_at: '2026-10-19' }]
  assert.deepEqual(candidateHistory(selected, runs).map(run => run.id), ['current', 'older'])
})

test('findings preserve all original source IDs and category when filtered', () => {
  const finding = { criterion_id: 'python', text: 'Needs clarification.', source_ids: ['cv-1', 'iv-2'] }
  const report = { findings: { strengths: [], weaknesses: [], evidence_gaps: [finding] } }
  const result = findingsFor(report)
  assert.equal(result[0].section, 'evidence_gaps')
  assert.deepEqual(result[0].source_ids, finding.source_ids)
  assert.deepEqual(findingsFor(null), [])
})

test('score rendering preserves zero and treats missing or invalid scores as unknown', () => {
  assert.equal(numericScore(0), 0)
  assert.equal(numericScore('78.67'), 78.67)
  for (const value of [null, undefined, '', 'not available', -1, 101]) assert.equal(numericScore(value), null)
})

test('long stored decimals display at most two places without changing zero or missing scores', () => {
  assert.equal(formatScoreValue('78.666666666666666666'), '78.67')
  assert.equal(formatScoreValue('100.0000'), '100')
  assert.equal(formatScoreValue(0), '0')
  assert.equal(formatScoreValue(null), '—')
})


test('withheld consolidation findings are never presented as a published assessment', () => {
  const result = {findings: {}, component_scores: {}, source_registry: [], provenance: {}}
  for (const status of ['queued', 'running', 'failed', 'needs_review']) {
    assert.equal(savedReport({run: {kind: 'f6', status}, result}), null)
  }
  assert.equal(savedReport({run: {kind: 'f5', status: 'completed'}, result}), null)
  assert.equal(savedReport({run: {kind: 'f6', status: 'completed'}, result}), result)
})
