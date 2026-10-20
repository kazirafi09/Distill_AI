// Run after npm run build with Playwright available on NODE_PATH. All requests are mocked.
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { mkdir, readFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import { extname, resolve, sep } from 'node:path'
const { chromium } = createRequire(import.meta.url)('playwright')
const base = 'http://ui-redesign.test'
const output = fileURLToPath(new URL('../../output/ui-redesign/', import.meta.url))
const dist = fileURLToPath(new URL('../dist/', import.meta.url))
await mkdir(output, { recursive: true })

const criteria = [
  { id: 'python', text: 'Python, 5+ years', dimension: 'technical_skills', priority: 'Must have' },
  { id: 'llm', text: 'Production experience with LLM applications', dimension: 'work_experience', priority: 'Must have' },
  { id: 'api', text: 'Designing and testing REST APIs', dimension: 'technical_skills', priority: 'Must have' },
  { id: 'graph', text: 'LangGraph or similar', dimension: 'additional_criteria', priority: 'Preferred' },
]
const profile = { criteria, dimension_weights: { technical_skills: 40, work_experience: 35, qualifications: 10, additional_criteria: 15 }, weights_confirmed: true }
const claim = value => ({ value, evidence: [{ source_id: 'pdf-page-1', quote: value }] })
const cvProfile = {
  contact: { name: claim('Amina Chowdhury'), email: claim('amina.cv@example.com'), phone: claim('+880 1700 000000'), address: claim('Dhaka, Bangladesh'), links: [claim('https://github.com/example')] },
  skills: ['Python', 'FastAPI', 'SQL', 'PostgreSQL', 'LangGraph', 'Git', 'Docker', 'REST APIs', 'Pytest', 'Redis', 'JavaScript', 'HTML', 'CSS', 'Linux'].map(claim),
  work_history: [{ title: claim('Backend Engineer'), employer: claim('Example Software Ltd'), dates: claim('2019 – 2025'), stated_duration: null, responsibilities: [claim('Built Python and FastAPI services for payments.'), claim('Maintained automated API tests.')] }],
  education: [{ qualification: claim('BSc'), field: claim('Computer Science'), institution: claim('Example University'), dates: claim('2015 – 2019'), grade: null }],
  projects: [{ name: claim('Question-answering assistant'), description: claim('Built a prototype question-answering bot using GPT models.'), dates: null, technologies: [claim('Python'), claim('FastAPI')] }],
  certifications: [],
}
const content = '# Senior AI Engineer\n\n## About the role\nWe are looking for a Senior AI Engineer to design, ship and maintain generative AI features for our recruitment products. You will work with product and backend engineers from first prototype to production.\n\n## What you will do\n- Build retrieval-augmented and agentic workflows with Python.\n- Evaluate model quality and keep results reproducible.\n- Expose AI features through reliable APIs.\n- Mentor engineers and review designs.\n\n## Essential requirements\n- 5+ years of professional software engineering, most of it in Python.\n- Production experience with LLM-based applications.\n- Experience designing and testing REST APIs.\n\n## Nice to have\n- Experience with LangGraph or similar orchestration frameworks.'
const jobs = [{ id: 'job-1', number: 87, title: 'Senior AI Engineer', conversation_id: 'jd-1', created_at: '2026-10-06T10:00:00Z' }, { id: 'job-2', number: 82, title: 'Junior PHP Laravel Developer', conversation_id: 'jd-2', created_at: '2026-10-02T10:00:00Z' }]
const applications = ['Amina Chowdhury', 'Tanvir Ahmed', 'Nusrat Jahan', 'Imran Hossain', 'Sadia Rahman', 'Farhan Kabir', 'Maliha Sultana', ...Array.from({ length: 10 }, (_, i) => `Candidate ${i + 8}`)].map((name, i) => ({ id: `app-${i}`, candidate_id: `person-${i}`, full_name: name, email: `${name.split(' ')[0].toLowerCase()}@example.com`, submitted_at_text: null, created_at: '2026-10-08T10:00:00Z', cv_documents: [{ id: `cv-${i}`, drive_file_id: `drive-${i}`, original_filename: `${name} - Kazi Rafi.pdf`, file_version_id: `file-${i}`, download_status: 'downloaded' }] }))
const assessments = applications.slice(0, 6).map((application, i) => ({ id: `assessment-${i}`, application_id: application.id, name: application.full_name, status: i === 5 ? 'failed' : 'completed', outdated: false, score: i === 5 ? null : { aggregate: String(82 - i * 6), evidence_coverage: '.75', must_have_review_flags: i === 1 ? [] : ['llm'], dimensions: [{ dimension: 'technical_skills', score: String(90 - i * 6), approved_percentage: '40', aggregate_contribution: String(36 - i * 2.4), justifications: [] }, { dimension: 'work_experience', score: String(80 - i * 6), approved_percentage: '35', aggregate_contribution: String(28 - i * 2.1), justifications: [] }, { dimension: 'qualifications', score: String(100 - i * 6), approved_percentage: '10', aggregate_contribution: String(10 - i * .6), justifications: [] }, { dimension: 'additional_criteria', score: String((8 - i * .9) / .15), approved_percentage: '15', aggregate_contribution: String(8 - i * .9), justifications: [] }], contributions: [] }, matches: i === 5 ? [] : criteria.map(criterion => ({ requirement_id: criterion.id, decision: criterion.id === 'llm' && i !== 1 ? 'partially_met' : criterion.id === 'graph' ? 'insufficient_evidence' : 'met', justification: criterion.id === 'llm' ? 'The CV describes an LLM prototype. It does not say whether it was used in production or by real users.' : 'The CV describes relevant roles and projects.', evidence: criterion.id === 'graph' ? [] : [{ quote: criterion.id === 'llm' ? 'Built a prototype question-answering bot using GPT models.' : 'Backend Engineer, 2019 to 2025: Python and FastAPI services for payments.', source_id: 'pdf-page-2', file_version_id: `file-${i}`, ocr_derived: false }] })), warnings: [], error_code: i === 5 ? 'CV_TEXT_NEEDS_REVIEW' : null }))
const results = { run: { id: 'run-1', status: 'completed_with_issues', total: 17, completed: 5, finished: 6, error_code: null, profile_version: 2, rubric_version: '1' }, profile, ranked: assessments.slice(0, 5), incomplete: [assessments[5]], history: [], warnings: [] }
const approved = id => ({ conversation_id: id === 'job-2' ? 'chat-2' : 'chat-1', created_at: jobs[0].created_at, checkpoint_id: 'checkpoint-1', mode: 'chat', messages: [{ id: 'm-1', role: 'assistant', content: 'I can help you review the requirements for this job.' }], error_code: null, jd_conversation: { conversation_id: 'jd-1', status: 'approved', messages: [], draft_jd: content, approved_jd: content, revision_count: 1, checkpoint_id: 'jd-checkpoint', screening_profile: profile, profile_version: 2, acceptance_errors: [], error_code: null, interrupt: null, job_description: jobs.find(job => job.id === id) ?? jobs[0] } })
const draft = { ...approved('job-1'), conversation_id: 'chat-draft', mode: 'jd_workflow', messages: [], jd_conversation: { ...approved('job-1').jd_conversation, status: 'awaiting_review', approved_jd: null, job_description: null, interrupt: { id: 'review-1', payload: { allowed_actions: ['revise', 'approve', 'cancel'] } } } }
let failResults = false, emptyApplications = false, fileReadFailure = false, parseAttempts = 0
const browser = await chromium.launch({ headless: true, ...(process.env.UI_TEST_BROWSER ? { executablePath: process.env.UI_TEST_BROWSER } : { channel: 'chrome' }) })
const page = await browser.newPage({ viewport: { width: 1280, height: 600 } })
const errors = []
page.on('pageerror', error => errors.push(error.message))
// Serve the compiled frontend from disk; the test never contacts a network or a backend.
await page.route('**/*', async route => {
  const pathname = new URL(route.request().url()).pathname
  const file = pathname.startsWith('/recruiter/') ? resolve(dist, 'index.html') : resolve(dist, `.${pathname}`)
  if (!file.startsWith(dist.endsWith(sep) ? dist : dist + sep)) return route.abort()
  try {
    const contentType = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.ttf': 'font/ttf', '.svg': 'image/svg+xml', '.png': 'image/png' }[extname(file)] ?? 'application/octet-stream'
    await route.fulfill({ status: 200, body: await readFile(file), contentType })
  } catch { await route.fulfill({ status: 404, body: 'Not found' }) }
})
await page.route('**/api/**', async route => {
  const path = new URL(route.request().url()).pathname.replace(/^\/api/, '')
  let data
  if (path === '/auth/refresh') data = { access_token: 'fixture-token', token_type: 'bearer' }
  else if (path === '/auth/me') data = { id: 'qa-user', email: 'recruiter@example.com', role: 'recruiter' }
  else if (path === '/health/db') data = { status: 'ok', database: 'connected' }
  else if (path === '/cv/source-checks') data = []
  else if (path === '/jd/descriptions') data = { items: jobs, limit: 10, offset: 0 }
  else if (path.startsWith('/jd/descriptions/')) data = { ...(jobs.find(job => job.id === path.split('/').at(-1)) ?? jobs[0]), content, screening_profile: profile, profile_version: 2, google_form_url: null }
  else if (path === '/cv/sources') data = jobs.map(job => ({ id: `source-${job.id}`, job_description_id: job.id, drive_folder_id: 'folder-1', spreadsheet_id: 'sheet-1', sheet_tab_id: 0 }))
  else if (path.endsWith('/applications')) data = emptyApplications ? [] : applications
  else if (path.endsWith('/results')) {
    if (failResults) return route.fulfill({ status: 503, json: { detail: 'Screening service unavailable.' } })
    data = results
  }
  else if (path === '/assistant/conversations' && route.request().method() === 'GET') data = { items: [{ conversation_id: 'chat-1', title: 'Senior AI Engineer requirements', created_at: jobs[0].created_at }, { conversation_id: 'chat-2', title: 'Interview-friendly wording', created_at: jobs[0].created_at }], limit: 20, offset: 0 }
  else if (path === '/assistant/conversations') data = { ...approved('job-1'), conversation_id: 'chat-new', jd_conversation: null, messages: [] }
  else if (path.endsWith('/messages')) data = draft
  else if (path.endsWith('/jd-actions')) {
    const saved = { ...jobs[0], id: 'job-3', number: 88, conversation_id: 'jd-3' }
    jobs.unshift(saved)
    data = { ...draft, jd_conversation: { ...draft.jd_conversation, status: 'approved', interrupt: null, job_description: saved } }
  }
  else if (path.includes('/assistant/job-descriptions/')) data = approved(path.split('/')[3])
  else if (path.startsWith('/assistant/conversations/')) data = approved(path.endsWith('chat-2') ? 'job-2' : 'job-1')
  else if (path.endsWith('/parse')) { parseAttempts++; fileReadFailure = false; data = { status: 'completed', profile: cvProfile, warnings: [], evidence: [], ocr_used: false } }
  else if (path.endsWith('/profile') && fileReadFailure) data = { status: 'failed', error_code: 'ENCRYPTED_PDF', profile: null, warnings: [], evidence: [], ocr_used: false }
  else if (path.endsWith('/profile')) data = { status: 'completed', ocr_used: false, warnings: ['CV_SHEET_EMAIL_DISCREPANCY', 'UNSUPPORTED_PROFILE_CLAIMS_OMITTED'], profile: cvProfile, evidence: [{ claim_path: 'work_history.0.title', quote: 'Backend Engineer, 2019 to 2025: Python and FastAPI services for payments.', source_id: 'pdf-page-1', ocr_derived: false }] }
  else if (path.includes('/references')) data = []
  else return route.fulfill({ status: 404, json: { detail: `Unmocked QA endpoint: ${path}` } })
  return route.fulfill({ status: 200, json: data })
})
async function screenshot(name) {
  await page.screenshot({ path: `${output}/${name}.png`, fullPage: true })
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, `${name}: horizontal overflow`)
}
try {
  await page.goto(`${base}/recruiter/job-descriptions`)
  await page.getByRole('heading', { name: 'Senior AI Engineer', exact: true, level: 2 }).waitFor()
  await screenshot('job-descriptions-desktop')
  await page.getByRole('navigation', { name: 'Job description sections' }).getByRole('button', { name: 'Screening criteria' }).click()
  await page.getByRole('table').waitFor()
  await screenshot('criteria-desktop')
  await page.getByRole('navigation', { name: 'Job description sections' }).getByRole('button', { name: 'AI assistant' }).click()
  await page.getByRole('complementary', { name: 'Conversations' }).waitFor()
  await screenshot('assistant-desktop')
  const input = page.getByRole('textbox', { name: 'Chat message', exact: true })
  await input.fill('Unsent message for the first job')
  await page.getByLabel('Job for assistant').selectOption('job-2')
  await page.getByText('I can help you review the requirements for this job.').waitFor()
  assert.equal(await input.inputValue(), '')
  await page.getByLabel('Job for assistant').selectOption('job-1')
  await page.waitForFunction(() => document.querySelector('textarea')?.value === 'Unsent message for the first job')
  await page.getByRole('button', { name: 'New job description', exact: true }).click()
  await input.fill('Draft a senior AI engineer role')
  await page.getByRole('button', { name: 'Send', exact: true }).click()
  await page.getByRole('navigation', { name: 'Job description sections' }).getByRole('button', { name: 'Screening criteria' }).click()
  await page.getByText('Before you can approve', { exact: true }).waitFor()
  assert.equal(await page.getByRole('button', { name: 'Approve and save', exact: true }).isDisabled(), true)
  for (const checkbox of await page.locator('.jd-review-check input').all()) await checkbox.check()
  assert.equal(await page.getByRole('button', { name: 'Approve and save', exact: true }).isEnabled(), true)
  await screenshot('draft-review-desktop')
  await page.getByRole('button', { name: 'Approve and save', exact: true }).click()
  await page.waitForFunction(() => document.querySelector('select[aria-label="Job for assistant"]')?.value === 'job-3')
  await page.getByRole('navigation', { name: 'Job description sections' }).getByRole('button', { name: 'Description', exact: true }).click()
  await page.getByRole('link', { name: 'Go to CV Screening' }).waitFor()

  await page.goto(`${base}/recruiter/cv-screening?job=job-1`)
  await page.locator('.screening-candidate-row').first().waitFor()
  await screenshot('screening-desktop')
  await page.getByRole('navigation', { name: 'Candidate sections' }).getByRole('button', { name: 'Profile', exact: true }).click()
  await page.getByRole('heading', { name: 'Contact details', exact: true }).waitFor()
  assert.equal(await page.locator('.profile-tags').first().locator('li').count(), 14)
  assert.equal(await page.locator('.profile-document .cv-parsing').count(), 0)
  assert.equal(await page.locator('.profile-document-header').count(), 0)
  await screenshot('candidate-profile-desktop')
  await page.getByRole('navigation', { name: 'Profile sections' }).getByRole('button', { name: 'Experience', exact: false }).click()
  await page.locator('.profile-evidence summary').click()
  await page.getByText('“Backend Engineer, 2019 to 2025: Python and FastAPI services for payments.”', { exact: true }).waitFor()
  assert.equal(await page.locator('.candidate-profile').evaluate(element => [element, ...element.querySelectorAll('section, details')].some(item => ['auto', 'scroll'].includes(getComputedStyle(item).overflowY))), false)
  await screenshot('candidate-profile-experience')
  await page.getByRole('navigation', { name: 'Candidate sections' }).getByRole('button', { name: 'Score details', exact: true }).click()
  await page.getByRole('heading', { name: 'How each category contributes' }).waitFor()
  await page.getByText('Adds 36 points', { exact: true }).waitFor()
  await screenshot('candidate-score-details')
  await page.getByRole('navigation', { name: 'Candidate sections' }).getByRole('button', { name: 'CV files', exact: true }).click()
  await page.locator('.cv-file-status-label').filter({ hasText: 'CV read successfully' }).waitFor()
  assert.equal(await page.getByRole('button', { name: 'Check saved parse', exact: true }).count(), 0)
  assert.equal(await page.getByRole('button', { name: 'Retry reading CV', exact: true }).count(), 0)
  await page.getByRole('heading', { name: 'Amina Chowdhury — CV.pdf', exact: true }).waitFor()
  assert.equal(await page.locator('.cv-file-metadata').evaluate(element => element.open), false)
  await screenshot('candidate-cv-files')
  await page.locator('.cv-file-metadata summary').click()
  await page.getByText('Amina Chowdhury - Kazi Rafi.pdf', { exact: true }).waitFor()
  assert.equal(parseAttempts, 0)
  fileReadFailure = true
  await page.getByRole('navigation', { name: 'Candidate sections' }).getByRole('button', { name: 'Profile', exact: true }).click()
  await page.getByRole('navigation', { name: 'Candidate sections' }).getByRole('button', { name: 'CV files', exact: true }).click()
  await page.locator('.cv-file-status-label').filter({ hasText: 'CV could not be read' }).waitFor()
  await page.getByRole('button', { name: 'Retry reading CV', exact: true }).click()
  await page.locator('.cv-file-status-label').filter({ hasText: 'CV read successfully' }).waitFor()
  assert.equal(parseAttempts, 1)
  await page.getByRole('navigation', { name: 'Candidate sections' }).getByRole('button', { name: 'Requirements', exact: true }).click()
  await page.locator('.has-review button').click()
  await page.waitForFunction(() => document.querySelectorAll('.screening-candidate-row').length === 4)
  await page.locator('.has-issues button').click()
  await page.waitForFunction(() => document.querySelectorAll('.screening-candidate-row').length === 1 && document.querySelector('.screening-candidate-row').textContent.includes('Farhan'))
  await page.getByRole('combobox', { name: 'Show', exact: true }).selectOption('all')
  await page.waitForFunction(() => document.querySelectorAll('.screening-candidate-row').length === 17)
  await page.getByRole('searchbox', { name: 'Search candidates' }).fill('Amina')
  assert.equal(await page.locator('.screening-candidate-row').count(), 1)
  await page.getByRole('checkbox', { name: 'Only those with essential criteria to review' }).click()
  await page.waitForURL(/review=1/)
  await page.waitForFunction(() => document.querySelector('.screening-review-filter input')?.checked)
  await page.locator('.screening-candidate-row').first().click()
  await page.setViewportSize({ width: 390, height: 844 })
  await screenshot('candidate-mobile')
  await page.getByRole('navigation', { name: 'Candidate sections' }).getByRole('button', { name: 'Profile', exact: true }).click()
  await page.getByRole('heading', { name: 'Contact details', exact: true }).waitFor()
  await screenshot('candidate-profile-mobile')
  await page.getByRole('button', { name: 'Back to candidates', exact: false }).click()
  assert.equal(await page.getByRole('searchbox', { name: 'Search candidates' }).inputValue(), 'Amina')
  assert.equal(await page.getByRole('checkbox', { name: 'Only those with essential criteria to review' }).isChecked(), true)
  await screenshot('candidates-mobile')
  await page.getByRole('searchbox', { name: 'Search candidates' }).fill('Nobody matches')
  await page.getByRole('heading', { name: 'No candidates match these filters' }).waitFor()
  await page.getByRole('button', { name: 'Clear filters' }).click()
  await page.setViewportSize({ width: 1280, height: 600 })
  await page.getByRole('button', { name: 'Source setup', exact: true }).first().click()
  await page.getByRole('dialog', { name: 'Application source setup' }).waitFor()
  await page.getByRole('button', { name: 'Close', exact: true }).filter({ visible: true }).click()
  emptyApplications = true
  await page.reload()
  await page.getByRole('heading', { name: 'No applications yet' }).waitFor()
  await screenshot('screening-empty')
  emptyApplications = false; failResults = true
  await page.reload()
  await page.getByRole('alert').filter({ hasText: 'Some results could not be loaded' }).waitFor()
  await screenshot('screening-failure')
  failResults = false
  await page.getByRole('button', { name: 'Try again', exact: true }).click()
  await page.locator('.screening-candidate-row').first().waitFor()
  applications[0].cv_documents.push({ ...applications[0].cv_documents[0], id: 'cv-extra', file_version_id: 'file-extra', drive_file_id: 'drive-extra', original_filename: 'Another CV - Kazi Rafi.docx' })
  await page.reload()
  await page.locator('.screening-candidate-row').first().waitFor()
  await page.getByRole('navigation', { name: 'Candidate sections' }).getByRole('button', { name: 'CV files', exact: true }).click()
  await page.getByRole('button', { name: 'View candidate profile', exact: false }).nth(1).click()
  await page.getByRole('combobox', { name: 'CV document', exact: true }).waitFor()
  assert.equal(await page.getByRole('combobox', { name: 'CV document', exact: true }).inputValue(), 'cv-extra')
  assert.deepEqual(await page.getByRole('combobox', { name: 'CV document', exact: true }).locator('option').allTextContents(), ['Amina Chowdhury — CV 1.pdf', 'Amina Chowdhury — CV 2.docx'])
  assert.deepEqual(errors, [])
  console.log('UI smoke checks passed: desktop, mobile, drafts, approval, filters, profile sections, scores, file labels, exact-file selection, retry reading, source setup, empty and failure recovery.')
} finally { await browser.close() }
