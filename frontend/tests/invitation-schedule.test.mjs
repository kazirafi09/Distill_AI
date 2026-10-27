import assert from 'node:assert/strict'
import test from 'node:test'
import { localTimeInput, scheduleSummary, scheduleError } from '../src/features/interviews/invitationScheduleView.ts'

test('invitation schedule validation rejects missing, reversed and ended slots', () => {
  assert.match(scheduleError({}, ['candidate']), /every candidate/)
  assert.match(scheduleError({ candidate: { start: '2099-01-01T12:00', end: '2099-01-01T11:00' } }, ['candidate']), /after/)
  assert.match(scheduleError({ candidate: { start: '2000-01-01T12:00', end: '2000-01-01T13:00' } }, ['candidate']), /future/)
  assert.equal(scheduleError({ candidate: { start: '2099-01-01T12:00', end: '2099-01-01T13:00' } }, ['candidate']), '')
})

test('an interview may already have started while its end time is still ahead', () => {
  const slot = { candidate: { start: '2026-10-25T10:30', end: '2026-10-25T11:30' } }
  assert.equal(scheduleError(slot, ['candidate'], new Date('2026-10-25T10:45').getTime()), '')
  assert.equal(scheduleError(slot, ['candidate'], new Date('2026-10-25T10:30').getTime()), '')
  assert.match(scheduleError(slot, ['candidate'], new Date('2026-10-25T11:30').getTime()), /has ended/)
})

test('saved schedules display their explicit timezone and preserve instants in local inputs', () => {
  assert.equal(localTimeInput(), '')
  assert.equal(localTimeInput('invalid'), '')
  const instant = '2099-01-01T06:00:00Z'
  assert.equal(new Date(localTimeInput(instant)).toISOString(), '2099-01-01T06:00:00.000Z')
  const summary = scheduleSummary({ starts_at: instant, ends_at: '2099-01-01T06:30:00Z', display_timezone: 'Asia/Dhaka' })
  assert.match(summary, /12:00/)
  assert.match(summary, /12:30/)
  assert.match(summary, /Asia\/Dhaka/)
  assert.equal(scheduleSummary(), 'Time not set')
})
