// Offline acceptance: real compiled UI, fixture APIs, no models or live data.
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { mkdir, readFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import { extname, resolve, sep } from 'node:path'
const { chromium } = createRequire(import.meta.url)('playwright')
const dist = fileURLToPath(new URL('../dist/', import.meta.url))
const output = fileURLToPath(new URL('../../output/wind-tunnel/', import.meta.url))
await mkdir(output, { recursive: true })
const original = 'Docker and CI/CD experience.'
const proposed = 'Experience using Docker and working with CI/CD pipelines.'
const profile = { criteria: [{ id: 'docker', text: original, dimension: 'technical_skills', priority: 'Preferred' }],
  dimension_weights: { technical_skills: 100, work_experience: 0, qualifications: 0, additional_criteria: 0 },
  weights_confirmed: true, minimum_years_experience: null, fresher_eligibility: null, google_form_url: null }
const content = `# Backend Developer\n\n## Responsibilities\nBuild REST APIs.\nMaintain internal services.\n\n## Preferred\n${original}`
const job = { id: 'job-1', number: 136, title: 'Backend Developer', conversation_id: 'jd-1', created_at: '2026-10-24T05:00:00Z' }
let applied = false, conflict = false, locked = false, saveConflict = false, mode = 'completed', applyCalls = 0, moreCalls = 0, generationIssue = false
let multiple = false, appliedIds = [], previewIds = []
const editFor = ids => ({ content: ids.reduce((text, id) => id === 'proposal-1' ? text.replace(original, proposed) : id === 'proposal-2' ? text.replace('Build REST APIs.', 'Develop REST APIs.') : text.replace('Maintain internal services.', 'Maintain backend services.'), content),
  screening_profile: ids.includes('proposal-1') ? { ...profile, criteria: [{ ...profile.criteria[0], text: proposed }] } : profile })
const version = () => applied || conflict ? 'b'.repeat(64) : 'a'.repeat(64)
const jobDetail = () => ({ ...job, content: applied ? editFor(appliedIds).content : content,
  editable_content: applied ? editFor(appliedIds).content : content, edit_version: version(), editing_locked: locked,
  posting_status: locked ? 'posted' : null, screening_profile: applied ? editFor(appliedIds).screening_profile : profile, profile_version: applied && appliedIds.includes('proposal-1') ? 2 : 1 })
const conversation = () => ({ conversation_id: 'jd-1', status: 'approved', created_at: job.created_at, checkpoint_id: null,
  messages: [], interrupt: null, draft_jd: content, approved_jd: jobDetail().content, revision_count: 1,
  screening_profile: jobDetail().screening_profile, profile_version: jobDetail().profile_version, acceptance_errors: [], error_code: null, job_description: job, edit_version: version() })
const assistant = () => ({ conversation_id: 'chat-1', created_at: job.created_at, checkpoint_id: null, mode: 'chat', messages: [], error_code: null, jd_conversation: conversation() })
const suggestion = () => ({ id: 'proposal-1', title: 'Clarify deployment experience', reason: 'Express the existing tools as experience using them.',
  status: applied ? 'applied' : 'ready', applied_at: applied ? job.created_at : null, basis: 'jd_quality',
  affected_areas: ['description', 'criteria'], effect_scope: 'requires_fresh_matching', finding_ids: [],
  patches: [{ kind: 'description', expected: original, proposed }, { kind: 'criterion', criterion_id: 'docker', field: 'text', expected: original, proposed }] })
const extraSuggestion = (id, title, expected, replacement) => ({ ...suggestion(), id, title, affected_areas: ['description'],
  status: applied ? appliedIds.includes(id) ? 'applied' : 'stale' : 'ready',
  patches: [{ kind: 'description', expected, proposed: replacement }] })
const fixtureSuggestions = () => {
  const first = { ...suggestion(), status: applied ? appliedIds.includes('proposal-1') ? 'applied' : 'stale' : 'ready' }
  return multiple ? [first, extraSuggestion('proposal-2', 'Clarify API wording', 'Build REST APIs.', 'Develop REST APIs.'),
    extraSuggestion('proposal-3', 'Clarify service wording', 'Maintain internal services.', 'Maintain backend services.')] : [first]
}
const batch = () => ({ id: 'batch-1', run_id: 'run-1', status: mode, stale: conflict || applied && multiple, trigger: 'approval', created_at: job.created_at,
  error_code: generationIssue ? 'SUGGESTION_ANALYSIS_INCOMPLETE' : null, needs_new_batch: generationIssue, generation_failures: generationIssue ? { keyword_stuffer: 'CV_REVIEW_EVIDENCE_INVALID' } : {}, stage: 'assess_cohorts', suggestions: mode === 'completed' ? fixtureSuggestions() : [],
  progress: { step: 3, total_steps: 6, label: 'Assessing CVs', documents_completed: 2, documents_total: 2,
    assessments_completed: mode === 'completed' ? 2 : 1, assessments_total: 2, criteria_completed: 4, criteria_total: 6, worker_connected: true, updated_at: job.created_at } })
const browser = await chromium.launch({ headless: true, ...(process.env.UI_TEST_BROWSER ? { executablePath: process.env.UI_TEST_BROWSER } : { channel: 'chrome' }) })
const page = await browser.newPage({ viewport: { width: 1280, height: 600 } })
const errors = []
page.on('pageerror', error => errors.push(error.message))
await page.route('**/*', async route => {
  const pathname = new URL(route.request().url()).pathname
  const file = pathname.startsWith('/recruiter/') ? resolve(dist, 'index.html') : resolve(dist, `.${pathname}`)
  if (!file.startsWith(dist.endsWith(sep) ? dist : dist + sep)) return route.abort()
  try { await route.fulfill({ body: await readFile(file), contentType: { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml' }[extname(file)] ?? 'application/octet-stream' }) }
  catch { await route.fulfill({ status: 404, body: 'Not found' }) }
})
await page.route('**/api/**', async route => {
  const path = new URL(route.request().url()).pathname.replace(/^\/api/, '')
  let data
  if (path === '/auth/refresh') data = { access_token: 'fixture-token', token_type: 'bearer' }
  else if (path === '/auth/me') data = { id: 'fixture-user', email: 'recruiter@example.invalid', role: 'recruiter' }
  else if (path === '/health/db') data = { status: 'ok', database: 'connected' }
  else if (path === '/cv/source-checks') data = []
  else if (path === '/jd/descriptions') data = { items: [job], limit: 10, offset: 0 }
  else if (path.endsWith('/suggestions/preview')) {
    previewIds = route.request().postDataJSON().suggestion_ids
    assert(previewIds.length >= 1 && previewIds.length <= 3)
    data = { edit: editFor(previewIds), review_token: 'reviewed-fixture' }
  }
  else if (path.endsWith('/suggestions/apply')) {
    applyCalls++
    const body = route.request().postDataJSON()
    assert.equal(body.criteria_reviewed, true)
    assert.equal(body.review_token, 'reviewed-fixture')
    assert.deepEqual(body.suggestion_ids, previewIds)
    if (saveConflict) return route.fulfill({ status: 409, json: { detail: 'The job changed.' } })
    appliedIds = [...body.suggestion_ids]; applied = true; data = conversation()
  } else if (path.endsWith('/suggestions/more')) { moreCalls++; data = { id: 'batch-new', status: 'queued' } }
  else if (path.endsWith('/suggestions')) data = { edit_version: version(), editing_locked: locked, batches: [batch()] }
  else if (path === '/jd/descriptions/job-1') data = jobDetail()
  else if (path === '/assistant/conversations') data = { items: [], limit: 20, offset: 0 }
  else if (path.includes('/assistant/job-descriptions/') || path.startsWith('/assistant/conversations/')) data = assistant()
  else if (path.includes('/references')) data = []
  else return route.fulfill({ status: 404, json: { detail: `Unmocked endpoint: ${path}` } })
  return route.fulfill({ json: data })
})
async function openSuggestions() {
  await page.goto('http://f11-fixture.test/recruiter/job-descriptions')
  await page.getByRole('navigation', { name: 'Job description sections' }).getByRole('button', { name: 'Suggestions', exact: true }).click()
  await page.getByRole('heading', { name: 'Suggestions', exact: true }).waitFor()
}
async function capture(name) {
  await page.screenshot({ path: `${output}/${name}.png`, fullPage: true })
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
}
try {
  await openSuggestions()
  await page.getByRole('button', { name: 'Review suggestion', exact: true }).waitFor()
  assert.equal(await page.locator('.suggestion-exact-changes').evaluate(element => element.open), false)
  await capture('suggestions-desktop')
  await page.getByText('Show exact changes', { exact: true }).click()
  await capture('exact-edits-desktop')
  await page.getByRole('button', { name: 'Review suggestion', exact: true }).click()
  const apply = page.getByRole('button', { name: 'Apply suggestion', exact: true })
  await apply.waitFor()
  assert.equal(await apply.isDisabled(), true)
  await capture('review-desktop')
  await page.getByRole('button', { name: 'Cancel review' }).click()
  assert.equal(applyCalls, 0)
  await page.getByRole('button', { name: 'Review suggestion', exact: true }).click()
  await page.getByRole('checkbox', { name: 'I reviewed all selected changes in the description and screening criteria.' }).check()
  await apply.click()
  await page.getByRole('button', { name: 'View applied change' }).waitFor()
  assert.equal(applyCalls, 1)
  assert.equal(moreCalls, 0)
  await capture('applied-desktop')
  applied = false
  await page.setViewportSize({ width: 390, height: 844 })
  await openSuggestions()
  await page.getByRole('button', { name: 'Review suggestion', exact: true }).waitFor()
  await capture('exact-edits-mobile')
  await page.getByRole('button', { name: 'Review suggestion', exact: true }).click()
  await page.getByRole('checkbox', { name: 'I reviewed all selected changes in the description and screening criteria.' }).check()
  conflict = true
  await page.getByText('The job changed or became locked after you started this review.', { exact: true }).waitFor()
  assert.equal(await apply.isDisabled(), true)
  await capture('review-conflict-mobile')
  conflict = false; saveConflict = true
  await openSuggestions()
  await page.getByRole('button', { name: 'Review suggestion', exact: true }).click()
  await page.getByRole('checkbox', { name: 'I reviewed all selected changes in the description and screening criteria.' }).check()
  await apply.click()
  await page.getByText('The job changed or became locked after you started this review.', { exact: true }).waitFor()
  assert.equal(await apply.isDisabled(), true)
  saveConflict = false; mode = 'running'
  await openSuggestions()
  await page.getByRole('progressbar', { name: 'Analysis stages completed' }).waitFor()
  await page.getByText('1/2 assessed', { exact: false }).waitFor()
  await capture('running-mobile')
  mode = 'completed'; locked = true
  await openSuggestions()
  await page.getByRole('button', { name: 'Review suggestion', exact: true }).waitFor()
  assert.equal(await page.getByRole('button', { name: 'Review suggestion', exact: true }).isDisabled(), true)
  assert.equal(await page.getByRole('button', { name: 'More suggestions', exact: true }).isDisabled(), true)
  locked = false; mode = 'limited'; generationIssue = true
  await openSuggestions()
  await page.getByText('Updated analysis settings are available.', { exact: true }).waitFor()
  await page.getByText('The review referenced text or passages outside this CV.', { exact: false }).waitFor()
  assert.equal(await page.getByRole('button', { name: 'Retry', exact: true }).count(), 0)
  await capture('generation-recovery-mobile')
  generationIssue = false; mode = 'completed'; multiple = true; applied = false; appliedIds = []
  await page.setViewportSize({ width: 1280, height: 600 })
  await openSuggestions()
  await page.locator('.suggestion-select input').first().waitFor()
  assert.equal(await page.locator('.suggestion-select input').count(), 3)
  await page.getByRole('button', { name: 'Select all', exact: true }).click()
  await page.getByRole('button', { name: 'Review selected (3)', exact: true }).click()
  const applyThree = page.getByRole('button', { name: 'Apply 3 suggestions', exact: true })
  await applyThree.waitFor()
  assert.equal(await applyThree.isDisabled(), true)
  assert.equal(previewIds.length, 3)
  assert.equal(await page.locator('.suggestion-added').count(), 3, 'Every selected edit must be highlighted, including two edits in one paragraph.')
  const beforeCancel = applyCalls
  await capture('multi-review-desktop')
  await page.getByRole('button', { name: 'Cancel review', exact: true }).click()
  assert.equal(applyCalls, beforeCancel)
  await page.getByRole('button', { name: 'Clear selection', exact: true }).click()
  assert.equal(await page.getByRole('button', { name: 'Review selected (0)', exact: true }).isDisabled(), true)
  await page.locator('.suggestion-select input').nth(0).check()
  await page.locator('.suggestion-select input').nth(1).check()
  await page.getByRole('button', { name: 'Review selected (2)', exact: true }).click()
  assert.deepEqual([...previewIds].sort(), ['proposal-1', 'proposal-2'])
  await page.getByRole('checkbox', { name: 'I reviewed all selected changes in the description and screening criteria.' }).check()
  saveConflict = true
  await page.getByRole('button', { name: 'Apply 2 suggestions', exact: true }).click()
  await page.getByText('The job changed or became locked after you started this review.', { exact: true }).waitFor()
  assert.equal(await page.getByRole('button', { name: 'Apply 2 suggestions', exact: true }).isDisabled(), true)
  assert.equal(applied, false)
  await page.getByRole('button', { name: 'Cancel review', exact: true }).click()
  saveConflict = false
  await page.getByRole('button', { name: 'Review selected (2)', exact: true }).click()
  await page.getByRole('checkbox', { name: 'I reviewed all selected changes in the description and screening criteria.' }).check()
  await page.getByRole('button', { name: 'Apply 2 suggestions', exact: true }).click()
  await page.getByRole('button', { name: 'View applied change', exact: true }).first().waitFor()
  assert.equal(await page.getByRole('button', { name: 'View applied change', exact: true }).count(), 2)
  assert.deepEqual(appliedIds, ['proposal-1', 'proposal-2'])
  assert.equal(await page.getByRole('button', { name: 'Review suggestion', exact: true }).isDisabled(), true)
  await capture('multi-applied-subset-desktop')
  applied = false; appliedIds = []
  await page.setViewportSize({ width: 390, height: 844 })
  await openSuggestions()
  await page.getByRole('button', { name: 'Select all', exact: true }).click()
  await capture('multi-selection-mobile')
  await page.getByRole('button', { name: 'Review selected (3)', exact: true }).click()
  await page.getByRole('checkbox', { name: 'I reviewed all selected changes in the description and screening criteria.' }).check()
  await page.getByRole('button', { name: 'Apply 3 suggestions', exact: true }).click()
  await page.getByRole('button', { name: 'View applied change', exact: true }).first().waitFor()
  assert.equal(await page.getByRole('button', { name: 'View applied change', exact: true }).count(), 3)
  assert.deepEqual(appliedIds, ['proposal-1', 'proposal-2', 'proposal-3'])
  await capture('multi-applied-all-mobile')
  assert.deepEqual(errors, [])
  console.log('F11 browser acceptance passed: single/multi-select, 2/3 Apply, exact changes, cancel, 409 conflicts, stale unselected cards, mobile, progress and locks.')
} finally { await browser.close() }
