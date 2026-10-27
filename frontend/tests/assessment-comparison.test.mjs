import assert from 'node:assert/strict'
import { test } from 'node:test'
import { comparisonCriteria, comparisonSelection, comparisonWarnings, toggleComparison, validateComparison } from '../src/features/assessments/comparison.ts'

function entry(id = 'a') {
  return {data: {run: {id: `f6-${id}`, application_id: id, job_id: 'job', kind: 'f6', status: 'completed', synthetic: false,
    profile_version: 1, rubric_version: 'f6-1', prompt_version: 'prompt-1', label: id},
    criteria: [{id: 'python', text: 'Python'}], final_score: {policy: {cv_weight: 30, interview_weight: 70}, warnings: []},
    result: {findings: {}, source_registry: [], provenance: {}, warnings: [], component_scores: {cv: {dimensions: [{dimension: 'technical', approved_percentage: '50'}]}}}},
    performance: {scoring_settings: {revision: 1, policy_sha256: 'same', rubric_version: 'performance-1'}, warnings: []}}
}

test('only completed F6 results can be selected; two selections retain their exact runs across pages', () => {
  const candidate = {id: 'a', name: 'A', assessment: {...entry().data.run, outdated: true}}
  assert.equal(comparisonSelection({...candidate, assessment: {...candidate.assessment, status: 'running'}}), null)
  assert.equal(comparisonSelection({...candidate, assessment: {...candidate.assessment, kind: 'f5'}}), null)
  const a = comparisonSelection(candidate)
  const b = {...a, id: 'b', runId: 'f6-b'}
  assert.deepEqual(toggleComparison([a], b), [a, b])
  assert.deepEqual(toggleComparison([a, b], {...b, id: 'c'}), [a, b])
  assert.deepEqual(toggleComparison([a, b], a), [b])
  assert.equal(a.runId, 'f6-a')
})

test('comparison rejects substituted runs, jobs, candidates, unfinished results and sample mismatches', () => {
  const {data} = entry()
  const selection = {id: 'a', runId: 'f6-a', synthetic: false}
  assert.doesNotThrow(() => validateComparison(data, selection, 'job'))
  for (const changes of [{id: 'other'}, {job_id: 'other'}, {application_id: 'other'}, {status: 'running'}, {kind: 'f5'}, {synthetic: true}]) {
    assert.throws(() => validateComparison({...data, run: {...data.run, ...changes}}, selection, 'job'), /no longer available/)
  }
  assert.throws(() => validateComparison({...data, result: null}, selection, 'job'))
})

test('criteria align by exact ID and text, never by row position or changed wording', () => {
  const a = entry(), b = entry('b')
  b.data.criteria = [{id: 'sql', text: 'SQL'}, {id: 'python', text: 'Advanced Python'}]
  assert.deepEqual(comparisonCriteria([a, b]).map(row => row.text), ['Python', 'SQL', 'Advanced Python'])
  assert.match(comparisonWarnings([a, b]).join(' '), /Criteria versions differ/)
  b.data.criteria = [{text: 'Python', id: 'python'}]
  assert.equal(comparisonCriteria([a, b]).length, 1)
  assert.equal(comparisonWarnings([a, b]).length, 0)
})

test('comparison exposes unknown and different scoring policies, historical criteria and source limitations', () => {
  const a = entry(), b = entry('b')
  assert.deepEqual(comparisonWarnings([a, b]), [])
  b.performance.scoring_settings.revision = 2
  b.data.outdated = true
  b.data.result.warnings = ['Transcript timing is unverified.']
  b.data.result.component_scores.cv.dimensions[0].approved_percentage = '80'
  b.data.final_score.policy.cv_weight = 40
  let warnings = comparisonWarnings([a, b]).join(' ')
  for (const text of ['Interview scoring revisions', 'older job criteria', 'Transcript timing', 'CV scoring weights differ', 'Combined-score policies differ']) assert.ok(warnings.includes(text), text)
  b.performance = null
  warnings = comparisonWarnings([a, b]).join(' ')
  assert.match(warnings, /compatibility could not be established/)
  assert.match(warnings, /Missing performance results are not zero/)
})
