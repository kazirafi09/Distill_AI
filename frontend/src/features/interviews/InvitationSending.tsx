import { useEffect, useRef, useState } from 'react'
import { ApiError } from '../../services/api'
import { prepareInvitations, confirmInvitations, loadInvitations, revokeInvitation } from './api'
import type { InvitationMode, InvitationPreview, InvitationStatus } from './api'

const statusLabels: Record<string, string> = {
  queued: 'Queued', sending: 'Sending', provider_accepted: 'Accepted by mail provider',
  failed: 'Failed', uncertain: 'Outcome uncertain', not_queued: 'Not queued',
}

export default function InvitationSending({ token, jobId, canSend }: {
  token: string; jobId: string; canSend: boolean
}) {
  const [mode, setMode] = useState<InvitationMode>('new')
  const [preview, setPreview] = useState<InvitationPreview | null>(null)
  const [statuses, setStatuses] = useState<InvitationStatus[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [refresh, setRefresh] = useState(0)
  const requestLock = useRef(false)
  const mounted = useRef(true)

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

  async function perform(action: () => Promise<void>) {
    if (requestLock.current) return
    requestLock.current = true
    setBusy(true)
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
      if (mounted.current) setBusy(false)
    }
  }

  return <section className="interview-saved-panel" aria-label="Interview invitations">
    <h3>Interview invitations</h3>
    <p>Send to the saved interviewees. Review recipients and the email before confirming.</p>
    {!canSend && <p className="interview-hint">Save your current selection and refresh outdated screening results first.</p>}
    {error && <p role="alert" className="interview-notice is-error">{error}</p>}
    {message && <p role="status" className="interview-notice">{message}</p>}
    <div className="interview-selection-actions">
      <label>Invitation action <select value={mode} disabled={busy} onChange={event => {
        setMode(event.target.value as InvitationMode); setPreview(null)
      }}>
        <option value="new">Send new invitations</option>
        <option value="retry_failed">Retry failed emails with existing links</option>
        <option value="reissue">Replace links and send again</option>
      </select></label>
      <button className="button-primary" disabled={busy || !canSend} onClick={() => void perform(async () => {
        const next = await prepareInvitations(token, jobId, mode)
        if (mounted.current) setPreview(next)
      })}>Send interview invitations</button>
      <button className="button-secondary" disabled={busy} onClick={() => setRefresh(value => value + 1)}>Refresh outcomes</button>
    </div>
    {preview && <div className="interview-notice">
      <h4>Review {preview.job_title}</h4>
      <p>From: {preview.sender} · {preview.send_count} emails · Links expire after {preview.link_days} days</p>
      {preview.mode === 'reissue' && <p>Confirming replaces earlier links. Those links will stop working.</p>}
      <ul>{preview.recipients.map(recipient => <li key={recipient.application_id}>
        {recipient.name} — {recipient.email}: {recipient.reason}
      </li>)}</ul>
      <strong>Subject: {preview.subject}</strong>
      <pre style={{ whiteSpace: 'pre-wrap' }}>{preview.body}</pre>
      <p>Each email receives that candidate's unique link. This preview expires at {new Date(preview.expires_at).toLocaleTimeString()}.</p>
      <div className="interview-selection-actions">
        <button className="button-primary" disabled={busy || !canSend || !preview.send_count} onClick={() => void perform(async () => {
          const result = await confirmInvitations(token, jobId, preview)
          if (mounted.current) { setMessage(result.message); setPreview(null); setRefresh(value => value + 1) }
        })}>Confirm and queue {preview.send_count} emails</button>
        <button className="button-secondary" disabled={busy} onClick={() => setPreview(null)}>Cancel</button>
      </div>
    </div>}
    {!!statuses.length && <div className="interview-table-scroll"><table className="interview-table">
      <thead><tr><th>Candidate</th><th>Email outcome</th><th>Link</th><th>Actions</th></tr></thead>
      <tbody>{statuses.map(item => <tr key={item.id}>
        <td>{item.name}<span className="interview-email">{item.email}</span></td>
        <td>{statusLabels[item.status] ?? item.status}<span className="interview-email">{item.attempts} attempts{item.error_code ? ` · ${item.error_code.replaceAll('_', ' ').toLowerCase()}` : ''}</span></td>
        <td>{item.link_status} · {new Date(item.expires_at).toLocaleString()}</td>
        <td><button className="button-secondary" disabled={busy || item.link_status !== 'active'} onClick={() => {
          if (!window.confirm(`Revoke the interview link for ${item.name}?`)) return
          void perform(async () => {
            await revokeInvitation(token, jobId, item.id)
            const next = await loadInvitations(token, jobId)
            if (mounted.current) { setStatuses(next); setPreview(null) }
          })
        }}>Revoke link</button></td>
      </tr>)}</tbody>
    </table></div>}
    <p className="interview-hint">Provider acceptance does not confirm delivery. For uncertain sends, check the sender's Sent folder before explicitly replacing the link and sending again.</p>
  </section>
}
