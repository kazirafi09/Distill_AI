import { useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { apiRequest } from '../../services/api'
import type { CandidateApplication } from './api'

export default function DeleteCandidate({ token, application, jobId, jobTitle, onDeleted }: {
  token: string; application: CandidateApplication; jobId: string; jobTitle: string; onDeleted: (pending: boolean) => void
}) {
  const dialog = useRef<HTMLDialogElement>(null)
  const saving = useRef(false)
  const [confirmed, setConfirmed] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  async function remove() {
    if (!confirmed || saving.current) return
    saving.current = true; setBusy(true); setError('')
    try {
      const result = await apiRequest<{ deleted: boolean; cleanup_pending: boolean }>(
        `/cv/jobs/${jobId}/applications/${application.id}/delete`,
        { token, method: 'POST', body: { confirmed: true } },
      )
      dialog.current?.close()
      onDeleted(result.cleanup_pending)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Could not delete this candidate. Try again.')
    } finally { saving.current = false; setBusy(false) }
  }

  return <>
    <button className="button-secondary screening-delete-button" onClick={() => {
      setConfirmed(false); setError(''); dialog.current?.showModal()
    }}>Delete candidate</button>
    {createPortal(<dialog ref={dialog} className="screening-dialog screening-delete-dialog" aria-labelledby="delete-candidate-title"
      onCancel={event => { if (saving.current) event.preventDefault() }}>
      <header><h2 id="delete-candidate-title">Delete candidate?</h2><button className="button-secondary" disabled={busy} onClick={() => dialog.current?.close()}>Cancel</button></header>
      <p>Delete <strong>{application.full_name}</strong> ({application.email}) from <strong>{jobTitle}</strong>?</p>
      <p>This permanently removes this application, its saved CVs and profile, screening results, invitations, interview transcript, identification data and assessments. It also removes the application from the shortlist and final selection.</p>
      <p>Applications for other jobs, Google Drive files, response Sheet rows, existing chat history and previously downloaded reports or delivered emails remain unchanged. These CV uploads will be excluded from automatic import for this job.</p>
      <p className="screening-notice is-warning">This cannot be undone. File and search-index cleanup is retried automatically if a storage service is unavailable.</p>
      <label className="screening-delete-confirm"><input type="checkbox" checked={confirmed} disabled={busy} onChange={event => setConfirmed(event.target.checked)} />I understand and confirm permanent deletion of this application.</label>
      {error && <p role="alert" className="screening-notice is-danger">{error}</p>}
      <footer><button className="button-secondary" disabled={busy} onClick={() => dialog.current?.close()}>Keep candidate</button><button className="button-primary screening-delete-confirm-button" disabled={!confirmed || busy} aria-busy={busy} onClick={() => void remove()}>{busy ? 'Deleting…' : 'Permanently delete'}</button></footer>
    </dialog>, document.body)}
  </>
}
