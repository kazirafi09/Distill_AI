// Offline browser acceptance: fixture APIs only, no model calls or database writes.
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { mkdir, readFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import { extname, resolve, sep } from 'node:path'
const {chromium} = createRequire(import.meta.url)('playwright')
const dist = fileURLToPath(new URL('../dist/', import.meta.url))
const output = fileURLToPath(new URL('../../output/assessment-comparison/', import.meta.url))
await mkdir(output, {recursive: true})
const run = (id, name) => ({id: `f6-${id}`, application_id: id, job_id: 'job', kind: 'f6', label: name, synthetic: false,
  parent_run_id: `f5-${id}`, status: 'completed', stage: 'completed', created_at: '2026-10-22T03:00:00Z', updated_at: '2026-10-22T03:00:00Z',
  profile_version: 1, prompt_version: 'f6-prompt-1', rubric_version: 'f6-1', attempt: 1, max_attempts: 3, completed_groups: 4, total_groups: 4})
const a = run('a', 'Amina Sample'), b = run('b', 'Samir Sample')
const candidate = run => ({id: run.application_id, name: run.label, assessment: {...run, outdated: false}, ready: true, transcript_status: 'completed', interview_status: 'completed'})
const candidates = [candidate(a), {id: 'pending', name: 'Pending Candidate', ready: false}, {...candidate(run('demo', 'Demo Candidate')), synthetic: true, assessment: {...run('demo', 'Demo Candidate'), synthetic: true}}]
const dimensions = ['technical_competency', 'communication', 'response_relevance', 'problem_solving', 'experience_depth', 'job_alignment']
function result(run) {
  const criterion = {id: 'python', text: run.application_id === 'b' ? 'Advanced Python' : 'Python'}
  const source = {id: 'iv-1', kind: 'interview', criterion_ids: ['python'], dimension: 'accuracy', justification: 'Saved explanation.', evidence: [{turn_id: 'answer', quote: 'A tuple is immutable.'}]}
  const finding = {criterion_id: 'python', text: 'Verify concurrency with an equivalent exercise.', source_ids: ['iv-1']}
  return {run, outdated: false, criteria: [criterion], answers: [], source_turns: [{id: 'answer', speaker_role: 'candidate', text: `Original saved answer for ${run.label}: a tuple is immutable.`, session_start_ms: null}],
    final_score: {score_100: run.application_id === 'a' ? '0' : null, status: run.application_id === 'a' ? 'provisional' : 'incomplete',
      cv_score_100: '0', interview_score_100: '0', interview_run_id: `performance-${run.application_id}`, policy: {version: 'final-1', cv_weight: 30, interview_weight: 70}, warnings: []},
    result: {warnings: [], findings: {strengths: [], weaknesses: [], evidence_gaps: [finding], recommended_areas: [finding], suitability: {position: 'mixed_evidence', rationale: 'Review original candidate evidence.', source_ids: ['iv-1']}},
      component_scores: {cv: {aggregate: '0', dimensions: [{dimension: 'technical_skills', score: '0', approved_percentage: '100'}], note: 'Documented credit.'}, interview: {}},
      source_registry: [source], provenance: {f5_run_id: run.parent_run_id, cv_assessment_id: 'cv', repairs_used: 0}, coverage: [{criterion_id: 'python', group_ids: [], exploration: 'unasked'}]}}
}
let failResult = false, failPerformance = false, badJob = false
const requests = [], errors = []
const browser = await chromium.launch({headless: true, ...(process.env.UI_TEST_BROWSER ? {executablePath: process.env.UI_TEST_BROWSER} : {channel: 'chrome'})})
const page = await browser.newPage({viewport: {width: 1440, height: 1000}})
page.on('pageerror', error => errors.push(error.message))
await page.route('**/*', async route => {
  const pathname = new URL(route.request().url()).pathname
  const file = pathname.startsWith('/recruiter/') ? resolve(dist, 'index.html') : resolve(dist, `.${pathname}`)
  if (!file.startsWith(dist.endsWith(sep) ? dist : dist + sep)) return route.abort()
  try { await route.fulfill({body: await readFile(file), contentType: {'.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml'}[extname(file)] ?? 'application/octet-stream'}) }
  catch { await route.fulfill({status: 404, body: 'Not found'}) }
})
await page.route('**/api/**', async route => {
  requests.push({method: route.request().method(), url: route.request().url()})
  const url = new URL(route.request().url()), path = url.pathname.replace(/^\/api/, '')
  let data = []
  if (path === '/auth/refresh') data = {access_token: 'fixture', token_type: 'bearer'}
  else if (path === '/auth/me') data = {id: 'user', email: 'sample@example.com', role: 'recruiter'}
  else if (path === '/jd/descriptions') data = {items: [{id: 'job', number: 207, title: 'Backend Engineer'}]}
  else if (path === '/assessments/jobs/job') data = {candidates: url.searchParams.get('offset') === '5' ? [candidate(b)] : candidates,
    runs: [a, b], offset: Number(url.searchParams.get('offset') ?? 0), page_size: 5, has_more_candidates: url.searchParams.get('offset') !== '5', profile_ready: true, profile_version: 1}
  else if (path.endsWith('/scoring-settings')) data = {revision: 0, job: {title: 'Backend Engineer', requirements: 'Python'}, policy: {weights: Object.fromEntries(dimensions.map((name, index) => [name, index ? 15 : 25])), minimum_assessed_dimensions: 4, essential_dimensions: ['technical_competency']}}
  else if (path.endsWith('/result')) {
    if (failResult) return route.fulfill({status: 503, json: {detail: 'Saved assessment temporarily unavailable.'}})
    data = result(path.includes('f6-a') ? a : b)
    if (badJob) data.run = {...data.run, job_id: 'other'}
  } else if (path.endsWith('/post-interview')) {
    if (failPerformance) return route.fulfill({status: 503, json: {detail: 'Performance temporarily unavailable.'}})
    const original = path.includes('f5-a') ? a : b
    data = {items: [{run: {...original, id: `performance-${original.application_id}`, kind: 'interview_performance'},
      result: {dimensions: Object.fromEntries(dimensions.map(name => [name, {score: 0, justification: 'An assessed zero.', evidence: [{turn_id: 'answer', quote: 'Saved answer.'}]}])),
        scoring_settings: {revision: original === a ? 1 : 2, policy_sha256: original === a ? 'policy-a' : 'policy-b', rubric_version: 'performance-1', policy: {weights: {technical_competency: 25}}}, warnings: []}}]}
  }
  await route.fulfill({json: data})
})
const compare = () => page.getByRole('button', {name: 'Compare selected', exact: true})
const modal = () => page.getByRole('dialog', {name: 'Compare candidates', exact: true})
try {
  await page.goto('http://comparison.test/recruiter/assessments?job=job')
  await page.getByRole('checkbox', {name: 'Compare Amina Sample', exact: true}).waitFor()
  assert.equal(await compare().isDisabled(), true)
  assert.equal(await page.getByRole('checkbox', {name: 'Compare Pending Candidate', exact: true}).isDisabled(), true)
  await page.getByRole('checkbox', {name: 'Compare Amina Sample', exact: true}).check()
  assert.equal(await page.getByRole('checkbox', {name: 'Compare Demo Candidate', exact: true}).isDisabled(), true)
  await page.getByRole('button', {name: 'Next', exact: true}).click()
  await page.getByRole('checkbox', {name: 'Compare Samir Sample', exact: true}).check()
  assert.equal(await page.getByRole('list', {name: 'Selected candidates'}).getByRole('listitem').count(), 2)
  await compare().click()
  await modal().getByText('Interview scoring revisions or policies differ.', {exact: false}).waitFor()
  assert.match(await modal().innerText(), /Criteria versions differ/)
  const combined = modal().getByRole('row', {name: /Combined score \/ 100/})
  assert.equal(await combined.locator('td strong').allTextContents().then(values => values.join('|')), '0|—')
  assert.equal(await modal().getByText('Not in this assessment’s criteria.', {exact: true}).count(), 2)
  await modal().getByText('Verify next', {exact: true}).waitFor()
  await modal().locator('details').filter({has: page.locator('summary', {hasText: /^Evidence$/})}).first().locator('summary').click()
  await modal().getByRole('button', {name: /INTERVIEW.*View source/}).first().click()
  const source = modal().getByRole('region', {name: 'Comparison source transcript'})
  assert.match(await source.innerText(), /Original saved answer for Amina Sample/)
  await source.getByRole('button', {name: 'Back to comparison'}).click()
  await modal().locator('.comparison-table-scroll').evaluate(element => {element.scrollTop = 0})
  await page.screenshot({path: resolve(output, 'comparison-desktop.png'), animations: 'disabled'})
  await page.keyboard.press('Escape')
  assert.equal(await compare().evaluate(element => element === document.activeElement), true)
  await page.setViewportSize({width: 390, height: 844})
  await compare().click()
  await modal().getByRole('table').waitFor()
  assert.equal(await modal().evaluate(element => Math.round(element.getBoundingClientRect().width)), 390)
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true)
  assert.equal(await modal().locator('.comparison-table-scroll').evaluate(element => element.scrollWidth > element.clientWidth), true)
  await modal().getByRole('link', {name: 'Open saved assessment ↗'}).last().focus()
  await page.keyboard.press('Tab')
  assert.equal(await page.evaluate(() => !!document.activeElement.closest('[role="dialog"]')), true)
  await page.screenshot({path: resolve(output, 'comparison-mobile.png'), animations: 'disabled'})
  await page.keyboard.press('Escape')
  failResult = true
  await compare().click()
  await modal().getByRole('heading', {name: 'Comparison unavailable'}).waitFor()
  failResult = false
  await modal().getByRole('button', {name: 'Try again'}).click()
  await modal().getByRole('table').waitFor()
  await page.keyboard.press('Escape')
  badJob = true
  await compare().click()
  await modal().getByRole('heading', {name: 'Comparison unavailable'}).waitFor()
  assert.equal(await modal().getByRole('table').count(), 0)
  await page.keyboard.press('Escape')
  badJob = false; failPerformance = true
  await compare().click()
  await modal().getByRole('table').waitFor()
  assert.match(await modal().innerText(), /compatibility could not be established/)
  assert.match(await modal().innerText(), /details could not be loaded/)
  await page.keyboard.press('Escape')
  await page.getByRole('button', {name: 'Remove Amina Sample from comparison'}).click()
  assert.equal(await compare().isDisabled(), true)
  assert.deepEqual(errors, [])
  assert.equal(requests.filter(item => item.method !== 'GET' && !item.url.includes('/auth/refresh')).length, 0)
  console.log('PASS: cross-page selection, completed F6 eligibility, sample separation, exact result loading, criteria/policy warnings, zero/null scores, evidence, retry, job mismatch, performance failure, mobile scrolling and keyboard focus; no model calls or browser errors.')
} finally {await browser.close()}
