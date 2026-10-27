// Offline browser QA: all API requests and frontend assets are served from local fixtures.
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { mkdir, readFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import { extname, resolve, sep } from 'node:path'
const { chromium } = createRequire(import.meta.url)('playwright')
const dist = fileURLToPath(new URL('../dist/', import.meta.url))
const output = fileURLToPath(new URL('../../output/assessment-redesign/', import.meta.url))
await mkdir(output, { recursive: true })
const criteria = ['Python', 'FastAPI and REST API development', 'PostgreSQL, SQL joins, indexes and transactions', 'Automated testing with pytest', 'Docker', 'AWS deployment experience', 'At least 3 years of professional Python backend development', "Bachelor’s degree in Computer Science or related", 'Explain technical decisions clearly in English', 'Kubernetes experience'].map((text, i) => ({ id: `c${i}`, text }))
const topics = ['Self-introduction and back-end experience', 'Difference between Python list and tuple', 'FastAPI async endpoints and blocking calls', 'Testing payment endpoints: success, timeout, duplicate retries', 'Database transactions and race conditions on last-item purchases', 'AWS and Kubernetes deployment experience']
const values = [[100, null, 75, 100], [100, 0, 50, 75], [100, 75, 75, 75], [50, null, 25, 50], [100, 100, 100, 100], [75, null, 50, 75]]
const dimensions = ['relevance', 'accuracy', 'completeness', 'articulation']
const turns = topics.flatMap((topic, index) => [{ id: `q${index}`, text: `${topic}?`, speaker_role: 'recruiter', session_start_ms: null }, { id: `a${index}`, text: index === 1 ? 'Both store ordered items. A tuple can be edited after you make it but it is faster.' : 'This is the saved candidate answer for the interview question.', speaker_role: 'candidate', session_start_ms: null }])
const mappings = [[0, 1, 2, 4, 6], [0], [1], [3], [2], [4, 5, 9]]
const answers = topics.map((topic, i) => ({ group: { id: `g${i}`, topic, answer_state: 'answered', criterion_ids: mappings[i].map(n => `c${n}`), question_turn_ids: [`q${i}`], answer_turn_ids: [`a${i}`], follow_up_turn_ids: [], clarification_turn_ids: [], revisit_turn_ids: [] }, evaluations: Object.fromEntries(dimensions.map((dimension, j) => [dimension, { score: values[i][j] === null ? null : values[i][j] / 25, score_100: values[i][j], reason: values[i][j] === null ? 'SELF_REPORT_UNVERIFIED' : 'ASSESSED', justification: dimension === 'accuracy' && i === 1 ? 'Says a tuple can be changed after creation. The stored answer is incorrect on mutability.' : 'The saved explanation describes what this answer demonstrated and what was missing.', evidence: [{ turn_id: `a${i}`, quote: turns[i * 2 + 1].text }], reference_evidence: [], accuracy_warning: null }])) }))
const run = { id: 'f6', parent_run_id: 'f5', kind: 'f6', job_id: 'job', application_id: 'app', label: 'Amina Chowdhury', status: 'completed', stage: 'completed', synthetic: true, created_at: '2026-10-22T02:38:00Z', updated_at: '2026-10-22T02:38:00Z', profile_version: 2, rubric_version: '1', attempt: 1, max_attempts: 3, completed_groups: 4, total_groups: 4, error_code: null }
const old = { ...run, id: 'f5', kind: 'f5', parent_run_id: null }
const sources = criteria.map((criterion, index) => ({ id: `cv-${index}`, kind: 'cv', criterion_ids: [criterion.id], decision: [5, 9].includes(index) ? 'unmet' : index === 6 ? 'partially_met' : 'met', justification: 'The CV describes the reported experience. This is self-reported evidence.', evidence: [{ quote: 'Built backend services and wrote automated API tests.', source_id: 'pdf-page-1', start: 214, end: 267 }] }))
sources.push({ id: 'iv-1', kind: 'interview', criterion_ids: ['c0'], dimension: 'accuracy', score_100: 0, justification: 'The candidate gave an incorrect explanation of tuple mutability.', evidence: [{ quote: turns[3].text, turn_id: 'a1' }] })
const finding = (n, text) => ({ criterion_id: `c${n}`, text, source_ids: [`cv-${n}`] })
const findings = { strengths: [finding(0, 'Backend engineer and junior developer; lists Python in skills.'), finding(1, 'REST APIs for order management; interview agrees.'), finding(2, 'Joins, indexes and transactions on the CV.'), finding(3, 'Wrote pytest tests for API validation.'), finding(4, 'Packages backend services using Docker.'), finding(7, 'BSc in Computer Science and Engineering.'), finding(8, 'English team discussions and API documentation.')], weaknesses: [finding(5, 'No production AWS; tutorial projects only.'), finding(9, 'States no hands-on Kubernetes experience.'), { ...finding(0, 'Incorrect tuple mutability explanation.'), source_ids: ['iv-1'] }], evidence_gaps: [finding(6, 'Confirm the exact timeline against the three-year requirement.')], recommended_areas: [finding(0, 'Probe Python depth beyond what the CV lists, with a short live exercise.'), finding(1, 'Test understanding of async endpoints that run blocking calls.'), finding(2, 'Ask follow-ups on transaction isolation, locking and race conditions.'), finding(3, 'Verify testing of failure scenarios, timeouts, retries and recovery logic.'), finding(4, 'Test containerisation and deployment skills beyond introductory tutorials.'), finding(5, 'Ask what was deployed and which services were used.')], suitability: { position: 'mixed_evidence', rationale: 'Review the source evidence before a hiring decision.', source_ids: ['cv-0'] } }
const payload = { findings, component_scores: { cv: { aggregate: '78.666666666666666666', dimensions: [{ dimension: 'technical_skills', score: '80', approved_percentage: '55' }], note: 'Saved CV score.' }, interview: {} }, source_registry: sources, provenance: { f5_run_id: 'f5', cv_assessment_id: 'synthetic-cv', repairs_used: 0 }, coverage: criteria.map(criterion => ({ criterion_id: criterion.id, group_ids: answers.filter(answer => answer.group.criterion_ids.includes(criterion.id)).map(answer => answer.group.id), exploration: ['c7', 'c8'].includes(criterion.id) ? 'unasked' : 'explored' })), warnings: ['TEXT_ONLY_FINALIZED'] }
const finalScore = { status: 'provisional', score_100: '62.10', cv_score_100: '78.67', interview_score_100: '55.00', cv_contribution: '23.60', interview_contribution: '38.50', policy: { version: 'candidate-final-1', cv_weight: 30, interview_weight: 70 }, reason: 'For recruiter review; not an automatic hiring decision.', warnings: ['Transcript gaps.'] }
const result = { run, final_score: finalScore, result: payload, answers, dimension_summaries: {}, criteria, source_turns: turns, outdated: false, f6: null }
const workspace = { candidates: [], runs: [run, old], profile_ready: true, has_more_candidates: false, offset: 0, synthetic_demo_available: false }
let postRequests = [], failInsights = false, insightMode = 'empty'
const featureRun = (id, kind, status) => ({...old, id, kind, status, error_code: status === 'needs_review' ? 'EXTENSION_OUTPUT_NEEDS_REVIEW' : null, parent_run_id: 'f5', synthetic: true, can_retry: false})
const policy = {policy_sha256: 'policy', revision: 0, policy: {weights: Object.fromEntries(['technical_competency', 'communication', 'response_relevance', 'problem_solving', 'experience_depth', 'job_alignment'].map(name => [name, 20])), minimum_assessed_dimensions: 4, essential_dimensions: ['technical_competency']}}
const performanceResult = score => ({warnings: [], dimensions: Object.fromEntries(Object.keys(policy.policy.weights).map(name => [name, {score, reason: 'assessed', justification: 'Saved dimension explanation.', basis: name === 'technical_competency' ? 'model_knowledge' : 'interview_only', evidence: [{turn_id: 'a1', quote: turns[3].text, speaker_role: 'candidate', session_start_ms: 1000}], reference_evidence: []}])), summary: {overall_score_100: String(score * 25), assessed_dimensions: 6, assessed_weight: 100, missing_weight: 0, status: 'assessed'}, scoring_settings: policy})
const savedInsights = [
  {run: featureRun('claims', 'cv_claim_review', 'needs_review'), result: null, reviews: [], diagnostics: [{attempt: 1, message: 'Saved CV quotation does not match.', field_path: 'claims.cv_evidence'}]},
  {run: featureRun('performance-current', 'interview_performance', 'completed'), result: performanceResult(3), reviews: []},
  {run: featureRun('performance-old', 'interview_performance', 'completed'), result: performanceResult(2), reviews: []},
  {run: featureRun('quality', 'interview_quality', 'completed'), result: {warnings: [], process_summary: 'Test process quality.', coverage: [{criterion_id: 'c0', text: 'Python', status: 'explored', group_ids: ['g1']}, {criterion_id: 'c7', text: 'Degree', status: 'not_explored', group_ids: []}], findings: [{category: 'follow_up', observation: 'The interviewer explored the answer in group g1.', improvement: 'Ask a practical question after the explanation.', group_ids: ['g1'], evidence: [{turn_id: 'q1', quote: turns[2].text, speaker_role: 'recruiter', session_start_ms: 1000}]}], speaking_time: {status: 'measured', candidate_ms: 60000, recruiter_ms: 30000}}, reviews: []},
]

const browser = await chromium.launch({ headless: true, ...(process.env.UI_TEST_BROWSER ? { executablePath: process.env.UI_TEST_BROWSER } : { channel: 'chrome' }) })
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })
await page.addInitScript(() => {
  window.__assessmentEntries = []
  const animate = Element.prototype.animate
  Element.prototype.animate = function(keyframes, options) {
    if (this.matches('.ca-content')) window.__assessmentEntries.push({ keyframes, options })
    return animate.call(this, keyframes, options)
  }
})
const errors = []
page.on('pageerror', error => errors.push(error.message))
await page.route('**/*', async route => {
  const pathname = new URL(route.request().url()).pathname
  const file = pathname.startsWith('/recruiter/') ? resolve(dist, 'index.html') : resolve(dist, `.${pathname}`)
  if (!file.startsWith(dist.endsWith(sep) ? dist : dist + sep)) return route.abort()
  try { await route.fulfill({ status: 200, body: await readFile(file), contentType: { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml' }[extname(file)] ?? 'application/octet-stream' }) }
  catch { await route.fulfill({ status: 404, body: 'Not found' }) }
})
await page.route('**/api/**', async route => {
  const path = new URL(route.request().url()).pathname.replace(/^\/api/, '')
  let data
  if (path === '/auth/refresh') data = { access_token: 'fixture', token_type: 'bearer' }
  else if (path === '/auth/me') data = { id: 'user', email: 'recruiter@example.com', role: 'recruiter' }
  else if (path === '/jd/descriptions') data = { items: [{ id: 'job', number: 207, title: 'Backend Engineer' }] }
  else if (path === '/assessments/jobs/job') data = workspace
  else if (path.endsWith('/post-interview') && route.request().method() === 'POST') { postRequests.push(route.request().postDataJSON()); data = { blockers: {} } }
  else if (path.endsWith('/post-interview')) {
    if (failInsights) return route.fulfill({ status: 503, json: { detail: 'Insights temporarily unavailable.' } })
    data = { items: insightMode === 'saved' ? savedInsights : [], actions: insightMode === 'saved' ? Object.fromEntries(['cv_claim_review','interview_performance','interview_quality'].map(kind => [kind, {action: 'none', reason: kind === 'cv_claim_review' ? 'Validation attempts are exhausted. The same inputs cannot be retried.' : 'This result is already saved.'}])) : undefined, blockers: { cv_claim_review: null, interview_performance: null, interview_quality: null }, source_warnings: [], scoring_settings: {}, review_required: true, prompt_version: 'interview-extensions-2' }
  }
  else if (path.endsWith('/report.pdf')) return route.fulfill({ contentType: 'application/pdf', body: '%PDF-1.4\n%%EOF' })
  else if (path.endsWith('/report')) data = { title: 'Candidate assessment report', filename: 'assessment.pdf', report_type: 'recruiter', metadata: { run, candidate: { name: run.label }, job: { number: 207, title: 'Backend Engineer' } }, sections: [{ title: 'Assessment overview', blocks: [{ kind: 'text', text: 'Saved report content for this candidate.' }] }] }
  else if (path === '/assessments/runs/f6/result') data = result
  else if (path === '/assessments/runs/f5/result') data = { ...result, final_score: {...finalScore, interview_run_id: 'performance-current'}, pinned_cv: {id: 'pinned-old-cv', score: {aggregate: '78.67'}, matches: sources.filter(source => source.kind === 'cv').map(source => ({requirement_id: source.criterion_ids[0], decision: source.decision, justification: source.justification, evidence: source.evidence}))}, run: old, result: { coverage: payload.coverage, warnings: ['TEXT_ONLY_FINALIZED'] }, f6: { ready: true, review_required: true } }
  else if (path === '/health/db') data = { status: 'ok', database: 'connected' }
  else data = []
  await route.fulfill({ json: data })
})
const nav = () => page.getByRole('navigation', { name: 'Candidate assessment sections' })
const screenshot = async name => { await page.evaluate(() => document.fonts.ready); await page.screenshot({ path: resolve(output, `${name}.png`), animations: 'disabled' }) }
try {
  await page.goto('http://assessment.test/recruiter/assessments?job=job&run=f6')
  await page.getByRole('heading', { name: 'Amina Chowdhury', exact: true }).waitFor()
  await page.getByText('78.67', { exact: false }).first().waitFor()
  await page.getByRole('heading', { name: 'Combined score', exact: true }).waitFor()
  assert.match(await page.getByRole('region', { name: 'Final assessment score' }).innerText(), /Provisional/)
  assert.equal(await nav().getByRole('button').count(), 8)
  assert.equal(await page.getByRole('button', { name: /I reviewed this analysis|Reviewed for this session/ }).count(), 0)
  await page.waitForFunction(() => document.querySelector('.ca-navigation nav')?.dataset.motionIndicator === 'ready')
  const indicatorBefore = await nav().evaluate(element => element.style.getPropertyValue('--motion-indicator-x'))

  assert.equal(await page.locator('.ca-answer-matrix .ca-unassessed').count(), 3)
  assert.equal(await page.locator('.ca-answer-matrix .ca-score-value').filter({ hasText: /^0$/ }).count(), 1)
  await screenshot('summary-1440')
  const savedAnalysis = page.getByRole('combobox', { name: 'Saved candidate analysis' })
  assert.equal(await savedAnalysis.evaluate(element => element.scrollWidth <= element.clientWidth), true)
  await savedAnalysis.click()
  await screenshot('saved-analysis-dropdown-1440')
  await page.keyboard.press('Escape')
  assert.equal(await page.locator('.ca-additional').count(), 0)
  await page.getByRole('button', { name: 'Report & export', exact: true }).click()
  const reportModal = page.getByRole('dialog', { name: 'Report & export', exact: true })
  await reportModal.waitFor()
  assert.equal(await page.locator('.ca-modal').evaluate(element => getComputedStyle(element).animationName), 'motion-rise')
  assert.equal(await page.locator('.ca-modal-overlay').evaluate(element => getComputedStyle(element).animationName), 'motion-fade')
  await screenshot('report-modal-1440')
  await reportModal.getByRole('combobox', { name: 'Report type' }).selectOption('technical')
  assert.equal(await reportModal.getByRole('checkbox', { name: /Include evidence appendix/ }).isChecked(), true)
  assert.equal(await reportModal.getByRole('checkbox', { name: /Include evidence appendix/ }).isDisabled(), true)
  await reportModal.getByRole('combobox', { name: 'Report type' }).selectOption('recruiter')
  await reportModal.getByRole('button', { name: 'Preview report', exact: true }).click()
  await reportModal.getByRole('heading', { name: 'Candidate assessment report', exact: true }).waitFor()
  await screenshot('report-preview-1440')
  const downloadEvent = page.waitForEvent('download')
  await reportModal.getByRole('button', { name: 'Download PDF', exact: true }).click()
  assert.match((await downloadEvent).suggestedFilename(), /distill-assessment-f6/)
  await page.evaluate(() => { window.__printCalls = 0; window.print = () => { window.__printCalls += 1 } })
  await reportModal.getByRole('button', { name: 'Print', exact: true }).click()
  await page.waitForFunction(() => window.__printCalls === 1)
  await page.keyboard.press('Escape')
  assert.equal(await page.getByRole('dialog').count(), 0)
  assert.equal(await page.getByRole('button', { name: 'Report & export', exact: true }).evaluate(element => element === document.activeElement), true)

  await nav().getByRole('button', { name: 'Assessment', exact: false }).click()
  await page.waitForFunction(previous => document.querySelector('.ca-navigation nav').style.getPropertyValue('--motion-indicator-x') !== previous, indicatorBefore)
  assert.match(await nav().evaluate(element => getComputedStyle(element, '::after').transitionProperty), /transform/)
  await page.waitForFunction(() => window.__assessmentEntries.length >= 1)
  const tabEntry = await page.evaluate(() => window.__assessmentEntries.at(-1))
  assert.equal(tabEntry.options.duration, 120)
  assert.equal(tabEntry.keyframes.some(frame => 'transform' in frame), false)
  await page.getByRole('button', { name: /Incorrect tuple mutability explanation/ }).click()
  await page.getByRole('button', { name: /INTERVIEW.*View source/ }).click()
  await page.getByRole('dialog', { name: 'Source transcript' }).waitFor()
  assert.equal(await page.locator('.ca-transcript-turn.highlighted').count(), 1)
  await page.keyboard.press('Tab')
  assert.equal(await page.evaluate(() => !!document.activeElement.closest('[role="dialog"]')), true)
  await page.keyboard.press('Escape')
  assert.equal(await page.getByRole('dialog').count(), 0)
  await page.getByRole('button', { name: /Gaps 1/ }).click()
  assert.equal(await page.locator('.ca-findings-table .ca-row-trigger').count(), 1)
  await page.getByRole('button', { name: /Confirm the exact timeline/ }).click()
  await screenshot('findings-1440')
  await nav().getByRole('button', { name: /Interview Q&A/ }).click()
  await page.getByRole('button', { name: 'Expand question 2', exact: true }).click()
  await page.getByRole('heading', { name: 'Why these scores', exact: true }).waitFor()
  await screenshot('qa-1440')
  await nav().getByRole('button', { name: /Criteria coverage/ }).click()
  const python = page.locator('.ca-coverage-table tbody tr').first()
  assert.match(await python.innerText(), /Documented in CV/)
  await screenshot('coverage-1440')
  await nav().getByRole('button', { name: /Verify next/ }).click()
  assert.equal(await page.locator('.ca-verification-list > article').count(), 6)
  await screenshot('verify-1440')
  await page.locator('.ca-more-details > summary').click()
  await page.getByRole('button', { name: 'Run history', exact: true }).click()
  await page.getByRole('dialog', { name: 'Run history' }).waitFor()
  await page.keyboard.press('Escape')
  await nav().getByRole('button', { name: 'Summary', exact: true }).click()
  await page.setViewportSize({ width: 1280, height: 600 })
  assert.equal((await page.locator('.ca-total').innerText()).replace(/\s+/g, ' '), '62.10 / 100')
  assert.equal(await page.locator('.ca-total').evaluate(element => element.scrollWidth <= element.clientWidth), true)
  await screenshot('summary-1280')
  await nav().getByRole('button', { name: /Interview Q&A/ }).click()
  await screenshot('qa-1280')
  await page.setViewportSize({ width: 390, height: 844 })
  await page.waitForFunction(() => getComputedStyle(document.querySelector('.sidebar-drawer')).visibility === 'hidden')
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth), false)
  await screenshot('qa-mobile')
  await nav().getByRole('button', { name: 'Summary', exact: true }).click()
  await screenshot('summary-mobile')
  await page.getByRole('button', { name: 'Report & export', exact: true }).click()
  await page.getByRole('dialog', { name: 'Report & export', exact: true }).waitFor()
  assert.equal(await page.locator('.ca-modal').evaluate(element => element.getBoundingClientRect().right <= window.innerWidth), true)
  await screenshot('report-modal-mobile')
  await page.keyboard.press('Escape')

  await page.setViewportSize({ width: 1440, height: 900 })
  await page.locator('.ca-more-details > summary').click()
  await page.getByRole('button', { name: 'Post-interview insights', exact: true }).click()
  const runAll = page.getByRole('button', { name: 'Run all available insights', exact: true })
  await runAll.waitFor()
  assert.equal(await runAll.isDisabled(), true)
  await page.getByRole('checkbox', { name: 'I reviewed the saved questions, scores and evidence.', exact: true }).click()
  await runAll.click()
  await page.waitForFunction(() => !document.querySelector('.post-interview-actions button')?.disabled)
  assert.deepEqual(postRequests, [{ features: ['cv_claim_review', 'interview_performance', 'interview_quality'], confirm_f5_review: true }])
  await page.keyboard.press('Escape')
  // Verify an analysis without F6 findings uses honest empty states and review-gated consolidation.
  await page.getByRole('combobox', { name: 'Saved candidate analysis' }).selectOption('f5')
  await page.getByRole('heading', { name: 'Complete the candidate assessment' }).waitFor()
  assert.equal(await page.getByRole('button', { name: 'Generate consolidated assessment', exact: true }).isDisabled(), true)
  assert.equal(await page.locator('.ca-preview-grid .ca-count').count(), 0)
  await page.getByRole('heading', {name: 'Consolidated assessment not generated', exact: true}).waitFor()
  await page.getByRole('checkbox', { name: 'I reviewed the saved questions, scores and evidence.', exact: true }).click()
  assert.equal(await page.getByRole('button', { name: 'Generate consolidated assessment', exact: true }).isDisabled(), false)
  // The F5 coverage screen must use its pinned CV without requiring F6.
  await nav().getByRole('button', {name: /Criteria coverage/}).click()
  assert.match(await page.locator('.ca-coverage-table tbody tr').first().innerText(), /Documented in CV/)
  assert.equal(await page.getByText('No CV assessment pinned', {exact: true}).count(), 0)
  // Completed, exhausted, historical and synthetic states remain independently readable.
  insightMode = 'saved'
  await page.reload()
  await nav().getByRole('button', {name: 'Performance', exact: true}).click()
  await page.locator('.post-interview-total').waitFor()
  assert.equal(await page.locator('.pi-performance-table tbody tr').count(), 6)
  assert.equal(await page.locator('.pi-performance-table .pi-row-detail').count(), 0)
  const contentWidth = await page.locator('.ca-content').evaluate(el => el.clientWidth - parseFloat(getComputedStyle(el).paddingLeft) - parseFloat(getComputedStyle(el).paddingRight))
  assert.ok(Math.abs((await page.locator('.post-interview-assessment').boundingBox()).width - contentWidth) < 3)
  await screenshot('performance-overview-1440')
  await page.setViewportSize({width: 390, height: 844})
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth), false)
  await screenshot('performance-overview-mobile')
  await page.setViewportSize({width: 1440, height: 900})
  assert.equal(await page.getByRole('button', {name: /Run all available insights|Retry saved analysis/}).count(), 0)
  await page.getByRole('combobox', {name: 'Saved analysis', exact: true}).selectOption('performance-old')
  await page.getByText('The combined score uses a different saved performance run.', {exact: false}).waitFor()
  await page.locator('.pi-performance-table .ca-citation').first().click()
  await page.getByRole('button', {name: 'View in source transcript', exact: true}).first().click()
  await page.getByRole('dialog', {name: 'Source transcript'}).waitFor()
  await page.mouse.click(5, 400)
  await page.getByRole('dialog').waitFor({state: 'hidden'})
  assert.equal(await page.getByRole('combobox', {name: 'Saved analysis', exact: true}).inputValue(), 'performance-old')
  assert.equal(await nav().getByRole('button', {name: 'Performance', exact: true}).getAttribute('aria-current'), 'page')
  await screenshot('performance-1440')
  await nav().getByRole('button', {name: 'CV claims', exact: true}).click()
  assert.equal(await page.getByRole('button', {name: /Run cv claim review|Retry saved analysis/}).count(), 0)
  await page.getByRole('heading', {name: 'CV comparison unavailable', exact: true}).waitFor()
  await page.getByText('View validation details', {exact: true}).click()
  await page.getByText('Saved CV quotation does not match.', {exact: false}).waitFor()
  await screenshot('claim-validation-1440')
  savedInsights[0] = {run: featureRun('claims-complete', 'cv_claim_review', 'completed'), reviews: [], result: {warnings: [], selection_note: 'Selected role-relevant claims from the saved CV.', claims: [{id: 'claim-1', claim: 'Built backend services.', status: 'discussed_not_substantiated', explanation: 'The answer described the project but did not establish its deployment scope.', scope_comparison: 'Same project; deployment detail not established.', follow_up: 'Ask which parts the candidate implemented.', cv_evidence: {quote: 'Built backend services.', source_id: 'pdf-page-1'}, interview_evidence: [{turn_id: 'a1', quote: turns[3].text, speaker_role: 'candidate', session_start_ms: 1000}]}]}}
  await page.reload()
  await nav().getByRole('button', {name: 'CV claims', exact: true}).click()
  await page.locator('.pi-claims-table').waitFor()
  await page.locator('.pi-claims-table .ca-citation').click()
  await page.getByText('Ask which parts the candidate implemented.', {exact: false}).waitFor()
  await page.locator('.pi-row-detail summary').filter({hasText: 'Recruiter review'}).click()
  await page.getByRole('textbox', {name: 'Clarification', exact: true}).fill('Follow up on implementation scope.')
  assert.equal(await page.getByRole('button', {name: 'Add clarification', exact: true}).isEnabled(), true)
  await screenshot('claims-1440')
  await page.setViewportSize({width: 390, height: 844})
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
  await screenshot('claims-mobile')
  await page.setViewportSize({width: 1440, height: 900})
  await nav().getByRole('button', {name: 'Interview quality', exact: true}).click()
  await page.getByRole('heading', {name: 'Observations & next steps', exact: true}).waitFor()
  assert.equal(await page.locator('.pi-quality-table .pi-row-detail').count(), 0)
  assert.match(await page.locator('.pi-quality-table .ca-row-trigger').innerText(), /question Q2/)
  await page.locator('.pi-quality-table .ca-citation').click()
  await page.getByRole('button', {name: /Q2 · Difference between Python list and tuple/}).click()
  assert.equal(await nav().getByRole('button', {name: /Interview Q&A/}).getAttribute('aria-current'), 'page')
  assert.equal(await page.locator('.ca-question-detail').count(), 1)
  await page.waitForFunction(() => document.activeElement?.classList.contains('ca-question-detail'))
  await nav().getByRole('button', {name: 'Interview quality', exact: true}).click()
  assert.equal(await page.locator('.pi-quality-table .pi-row-detail').count(), 1)
  await page.locator('.pi-quality-table .ca-citation').click()
  await page.getByText('Authored synthetic timing', {exact: true}).click()
  await page.getByText('Authored test intervals, not recorded speech or microphone measurements.', {exact: true}).waitFor()
  await screenshot('quality-1440')
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await page.getByRole('button', { name: 'Report & export', exact: true }).click()
  await page.getByRole('dialog', { name: 'Report & export', exact: true }).waitFor()
  assert.equal(await page.locator('.ca-modal').evaluate(element => getComputedStyle(element).animationName), 'none')
  await page.keyboard.press('Escape')
  const previousEntries = await page.evaluate(() => window.__assessmentEntries.length)
  await nav().getByRole('button', { name: /Interview Q&A/ }).click()
  await page.waitForFunction(count => window.__assessmentEntries.length > count, previousEntries)
  const reducedEntry = await page.evaluate(() => window.__assessmentEntries.at(-1))
  assert.equal(reducedEntry.options.duration, 40)
  assert.equal(reducedEntry.keyframes.some(frame => 'transform' in frame), false)
  assert.deepEqual(errors, [])
  console.log('PASS: 8 tabs, expanded evidence/Q&A, source navigation, history, score semantics, review gating, combined analysis request, report modal/preview/download/print, shared tab/modal motion, reduced-motion support and responsive layouts; no browser errors.')
} finally { await browser.close() }
