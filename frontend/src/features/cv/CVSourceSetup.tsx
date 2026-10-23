import { useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { ApiError } from '../../services/api'
import { linkSheet, linkSource, parseFolder, parseSheet } from './api'
import type { CVSource } from './api'
import type { JobDescriptionSummary } from '../jd/api'

function errorMessage(error: unknown) {
  if (error instanceof ApiError && error.status === 401) return 'Your session expired. Sign out and sign in again.'
  return error instanceof Error ? error.message : 'Something went wrong. Please try again.'
}
export default function ConnectionForm({ token, job, source, onLinked }: {
  token: string
  job: JobDescriptionSummary
  source?: CVSource
  onLinked: (source: CVSource) => void
}) {
  const [folder, setFolder] = useState('')
  const [sheet, setSheet] = useState(source?.spreadsheet_id ?? '')
  const [tab, setTab] = useState(source?.sheet_tab_id?.toString() ?? '')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const submitting = useRef(false)

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (submitting.current) return
    submitting.current = true
    setSaving(true)
    setError('')
    try {
      const mapping = parseSheet(sheet, tab)
      let current = source
      if (!current) {
        current = await linkSource(token, job.id, parseFolder(folder))
        onLinked(current) // Preserve the source even if linking the Sheet fails.
      }
      const linked = await linkSheet(token, current.id, mapping.spreadsheetId, mapping.tabId)
      onLinked(linked)
    } catch (caught) {
      setError(errorMessage(caught))
    } finally {
      submitting.current = false
      setSaving(false)
    }
  }

  return (
    <form className="cv-connect" onSubmit={(event) => void handleSubmit(event)}>
      <div>
        <h3>Connect form responses</h3>
        <p>Share the response Sheet, CV folder and image upload folder with your service account.</p>
      </div>
      {!source && <label>CV upload folder URL or ID
        <input required value={folder} onChange={(event) => setFolder(event.target.value)} placeholder="https://drive.google.com/drive/folders/…" disabled={saving} />
        <span className="cv-hint">Use the Form’s upload folder inside your configured CV parent folder.</span>
      </label>}
      {source && <p className="cv-hint">CV folder is linked. Complete the response Sheet connection below.</p>}
      <div className="cv-connect-fields">
        <label>Response Sheet URL or ID
          <input required value={sheet} onChange={(event) => setSheet(event.target.value)} placeholder="https://docs.google.com/spreadsheets/d/…/edit#gid=…" disabled={saving || !!source?.spreadsheet_id} />
        </label>
        <label>Response tab ID (gid)
          <input value={tab} onChange={(event) => setTab(event.target.value)} placeholder="Optional when URL has gid" inputMode="numeric" disabled={saving || !!source?.spreadsheet_id} />
        </label>
      </div>
      <p className="cv-hint">Required response columns: Name, Email, CV File and Image. Every response needs a CV upload and one JPEG or PNG image (up to 5 MB). Timestamp is optional. Extra fields are ignored.</p>
      {error && <p className="cv-error" role="alert">{error}</p>}
      <button className="button-primary" disabled={saving}>{saving ? 'Connecting…' : 'Connect responses'}</button>
    </form>
  )
}


