// Compiled UI with fixture APIs: no emails, models or live calendar writes.
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { readFile, mkdir } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import { extname, resolve, sep } from 'node:path'
const { chromium } = createRequire(import.meta.url)('playwright')
const dist = fileURLToPath(new URL('../dist/', import.meta.url))
const output = fileURLToPath(new URL('../../output/invitations/', import.meta.url))
await mkdir(output, { recursive: true })
const browser = await chromium.launch({ headless: true, channel: 'chrome' })
const page = await browser.newPage({ viewport: { width: 1280, height: 900 }, timezoneId: 'Asia/Dhaka' })
const errors = []
page.on('pageerror', error => errors.push(error.message))
const job = { id: 'job-1', title: 'Python developer', number: 139, conversation_id: 'jd-1', created_at: '2026-10-01T00:00:00Z' }
const members = ['Ada', 'Sam'].map((name, i) => ({ application_id: `app-${i}`, name, email: `${name.toLowerCase()}@example.com`, rank: i + 1, score: { aggregate: '90' } }))
let lastRequest, confirmations = 0
const previewFor = body => ({ batch_id: 'preview-1', preview_key: 'a'.repeat(64), expires_at: '2099-01-01T00:00:00Z',
  job_title: job.title, sender: 'sender@example.com', send_count: 2, mode: body.mode, include_calendar: body.include_calendar,
  subject: 'Interview invitation | Python developer', recipients: members.map(item => ({ ...item, action: 'create', reason: 'New invitation',
    schedule: body.schedules.find(slot => slot.application_id === item.application_id), subject: 'Interview invitation | Python developer',
    body: `Dear ${item.name},\n\nWe are pleased to invite you to an online interview.\n\nYour personal interview link:\n{interview_link}\n\nBest regards,\nRecruitment Team` })) })
await page.route('**/*', async route => {
  const path = new URL(route.request().url()).pathname
  const file = path.startsWith('/recruiter/') ? resolve(dist, 'index.html') : resolve(dist, `.${path}`)
  if (!file.startsWith(dist.endsWith(sep) ? dist : dist + sep)) return route.abort()
  try {
    await route.fulfill({ body: await readFile(file), contentType: { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml' }[extname(file)] ?? 'application/octet-stream' })
  } catch { await route.fulfill({ status: 404, body: 'Not found' }) }
})
await page.route('**/api/**', async route => {
  const path = new URL(route.request().url()).pathname.replace(/^\/api/, '')
  let data
  if (path === '/auth/refresh') data = { access_token: 'fixture', token_type: 'bearer' }
  else if (path === '/auth/me') data = { id: 'fixture-user', email: 'recruiter@example.com', role: 'recruiter' }
  else if (path === '/health/db') data = { status: 'ok', database: 'connected' }
  else if (path === '/cv/source-checks') data = []
  else if (path === '/jd/descriptions') data = { items: [job], limit: 10, offset: 0 }
  else if (path.endsWith('/shortlist')) data = { id: 'shortlist', job_id: job.id, members, outdated: false, selected_count: 2 }
  else if (path.endsWith('/invitations/options')) data = { join_early_minutes: 10, recipients: members.map(item => ({ ...item, schedule: null })) }
  else if (path.endsWith('/invitations/preview')) { lastRequest = route.request().postDataJSON(); data = previewFor(lastRequest) }
  else if (path.endsWith('/invitations/confirm')) { confirmations++; data = { queued: 2, already_confirmed: false, message: 'Queued 2 individual emails. Queueing is not delivery.' } }
  else if (path.endsWith('/invitations')) data = []
  else return route.fulfill({ status: 404, json: { detail: `Unmocked route ${path}` } })
  return route.fulfill({ json: data })
})
try {
  await page.goto('http://invitation-fixture.test/recruiter/invitations?job=job-1')
  const setTimes = page.getByRole('button', { name: 'Set interview times', exact: true })
  await setTimes.waitFor()
  assert.equal(await page.getByText('Default invitation email', { exact: true }).count(), 0)
  await setTimes.click()
  const dialog = page.getByRole('dialog', { name: 'Set interview times', exact: true })
  await dialog.waitFor()
  await dialog.getByLabel('Start time for all candidates', { exact: true }).fill('2099-01-01T12:00')
  await dialog.getByLabel('End time for all candidates', { exact: true }).fill('2099-01-01T11:30')
  await dialog.getByRole('button', { name: 'Apply to all', exact: true }).click()
  await dialog.getByRole('alert').filter({ hasText: 'after its start' }).waitFor()
  await dialog.getByLabel('End time for all candidates', { exact: true }).fill('2099-01-01T12:30')
  await dialog.getByRole('button', { name: 'Apply to all', exact: true }).click()
  for (const name of ['Ada', 'Sam']) {
    assert.equal(await dialog.getByLabel(`Start time for ${name}`, { exact: true }).inputValue(), '2099-01-01T12:00')
    assert.equal(await dialog.getByLabel(`End time for ${name}`, { exact: true }).inputValue(), '2099-01-01T12:30')
  }
  for (const [index, name] of ['Ada', 'Sam'].entries()) {
    await dialog.getByLabel(`Start time for ${name}`, { exact: true }).fill(`2099-01-01T${index ? '13' : '12'}:00`)
    await dialog.getByLabel(`End time for ${name}`, { exact: true }).fill(`2099-01-01T${index ? '13' : '11'}:30`)
  }
  await dialog.getByRole('button', { name: 'Use these times', exact: true }).click()
  await dialog.getByRole('alert').filter({ hasText: 'after its start' }).waitFor()
  await dialog.getByLabel('End time for Ada', { exact: true }).fill('2099-01-01T12:30')
  await page.screenshot({ path: `${output}/times-desktop.png`, fullPage: true })
  await dialog.getByRole('button', { name: 'Use these times', exact: true }).click()
  await dialog.waitFor({ state: 'hidden' })
  await page.getByRole('button', { name: 'Review invitations', exact: true }).click()
  const confirm = page.getByRole('button', { name: 'Confirm and queue 2 emails', exact: true })
  await confirm.waitFor()
  assert.equal(lastRequest.schedules.length, 2)
  assert.equal(lastRequest.schedules[0].starts_at, '2099-01-01T06:00:00.000Z')
  assert.equal(lastRequest.schedules[0].display_timezone, 'Asia/Dhaka')
  assert.equal(lastRequest.include_calendar, true)
  assert.equal(confirmations, 0)
  await setTimes.click()
  await dialog.getByLabel('Start time for Ada', { exact: true }).fill('2099-01-01T14:00')
  await dialog.getByLabel('End time for Ada', { exact: true }).fill('2099-01-01T14:30')
  await dialog.getByRole('button', { name: 'Cancel', exact: true }).click()
  assert.equal(await confirm.isVisible(), true)
  await setTimes.click()
  assert.equal(await dialog.getByLabel('Start time for Ada', { exact: true }).inputValue(), '2099-01-01T12:00')
  await page.setViewportSize({ width: 390, height: 844 })
  await page.screenshot({ path: `${output}/times-mobile.png`, fullPage: true })
  assert.equal(await dialog.evaluate(element => element.scrollWidth > element.clientWidth), false)
  await dialog.getByLabel('Start time for Ada', { exact: true }).fill('2099-01-01T14:00')
  await dialog.getByLabel('End time for Ada', { exact: true }).fill('2099-01-01T14:30')
  await dialog.getByRole('button', { name: 'Use these times', exact: true }).click()
  assert.equal(await confirm.count(), 0)
  await page.getByRole('button', { name: 'Review invitations', exact: true }).click()
  await confirm.waitFor()
  assert.equal(lastRequest.schedules[0].starts_at, '2099-01-01T08:00:00.000Z')
  await confirm.click()
  await page.getByRole('status').filter({ hasText: 'Queued 2 individual emails' }).waitFor()
  assert.equal(confirmations, 1)
  assert.deepEqual(errors, [])
  console.log('Invitation UI passed: no default-template panel, time modal, validation, cancel, edits invalidate preview, timezone conversion, calendar option, explicit confirmation and mobile layout.')
} finally { await browser.close() }
