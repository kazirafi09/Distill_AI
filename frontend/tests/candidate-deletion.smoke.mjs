// Offline API fixtures: no real candidate writes, provider calls or email sends.
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { mkdir } from 'node:fs/promises'
const { chromium } = createRequire(import.meta.url)('playwright')
const output = new URL('../../output/candidate-deletion/', import.meta.url).pathname.replace(/^\/(\w:)/, '$1')
await mkdir(output, { recursive: true })
const browser = await chromium.launch({ headless: true, channel: 'chrome' })
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' })
const errors = []
page.on('pageerror', error => errors.push(error.message))
let applications = [{ id: 'application', candidate_id: 'candidate', full_name: 'Amina Sample', email: 'amina@example.invalid',
  created_at: '2026-10-26T12:00:00Z', submitted_at_text: null, cv_documents: [] }]
let requests = 0, fail = true, release
await page.route('**/api/**', async route => {
  const path = new URL(route.request().url()).pathname.replace(/^\/api/, '')
  let data = []
  if (path === '/auth/refresh') data = { access_token: 'fixture', token_type: 'bearer' }
  else if (path === '/auth/me') data = { id: 'fixture', email: 'recruiter@example.invalid', role: 'recruiter' }
  else if (path === '/health/db') data = { status: 'ok', database: 'connected' }
  else if (path === '/jd/descriptions') data = { items: [{ id: 'job', number: 219, title: 'Python Developer' }] }
  else if (path === '/cv/sources') data = [{ id: 'source', job_description_id: 'job', drive_folder_id: 'folder', spreadsheet_id: 'sheet', sheet_tab_id: 0 }]
  else if (path === '/cv/applications') data = applications
  else if (path.endsWith('/results')) data = { run: null, profile: null, ranked: [], incomplete: [], history: [], warnings: [] }
  else if (path.endsWith('/delete')) {
    requests++
    assert.equal(route.request().method(), 'POST')
    assert.deepEqual(route.request().postDataJSON(), { confirmed: true })
    assert.equal(path, '/cv/jobs/job/applications/application/delete')
    if (fail) return route.fulfill({ status: 409, json: { detail: 'An import is active. Try again shortly.' } })
    await new Promise(resolve => { release = resolve })
    applications = []
    data = { deleted: true, cleanup_pending: true }
  }
  await route.fulfill({ json: data })
})
try {
  await page.goto('http://localhost:5173/recruiter/cv-screening?job=job')
  const button = page.getByRole('button', { name: 'Delete candidate', exact: true })
  await button.click()
  const modal = page.getByRole('dialog', { name: 'Delete candidate?' })
  await modal.waitFor()
  const confirm = modal.getByRole('button', { name: 'Permanently delete', exact: true })
  assert.equal(await confirm.isDisabled(), true)
  await page.keyboard.press('Escape')
  await modal.waitFor({ state: 'hidden' })
  assert.equal(requests, 0)
  assert.equal(await button.evaluate(element => element === document.activeElement), true)
  await button.click()
  await modal.waitFor()
  await page.waitForFunction(() => document.querySelector('.screening-delete-dialog')?.open)
  await page.screenshot({ path: `${output}/desktop.png`, fullPage: true })
  await page.setViewportSize({ width: 390, height: 844 })
  await page.screenshot({ path: `${output}/mobile.png`, fullPage: true })
  assert.equal(await modal.evaluate(element => element.scrollWidth > element.clientWidth), false)
  await modal.getByRole('checkbox').check()
  await confirm.click()
  await modal.getByRole('alert').waitFor()
  assert.match(await modal.getByRole('alert').innerText(), /import is active/)
  assert.equal(await modal.isVisible(), true)
  fail = false
  await confirm.click()
  await page.getByRole('button', { name: 'Deleting…' }).waitFor()
  await page.keyboard.press('Escape')
  assert.equal(await modal.isVisible(), true)
  assert.equal(await modal.getByRole('button', { name: 'Keep candidate' }).isDisabled(), true)
  release()
  await page.getByRole('status').filter({ hasText: 'cleanup is pending' }).waitFor()
  await page.getByRole('heading', { name: 'No applications yet' }).waitFor()
  assert.equal(requests, 2)
  assert.deepEqual(errors, [])
  console.log('Candidate deletion browser checks passed: confirmation, cancel/focus, mobile, conflict, guarded send, pending cleanup and refreshed list.')
} finally { await browser.close() }
