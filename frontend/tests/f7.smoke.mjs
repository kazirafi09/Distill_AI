// Offline UI acceptance: mock network services; exercise real media preview and UI.
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { readFile, mkdir } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import { resolve, extname, sep } from 'node:path'
const { chromium } = createRequire(import.meta.url)('playwright')
const dist = resolve(fileURLToPath(new URL('../dist/', import.meta.url)))
const output = fileURLToPath(new URL('../../output/f7/', import.meta.url))
await mkdir(output, { recursive: true })
const browser = await chromium.launch({ channel: 'chrome', headless: true,
  args: ['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream'] })
const context = await browser.newContext({ permissions: ['camera', 'microphone'], viewport: { width: 1440, height: 1000 } })
const page = await context.newPage()
await page.addInitScript(() => document.addEventListener('loadeddata', event => {
  if (event.target instanceof HTMLVideoElement && event.target.getAttribute('aria-label') === 'Face verification camera preview') {
    window.verificationPreviewReadyAt = performance.now()
  }
}, true))
const errors = [], actions = []
page.on('pageerror', error => errors.push(error.message))
const token = 'a'.repeat(43)
const identity = { status: 'not_performed', consent: 'pending', has_reference: true, reason: null,
  checked_at: null, expires_at: '2026-11-20T12:00:00Z', reviewed_at: null, review_note: null,
  available: true, consent_text: 'I agree to compare one camera frame with my application photo. Images are processed in memory.',
  consent_version: 'face-verification-v3', retention_days: 30,
  attempts: 0, attempt_limit: 5, attempt_limit_reached: false, manual_review_required: false }
const source = { id: 'source', job_description_id: 'job', drive_folder_id: 'folder', spreadsheet_id: 'sheet', sheet_tab_id: 0 }
let attempts = 0
let admitted = false
let joinRequests = 0
let previewFaceCount = 2
let singleFaceSince = null
let lastCheckedFrame = null
let previewChecks = 0
await page.route('**/*', async route => {
  const path = new URL(route.request().url()).pathname
  const file = path === '/interview' || path.startsWith('/recruiter/') ? resolve(dist, 'index.html') : resolve(dist, `.${path}`)
  if (!file.startsWith(dist + sep)) return route.abort()
  try { await route.fulfill({ body: await readFile(file), contentType: { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css' }[extname(file)] ?? 'application/octet-stream' }) }
  catch { await route.fulfill({ status: 404 }) }
})
await page.route('**/api/**', async route => {
  const request = route.request(), path = new URL(request.url()).pathname.replace('/api', '')
  let data = []
  if (path === '/interviews/entry') data = { candidate_name: 'Synthetic Candidate', job_title: 'Backend Engineer', instructions: 'Prepare examples.', expires_at: identity.expires_at }
  else if (path === '/interviews/candidate/status') data = { status: 'in_progress', can_join: admitted, waiting_for_admission: !admitted }
  else if (path === '/interviews/candidate/join') { joinRequests++; data = {} }
  else if (path === '/interviews/candidate/identity') data = identity
  else if (path.endsWith('/identity/consent')) {
    const body = request.postDataJSON(); actions.push(body.choice)
    identity.consent = body.choice
    identity.admission_ready = ['declined', 'withdrawn'].includes(body.choice)
    if (body.choice === 'withdrawn') { identity.status = 'not_performed'; identity.has_reference = false }
    data = identity
  } else if (path.endsWith('/identity/check-frame')) {
    assert.equal(identity.consent, 'accepted')
    previewChecks++
    lastCheckedFrame = request.postDataJSON().frame
    if (previewFaceCount === 1) singleFaceSince ??= Date.now()
    else singleFaceSince = null
    data = { face_count: previewFaceCount, reason: previewFaceCount > 1 ? 'multiple_faces' : previewFaceCount === 0 ? 'no_face' : null }
  } else if (path.endsWith('/identity/verify')) {
    assert.equal(identity.consent, 'accepted'); assert.ok(request.postDataJSON().frame.length > 100)
    assert.equal(request.postDataJSON().frame, lastCheckedFrame, 'Compare the exact frame that passed the face-count check')
    assert.ok(singleFaceSince !== null && Date.now() - singleFaceSince >= 5000,
      'Every interruption must require another full five seconds alone')
    assert.ok(await page.evaluate(() => performance.now() - window.verificationPreviewReadyAt) >= 5000,
      'Capture must wait five full seconds after the waiting-room preview is ready')
    actions.push('verify'); attempts++
    identity.status = attempts === 1 ? 'not_identified' : attempts === 2 || attempts >= 4 ? 'unable_to_verify' : 'matched'
    identity.reason = attempts === 5 ? 'multiple_faces' : attempts === 2 || attempts === 4 ? 'poor_quality' : null
    identity.checked_at = '2026-10-22T12:00:00Z'
    identity.similarity = attempts === 2 || attempts >= 4 ? null : .82
    identity.threshold = .363
    identity.attempts = attempts
    identity.attempt_limit_reached = attempts >= identity.attempt_limit
    identity.manual_review_required = identity.attempt_limit_reached && identity.status !== 'matched'
    identity.admission_ready = !identity.manual_review_required
    data = identity
  } else if (path === '/auth/refresh') data = { access_token: 'fixture' }
  else if (path === '/auth/me') data = { id: 'owner', email: 'owner@example.com', role: 'recruiter' }
  else if (path === '/jd/descriptions') data = { items: [{ id: 'job', title: 'Backend Engineer', number: 1 }] }
  else if (path === '/cv/sources') data = [source]
  else if (path === '/cv/screening/jobs/job/results') data = { run: null, profile: null, ranked: [], incomplete: [], history: [], warnings: [] }
  else if (path.endsWith('/shortlist')) data = { members: [{ application_id: 'application', name: 'Synthetic Candidate', email: 'test@example.com', score: { aggregate: '80' }, rank: 1 }] }
  else if (path.endsWith('/session')) data = { status: 'not_started', candidate_admitted: false }
  else if (path.endsWith('/identity/review')) { identity.reviewed_at = '2026-10-22T12:00:00Z'; identity.review_note = request.postDataJSON().note; identity.admission_ready = true; identity.manual_review_required = false; data = identity }
  else if (path.endsWith('/identity')) data = identity
  await route.fulfill({ json: data })
})
try {
  await page.goto(`http://localhost/interview#${token}`)
  await page.getByRole('button', { name: 'Continue', exact: true }).click()
  const popup = page.getByRole('dialog', { name: 'Verify your application photo' })
  const verification = popup.getByRole('region', { name: 'Photo verification', exact: true })
  await verification.getByRole('button', { name: 'Agree and take my photo' }).waitFor()
  assert.equal(await verification.locator('video').count(), 0)
  assert.equal(attempts, 0)
  assert.equal(previewChecks, 0, 'No preview frames may be checked before consent')
  await verification.getByRole('button', { name: 'Decline · use manual review' }).click()
  await verification.getByRole('button', { name: 'Agree and take my photo' }).click()
  const summary = page.getByRole('region', { name: 'Photo verification summary' })
  const waitingVideo = page.getByRole('region', { name: 'Device preparation' })
  await waitingVideo.getByText('Please be alone in the frame', { exact: true }).waitFor()
  assert.equal(await popup.isVisible(), false)
  assert.equal(await waitingVideo.getByText(/Photo in \d seconds/).count(), 0)
  await page.waitForTimeout(1200)
  assert.equal(attempts, 0, 'Multiple faces must prevent the countdown and comparison')
  previewFaceCount = 1
  await waitingVideo.getByText('Photo in 5 seconds', { exact: true }).waitFor()
  await waitingVideo.getByText('Photo in 3 seconds', { exact: true }).waitFor()
  previewFaceCount = 2
  await waitingVideo.getByText('Please be alone in the frame', { exact: true }).waitFor()
  assert.equal(await waitingVideo.getByText(/Photo in \d seconds/).count(), 0)
  assert.equal(attempts, 0, 'Another face entering must cancel the running countdown')
  previewFaceCount = 1
  await waitingVideo.getByText('Photo in 5 seconds', { exact: true }).waitFor()
  await waitingVideo.getByText('Photo in 4 seconds', { exact: true }).waitFor()
  previewFaceCount = 0
  await waitingVideo.getByText('Looking for your face…', { exact: true }).waitFor()
  assert.equal(attempts, 0, 'Losing the candidate face must also reset the countdown')
  previewFaceCount = 1
  await waitingVideo.getByText('Photo in 5 seconds', { exact: true }).waitFor()
  assert.equal(await popup.isVisible(), false, 'Consent must close the modal before capture')
  assert.equal(await popup.locator('video').count(), 0, 'The modal must never contain a video feed')
  await waitingVideo.getByLabel('Face verification camera preview').waitFor()
  assert.equal(attempts, 0)
  await page.screenshot({ path: resolve(output, 'waiting-room-countdown-desktop.png'), fullPage: true })
  await page.setViewportSize({ width: 390, height: 844 })
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1))
  await page.screenshot({ path: resolve(output, 'waiting-room-countdown-mobile.png'), fullPage: true })
  await summary.getByText('Not identified', { exact: true }).waitFor()
  assert.equal(attempts, 1, 'Consent should automatically capture exactly one frame')
  await summary.getByRole('button', { name: 'Retake and compare photo' }).click()
  await waitingVideo.getByText('Photo in 5 seconds', { exact: true }).waitFor()
  await page.waitForFunction(() => {
    return scrollY === 0
  })
  await page.screenshot({ path: resolve(output, 'waiting-room-countdown-mobile.png'), fullPage: true })
  await waitingVideo.getByRole('button', { name: 'Cancel photo capture' }).click()
  await page.waitForTimeout(5200)
  assert.equal(attempts, 1, 'Cancelling must clear the pending capture timer')
  assert.equal(await waitingVideo.locator('video').count(), 0)
  for (const label of ['Unable to verify', 'Matches submitted photo']) {
    await summary.getByRole('button', { name: 'Retake and compare photo' }).click()
    await summary.getByText(label, { exact: true }).waitFor()
  }
  assert.equal(await page.getByRole('button', { name: 'Join interview', exact: true }).isDisabled(), true,
    'Photo comparison must not grant admission')
  assert.equal(joinRequests, 0)
  await page.screenshot({ path: resolve(output, 'candidate-desktop.png'), fullPage: true })
  await page.setViewportSize({ width: 390, height: 844 })
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1))
  await page.screenshot({ path: resolve(output, 'candidate-mobile.png'), fullPage: true })
  await summary.getByRole('button', { name: 'Retake and compare photo' }).click()
  await summary.getByText('Unable to verify', { exact: true }).waitFor()
  assert.equal(attempts, 4)
  await summary.getByRole('button', { name: 'Retake and compare photo' }).click()
  await summary.getByText('Manual review required', { exact: true }).waitFor()
  await summary.getByText(/You’ve reached the 5-photo limit/).waitFor()
  assert.equal(attempts, 5)
  assert.equal(await waitingVideo.locator('video').count(), 0, 'Fifth multiple-face result must stop capture instead of restarting')
  assert.equal(await summary.getByRole('button', { name: /take.*photo/i }).count(), 0)
  await page.reload()
  await page.getByRole('button', { name: 'Continue', exact: true }).click()
  await verification.getByText('Manual review required', { exact: true }).waitFor()
  assert.equal(await page.getByLabel('Face verification camera preview').count(), 0)
  assert.equal(await verification.getByRole('button', { name: /take.*photo/i }).count(), 0, 'Reload must not restore capture after exhaustion')
  await verification.getByRole('button', { name: 'Continue to waiting room' }).click()
  await summary.getByRole('button', { name: 'Open photo verification' }).click()
  await verification.getByRole('button', { name: 'Withdraw and delete identification data' }).click()
  await verification.getByText(/Identification data was deleted/).waitFor()
  assert.deepEqual(actions, ['declined', 'accepted', 'verify', 'verify', 'verify', 'verify', 'verify', 'withdrawn'])
  await verification.getByRole('button', { name: 'Continue to waiting room' }).click()
  assert.equal(await popup.isVisible(), false)
  await page.getByText('Waiting for the recruiter to admit you.', { exact: true }).first().waitFor()
  admitted = true
  await page.waitForFunction(() => [...document.querySelectorAll('button')].some(button => button.textContent === 'Join interview' && !button.disabled))
  assert.equal(await popup.count(), 0, 'Admission must not reopen pre-admission verification')
  assert.equal(joinRequests, 0, 'Candidate explicitly joins after recruiter permission')

  await page.setViewportSize({ width: 1440, height: 1000 })
  await page.goto('http://localhost/recruiter/cv-screening?job=job')
  await page.getByRole('button', { name: 'Source setup', exact: true }).click()
  const setup = page.getByRole('dialog', { name: 'Application source setup' })
  assert.equal(await setup.getByText('Edit Form field mapping', { exact: true }).count(), 0)
  await setup.getByText('Required response columns: Name, Email, CV File and Image.', { exact: false }).waitFor()
  assert.equal(await setup.getByRole('combobox').count(), 0)
  await page.screenshot({ path: resolve(output, 'fixed-source-setup.png'), fullPage: true })

  identity.has_reference = true; identity.consent = 'accepted'; identity.status = 'unable_to_verify'; identity.reason = 'poor_quality'; identity.similarity = null; identity.threshold = null
  identity.attempts = 1; identity.attempt_limit_reached = false; identity.manual_review_required = false
  await page.goto('http://localhost/recruiter/interviews?job=job')
  const recruiter = page.getByRole('region', { name: 'Candidate identification', exact: true })
  assert.equal(await page.getByText('Candidate profile and interview preparation', { exact: true }).count(), 0)
  await recruiter.getByText('Photo quality was too low', { exact: true }).waitFor()
  await recruiter.getByText('No score yet', { exact: true }).waitFor()
  assert.equal(await recruiter.getByText('Ready for your admission decision.', { exact: true }).count(), 0)
  assert.equal(await recruiter.locator('..').getAttribute('class'), 'lobby-preview-column', 'Verification belongs below the camera controls')
  const controlsBox = await page.locator('.lobby-device-controls').boundingBox()
  const verificationBox = await recruiter.boundingBox()
  assert.ok(verificationBox.y > controlsBox.y + controlsBox.height)
  assert.ok(verificationBox.height < 300, 'The default card should remain compact')
  await recruiter.screenshot({ path: resolve(output, 'verification-card-desktop.png') })
  await page.screenshot({ path: resolve(output, 'verification-layout-desktop.png'), fullPage: true })
  await page.setViewportSize({ width: 390, height: 844 })
  await page.waitForFunction(() => {
    const drawer = document.querySelector('.sidebar-drawer')
    return drawer && getComputedStyle(drawer).visibility === 'hidden'
  })
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1))
  await recruiter.screenshot({ path: resolve(output, 'verification-card-mobile.png') })
  identity.attempts = 5; identity.attempt_limit_reached = true; identity.manual_review_required = true; identity.admission_ready = false
  await page.reload()
  await page.locator('.recruiter-verification').scrollIntoViewIfNeeded()
  await recruiter.getByRole('status').filter({ hasText: 'Manual review required' }).waitFor()
  await recruiter.getByText('Photo attempt limit reached', { exact: true }).waitFor()
  await recruiter.getByText('Record a manual review before admitting this candidate.', { exact: true }).waitFor()
  await recruiter.getByRole('button', { name: 'Review manually', exact: true }).click()
  await recruiter.getByLabel('Review note (do not add identity documents)').fill('Confirmed application details after the photo attempt limit.')
  await recruiter.getByRole('button', { name: 'Record manual review' }).click()
  await recruiter.getByText('Manual review saved', { exact: true }).waitFor()
  identity.reviewed_at = null; identity.review_note = null; identity.manual_review_required = false
  identity.status = 'matched'; identity.reason = null; identity.similarity = .82; identity.threshold = .363
  await page.setViewportSize({ width: 1440, height: 1000 })
  await page.reload()
  await recruiter.getByText('82.0%', { exact: true }).waitFor()
  await recruiter.getByRole('button', { name: 'Review manually', exact: true }).click()
  await recruiter.getByLabel('Review note (do not add identity documents)').fill('Confirmed application details with candidate.')
  await recruiter.getByRole('button', { name: 'Record manual review' }).click()
  await recruiter.getByText('Manual review saved', { exact: true }).waitFor()
  await recruiter.getByText('Details & data controls', { exact: true }).click()
  await recruiter.getByText(/Manual review: Confirmed application details/).waitFor()
  await page.screenshot({ path: resolve(output, 'recruiter-review.png'), fullPage: true })
  assert.deepEqual(errors, [])
  console.log('F7 browser acceptance passed: consent, capture/retry, withdrawal, mobile layout, fixed required columns, recruiter review.')
} catch (error) {
  await page.screenshot({ path: resolve(output, 'failure.png'), fullPage: true })
  console.error(await page.locator('body').innerText())
  throw error
} finally { await browser.close() }
