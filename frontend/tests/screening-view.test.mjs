import assert from 'node:assert/strict'
import { test } from 'node:test'
import { candidateStatus, evidenceLink, reviewCount, sourceLabel, validScore } from '../src/features/cv/screeningView.ts'

const application = {
  id: 'application-1', candidate_id: 'candidate-1', full_name: 'Amina', email: 'amina@example.com',
  created_at: '2026-10-08T10:00:00Z', submitted_at_text: null,
  cv_documents: [{ id: 'cv-1', drive_file_id: 'drive-1', file_version_id: 'version-1', download_status: 'downloaded', original_filename: 'CV.pdf' }],
}
const assessment = {
  id: 'assessment-1', application_id: application.id, name: 'Amina', status: 'completed', outdated: false,
  score: { aggregate: '82.5', must_have_review_flags: ['criterion-1'] }, matches: [], warnings: [], error_code: null,
}

test('missing and invalid scores never become zero or a completed screening result', () => {
  for (const score of [null, undefined, '', ' ', 'NaN', 'Infinity', '-1', '101']) {
    const item = { ...assessment, score: { ...assessment.score, aggregate: score } }
    assert.equal(validScore(item), null)
    assert.notEqual(candidateStatus(application, item).key, 'screened')
    assert.equal(reviewCount(item), 0)
  }
  assert.equal(validScore({ ...assessment, score: { aggregate: '0' } }), 0)
  assert.equal(candidateStatus(application, { ...assessment, score: { aggregate: '0' } }).key, 'screened')
})

test('outdated scores stay visible but are excluded from current review flags', () => {
  const old = { ...assessment, outdated: true }
  assert.equal(validScore(old), 82.5)
  assert.equal(candidateStatus(application, old).key, 'outdated')
  assert.equal(reviewCount(old), 0)
  assert.equal(reviewCount(assessment), 1)
})

test('processing failures and unprocessed applications have separate states', () => {
  assert.equal(candidateStatus(application).key, 'waiting')
  assert.equal(candidateStatus(application, { ...assessment, status: 'failed', score: null }).key, 'issue')
  assert.equal(candidateStatus({ ...application, cv_documents: [{ ...application.cv_documents[0], download_status: 'failed' }] }).key, 'issue')
})

test('evidence links resolve only to the exact saved CV version', () => {
  assert.equal(evidenceLink(application, { file_version_id: 'version-1' }), 'https://drive.google.com/file/d/drive-1/view')
  assert.equal(evidenceLink(application, { file_version_id: 'older-version' }), null)
  assert.equal(evidenceLink({ ...application, cv_documents: [] }), null)
  assert.equal(sourceLabel('pdf-page-2'), 'CV · page 2')
  assert.equal(sourceLabel('docx-body'), 'CV · document body')
})
