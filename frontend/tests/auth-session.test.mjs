import assert from 'node:assert/strict'
import { test, beforeEach } from 'node:test'
import { ApiError, acceptSession, apiRequest, clearSession, logoutRecruiter, restoreSession } from '../src/services/api.ts'

globalThis.window = new EventTarget()
const profile = { id: 'test-user', email: 'person@example.com', role: 'recruiter' }
const json = (body, status = 200) => new Response(JSON.stringify(body), { status })
beforeEach(() => clearSession())

test('a reload restores identity using the cookie and sends credentials', async () => {
  const calls = []
  globalThis.fetch = async (url, options) => {
    calls.push(url)
    assert.equal(options.credentials, 'same-origin')
    if (url.endsWith('/refresh')) return json({ access_token: 'restored' })
    assert.equal(options.headers.get('Authorization'), 'Bearer restored')
    return json(profile)
  }
  assert.deepEqual(await restoreSession(), { token: 'restored', user: profile })
  assert.deepEqual(calls, ['/api/auth/refresh', '/api/auth/me'])
})

test('concurrent expired requests share renewal and retry with the new token', async () => {
  let renewals = 0
  let successes = 0
  globalThis.fetch = async (url, options) => {
    if (url.endsWith('/refresh')) {
      renewals++
      await new Promise(resolve => setTimeout(resolve, 10))
      return json({ access_token: 'renewed' })
    }
    if (options.headers.get('Authorization') === 'Bearer renewed') {
      successes++
      return json({ ok: true })
    }
    return json({ detail: 'Expired' }, 401)
  }
  await Promise.all([apiRequest('/jobs', { token: 'expired' }), apiRequest('/cvs', { token: 'expired' })])
  assert.equal(renewals, 1)
  assert.equal(successes, 2)
  await apiRequest('/jobs', { token: 'expired' })
  assert.equal(renewals, 1)
})

test('missing saved session returns signed-out state; network failures remain retryable', async () => {
  globalThis.fetch = async () => json({ detail: 'No session' }, 401)
  assert.equal(await restoreSession(), null)
  globalThis.fetch = async () => { throw new TypeError('Network unavailable') }
  await assert.rejects(restoreSession(), TypeError)
})

test('revoked sessions notify the app and do not retry the protected action', async () => {
  let events = 0
  const onExpired = () => events++
  window.addEventListener('distill-session-expired', onExpired)
  globalThis.fetch = async () => json({ detail: 'Revoked' }, 401)
  await assert.rejects(apiRequest('/jobs', { token: 'revoked' }), ApiError)
  assert.equal(events, 1)
  window.removeEventListener('distill-session-expired', onExpired)
})

test('sign-out revokes the cookie session before clearing memory', async () => {
  globalThis.fetch = async (url, options) => {
    if (url.endsWith('/logout')) return new Response(null, { status: 204 })
    if (url.endsWith('/me')) return json(profile)
    assert.equal(options.headers.get('Authorization'), 'Bearer other-token')
    return json({ ok: true })
  }
  await acceptSession('signed-in')
  await logoutRecruiter()
  await apiRequest('/jobs', { token: 'other-token' })
})
