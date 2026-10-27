import { useEffect, useRef, useState } from 'react'
import { ApiError } from '../../services/api'
import { prepareInvitations, confirmInvitations, loadInvitations, revokeInvitation, loadInvitationOptions, saveInvitationSchedules } from './api'
import type { InvitationMode, InvitationPreview, InvitationStatus, InvitationOptions } from './api'
import { localTimeInput, scheduleSummary, scheduleError } from './invitationScheduleView'
import type { TimeInputs } from './invitationScheduleView'
import './InvitationSending.css'

const statusLabels: Record<string, string> = {
  queued: 'Queued', sending: 'Sending', provider_accepted: 'Accepted by mail provider',
  failed: 'Failed', uncertain: 'Outcome uncertain', not_queued: 'Not queued',
}

export default function InvitationSending({ token, jobId, canSend, shortlistRevision = 0 }: {
  token: string; jobId: string; canSend: boolean; shortlistRevision?: number
}) {
  const [mode, setMode] = useState<InvitationMode>('new')
  const [preview, setPreview] = useState<InvitationPreview | null>(null)
  const [statuses, setStatuses] = useState<InvitationStatus[]>([])
  const [busy, setBusy] = useState(false)
  const [operation, setOperation] = useState<'preview' | 'queue' | 'revoke' | 'schedule' | null>(null)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [refresh, setRefresh] = useState(0)
  const requestLock = useRef(false)
  const mounted = useRef(true)
  const scheduleDialog = useRef<HTMLDialogElement>(null)
  const [options, setOptions] = useState<InvitationOptions | null>(null)
  const [optionsError, setOptionsError] = useState('')
  const [times, setTimes] = useState<TimeInputs>({})
  const [draftTimes, setDraftTimes] = useState<TimeInputs>({})
  const [sharedTime, setSharedTime] = useState({ start: '', end: '' })
  const [sharedTimeError, setSharedTimeError] = useState('')
  const [timeError, setTimeError] = useState('')
  const localTimezone = Intl.DateTimeFormat().resolvedOptions().timeZone
  const scheduledCount = options?.recipients.filter(item => times[item.application_id]?.start && times[item.application_id]?.end).length ?? 0

  useEffect(() => {
    const controller = new AbortController()
    void loadInvitationOptions(token, jobId, controller.signal).then(next => {
      if (controller.signal.aborted) return
      setOptions(next)
      setOptionsError('')
      setTimes(Object.fromEntries(next.recipients.map(item => [item.application_id,
        { start: localTimeInput(item.schedule?.starts_at), end: localTimeInput(item.schedule?.ends_at) }])))
      setPreview(null)
    }).catch(caught => { if (!controller.signal.aborted) setOptionsError(caught instanceof Error ? caught.message : 'Could not load invitation settings.') })
    return () => controller.abort()
  }, [token, jobId, shortlistRevision, refresh])

  function openTimes() {
    setDraftTimes(Object.fromEntries(Object.entries(times).map(([id, value]) => [id, { ...value }])))
    const firstId = options?.recipients[0]?.application_id
    setSharedTime(firstId && times[firstId] ? { ...times[firstId] } : { start: '', end: '' })
    setSharedTimeError('')
    setTimeError('')
    scheduleDialog.current?.showModal()
  }

  function applyTimeToAll() {
    const invalid = scheduleError({ shared: sharedTime }, ['shared'])
    if (invalid) { setSharedTimeError(invalid); return }
    setDraftTimes(Object.fromEntries((options?.recipients ?? []).map(item => [item.application_id, { ...sharedTime }])))
    setSharedTimeError('')
    setTimeError('')
  }

  async function saveTimes() {
    if (requestLock.current) return
    const invalid = scheduleError(draftTimes, options?.recipients.map(item => item.application_id) ?? [])
    if (invalid) { setTimeError(invalid); return }
    requestLock.current = true
    setBusy(true)
    setOperation('schedule')
    setTimeError('')
    try {
      const schedules = (options?.recipients ?? []).map(item => ({ application_id: item.application_id,
        starts_at: new Date(draftTimes[item.application_id].start).toISOString(),
        ends_at: new Date(draftTimes[item.application_id].end).toISOString(), display_timezone: localTimezone }))
      const result = await saveInvitationSchedules(token, jobId, schedules)
      if (mounted.current) {
        setTimes(draftTimes)
        setPreview(null)
        setError('')
        setMessage(result.message)
        scheduleDialog.current?.close()
      }
    } catch (caught) {
      if (mounted.current) setTimeError(caught instanceof Error ? caught.message : 'Could not save interview times.')
    } finally {
      requestLock.current = false
      if (mounted.current) { setBusy(false); setOperation(null) }
    }
  }

  useEffect(() => {
    mounted.current = true
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout> | undefined
    async function poll() {
      try {
        const next = await loadInvitations(token, jobId, controller.signal)
        if (controller.signal.aborted) return
        setStatuses(next)
        if (next.some(item => item.status === 'queued' || item.status === 'sending')) {
          timer = setTimeout(() => void poll(), 5000)
        }
      } catch (caught) {
        if (!controller.signal.aborted) setError(caught instanceof Error ? caught.message : 'Could not load invitations.')
      }
    }
    void poll()
    return () => { mounted.current = false; controller.abort(); clearTimeout(timer) }
  }, [token, jobId, refresh])

  async function perform(action: () => Promise<void>, operation: 'preview' | 'queue' | 'revoke') {
    if (requestLock.current) return
    requestLock.current = true
    setBusy(true)
    setOperation(operation)
    setError('')
    try {
      await action()
    } catch (caught) {
      if (mounted.current) {
        setError(caught instanceof Error ? caught.message : 'Could not complete this action.')
        if (caught instanceof ApiError && caught.status === 409) setPreview(null)
      }
    } finally {
      requestLock.current = false
      if (mounted.current) { setBusy(false); setOperation(null) }
    }
  }

  return <section className="interview-saved-panel invitation-panel" aria-label="Interview invitations">
    <div className="invitation-panel-heading">
      <div><h3>Interview invitations</h3>
        <p>Send to the saved interviewees. Review recipients and the email before confirming.</p></div>
      <button type="button" className="button-secondary invitation-refresh" disabled={busy} onClick={() => setRefresh(value => value + 1)}>
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true"><path d="M20 7v5h-5M4 17v-5h5" /><path d="M6 7a7 7 0 0 1 12-1l2 3M4 15l2 3a7 7 0 0 0 12-1" /></svg>
        Refresh outcomes
      </button>
    </div>
    {!canSend && <p className="interview-hint">Save your current selection and refresh outdated screening results first.</p>}
    {error && <p role="alert" className="interview-notice is-error">{error}</p>}
    {message && <p role="status" data-motion-success className="interview-notice">{message}</p>}
    {optionsError && <p role="alert" className="interview-notice is-error">{optionsError}</p>}
    <div className="invitation-settings">
      <section className="invitation-schedule-card"><div><h4>Interview schedule</h4><p>{scheduledCount} of {options?.recipients.length ?? 0} candidates have times set.</p><p>Joining opens {options?.join_early_minutes ?? 10} minutes before the start and closes at the end. Recruiter admission is still required.</p></div>
        <button type="button" className="button-secondary" disabled={busy || !options?.recipients.length || mode === 'retry_failed'} onClick={openTimes}>Set interview times</button>
        {mode === 'retry_failed' && <p className="interview-hint">Retries keep the original email and times. Choose “Reschedule / replace links and send again” to change them.</p>}
      </section>
    </div>
    <div className="invitation-send-controls">
      <label>Invitation action <select aria-label="Invitation action" value={mode} disabled={busy} onChange={event => {
        setMode(event.target.value as InvitationMode); setPreview(null)
      }}>
        <option value="new">Send new invitations</option>
        <option value="retry_failed">Retry failed emails with existing links</option>
        <option value="reissue">Reschedule / replace links and send again</option>
      </select></label>
      <button type="button" className="button-primary" disabled={busy || !canSend || !options || !!optionsError} onClick={() => void perform(async () => {
        const schedules = mode === 'retry_failed' ? [] : options!.recipients.flatMap(item => {
          const value = times[item.application_id]
          return value?.start && value?.end ? [{ application_id: item.application_id, starts_at: new Date(value.start).toISOString(), ends_at: new Date(value.end).toISOString(), display_timezone: localTimezone }] : []
        })
        const next = await prepareInvitations(token, jobId, mode, schedules)
        if (mounted.current) setPreview(next)
      }, 'preview')}>{operation === 'preview' ? 'Preparing preview…' : 'Review invitations'}</button>
    </div>
    {preview && <div className="interview-notice">
      <h4>Review {preview.job_title}</h4>
      <p>From: {preview.sender} · {preview.send_count} emails · {preview.include_calendar ? 'Calendar file included' : 'Calendar not included'}</p>
      <p>Each scheduled link closes at the interview end time.</p>
      {preview.mode === 'reissue' && <p>Confirming replaces earlier links. Those links will stop working.</p>}
      <div className="invitation-recipient-previews">{preview.recipients.map(recipient => <details key={recipient.application_id}>
        <summary>{recipient.name} · {recipient.email} · {recipient.reason}</summary>
        <p>{scheduleSummary(recipient.schedule)}</p><strong>Subject: {recipient.subject}</strong><pre>{recipient.body}</pre>
      </details>)}</div>
      <p>Each email receives that candidate's unique link. This preview expires at {new Date(preview.expires_at).toLocaleTimeString()}.</p>
      <div className="interview-selection-actions">
        <button className="button-primary" disabled={busy || !canSend || !preview.send_count} onClick={() => void perform(async () => {
          const result = await confirmInvitations(token, jobId, preview)
          if (mounted.current) { setMessage(result.message); setPreview(null); setRefresh(value => value + 1) }
        }, 'queue')}>{operation === 'queue' ? 'Queueing emails…' : `Confirm and queue ${preview.send_count} emails`}</button>
        <button className="button-secondary" disabled={busy} onClick={() => setPreview(null)}>Cancel</button>
      </div>
    </div>}
    {!!statuses.length && <div className="interview-table-scroll"><table className="interview-table">
      <thead><tr><th>Candidate</th><th>Email outcome</th><th>Link</th><th>Actions</th></tr></thead>
      <tbody>{statuses.map(item => <tr key={item.id}>
        <td>{item.name}<span className="interview-email">{item.email}</span></td>
        <td>{statusLabels[item.status] ?? item.status}<span className="interview-email">{item.attempts} attempts{item.error_code ? ` · ${item.error_code.replaceAll('_', ' ').toLowerCase()}` : ''}</span></td>
        <td>{scheduleSummary(item.schedule)}<span className="interview-email">{item.link_status} · Valid until {new Date(item.expires_at).toLocaleString()}</span></td>
        <td><button type="button" className="invitation-revoke" disabled={busy || item.link_status !== 'active'} onClick={() => {
          if (!window.confirm(`Revoke the interview link for ${item.name}?`)) return
          void perform(async () => {
            await revokeInvitation(token, jobId, item.id)
            const next = await loadInvitations(token, jobId)
            if (mounted.current) { setStatuses(next); setPreview(null) }
          }, 'revoke')
        }}>Revoke link</button></td>
      </tr>)}</tbody>
    </table></div>}
    <dialog className="invitation-time-dialog" ref={scheduleDialog} aria-labelledby="invitation-time-title">
      <header><div><h3 id="invitation-time-title">Set interview times</h3><p>Times are entered in {localTimezone}. Apply one slot to all or edit each candidate.</p></div><button type="button" className="button-secondary" disabled={busy} onClick={() => scheduleDialog.current?.close()}>Close</button></header>
      <form onSubmit={event => { event.preventDefault(); void saveTimes() }}>
        <div className="invitation-time-list">
          <fieldset className="invitation-shared-time" disabled={busy}><legend>Same time for everyone</legend>
            <label>Start time<input aria-label="Start time for all candidates" type="datetime-local" value={sharedTime.start} onChange={event => { setSharedTime(current => ({ ...current, start: event.target.value })); setSharedTimeError('') }} /></label>
            <label>End time<input aria-label="End time for all candidates" type="datetime-local" value={sharedTime.end} onChange={event => { setSharedTime(current => ({ ...current, end: event.target.value })); setSharedTimeError('') }} /></label>
            <button type="button" className="button-secondary" disabled={!sharedTime.start || !sharedTime.end || !options?.recipients.length} onClick={applyTimeToAll}>Apply to all</button>
            {sharedTimeError && <p className="cv-error" role="alert">{sharedTimeError}</p>}
          </fieldset>
          {options?.recipients.map(item => <fieldset key={item.application_id} disabled={busy}>
          <legend>{item.name}<small>{item.email}</small></legend>
          <label>Start time<input aria-label={`Start time for ${item.name}`} type="datetime-local" required value={draftTimes[item.application_id]?.start ?? ''} onChange={event => setDraftTimes(current => ({ ...current, [item.application_id]: { ...current[item.application_id], start: event.target.value } }))} /></label>
          <label>End time<input aria-label={`End time for ${item.name}`} type="datetime-local" required value={draftTimes[item.application_id]?.end ?? ''} onChange={event => setDraftTimes(current => ({ ...current, [item.application_id]: { ...current[item.application_id], end: event.target.value } }))} /></label>
        </fieldset>)}</div>
        {timeError && <p className="cv-error" role="alert">{timeError}</p>}
        <footer><p>Save these times to use here or in the assistant. You can edit them later. Sending invitations requires a separate review and confirmation.</p><div><button type="button" className="button-secondary" disabled={busy} onClick={() => scheduleDialog.current?.close()}>Cancel</button><button type="submit" className="button-primary" disabled={busy}>{operation === 'schedule' ? 'Saving times…' : 'Use these times'}</button></div></footer>
      </form>
    </dialog>
    <p className="interview-hint">Provider acceptance does not confirm delivery. For uncertain sends, check the sender's Sent folder before explicitly replacing the link and sending again.</p>
  </section>
}
