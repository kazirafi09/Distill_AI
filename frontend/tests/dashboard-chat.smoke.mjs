// Run against the Vite development server: StrictMode must remain enabled.
// Every API request is intercepted; no real account, model or tool is used.
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
const { chromium } = createRequire(import.meta.url)('playwright')
const browser = await chromium.launch({ headless: true, channel: 'chrome' })
const page = await browser.newPage()
let creations = 0, messages = 0
const failures = []
page.on('requestfailed', request => { if (request.url().includes('/api/')) failures.push(new URL(request.url()).pathname) })
const empty = { conversation_id: 'fixture-chat', created_at: '2026-10-25T00:00:00Z', checkpoint_id: null,
  mode: 'chat', messages: [], error_code: null, jd_conversation: null }
await page.route('**/api/**', async route => {
  const path = new URL(route.request().url()).pathname.replace(/^\/api/, '')
  let data
  if (path === '/auth/refresh') data = { access_token: 'fixture', token_type: 'bearer' }
  else if (path === '/auth/me') data = { id: 'fixture-user', email: 'recruiter@example.com', role: 'recruiter' }
  else if (path === '/health/db') data = { status: 'ok', database: 'connected' }
  else if (path === '/cv/source-checks') data = []
  else if (path === '/dashboard') data = { updated_at: '2026-10-25T00:00:00Z', totals: { jobs: 0, applications: 0, waiting: 0, attention: 0 }, source_count: 0, active_session_count: 0, jobs: [], attention: [], sessions: [], activity: [] }
  else if (path === '/jd/descriptions') data = { items: [], limit: 10, offset: 0 }
  else if (path === '/assistant/conversations' && route.request().method() === 'GET') data = { items: [], limit: 20, offset: 0 }
  else if (path === '/assistant/conversations') { creations++; data = empty }
  else if (path === '/assistant/conversations/fixture-chat/messages') {
    messages++
    assert.equal(route.request().postDataJSON().message, 'tell me about job 139')
    return route.fulfill({ contentType: 'text/event-stream', body: `data: ${JSON.stringify({ type: 'complete', conversation: { ...empty, checkpoint_id: 'done', messages: [
      { id: 'user-1', role: 'user', content: 'tell me about job 139' },
      { id: 'reply-1', role: 'assistant', content: 'JD 139: Build Python APIs.' },
    ] } })}\n\n` })
  } else return route.fulfill({ status: 404, json: { detail: `Unmocked route ${path}` } })
  return route.fulfill({ json: data })
})
try {
  await page.goto('http://localhost:5173/recruiter/dashboard')
  await page.getByRole('textbox', { name: 'Ask the recruiter assistant', exact: true }).fill('tell me about job 139')
  await page.getByRole('textbox', { name: 'Ask the recruiter assistant', exact: true }).press('Enter')
  await page.getByText('JD 139: Build Python APIs.', { exact: true }).waitFor({ timeout: 6000 })
  assert.equal(creations, 1)
  assert.equal(messages, 1)
  assert.equal(await page.getByRole('button', { name: 'Stop waiting', exact: true }).count(), 0)
  await page.getByRole('link', { name: 'Job Descriptions', exact: true }).click()
  await page.getByRole('link', { name: 'Dashboard', exact: true }).click()
  await page.getByRole('textbox', { name: 'Ask the recruiter assistant', exact: true }).fill('tell me about job 139')
  await page.getByRole('textbox', { name: 'Ask the recruiter assistant', exact: true }).press('Enter')
  await page.getByText('JD 139: Build Python APIs.', { exact: true }).waitFor({ timeout: 6000 })
  assert.equal(creations, 2)
  assert.equal(messages, 2)
  console.log('Dashboard chat passed under development StrictMode: first message completes once, leaving and returning starts fresh, no duplicate sends.')
} catch (error) {
  console.log(JSON.stringify({ creations, messages, failures }))
  throw error
} finally { await browser.close() }
