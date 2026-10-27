import { useEffect, useRef, useState } from 'react'
import { useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import { useRecruitmentJob } from '../interviews/useRecruitmentJob'
import ShortlistWorkspace from '../interviews/ShortlistWorkspace'
import { listApplications, listSources } from './api'
import type { CandidateApplication, CVSource, CVSourceCheck } from './api'
import ConnectionForm from './CVSourceSetup'
import ScreeningWorkspace from './ScreeningWorkspace'
import './CVScreening.css'
import '../jd/JobDescriptions.css'
import './ScreeningWorkspace.css'

export default function CVScreening({ token, sourceChecks, checksError, refreshChecks }: {
  token: string; sourceChecks: CVSourceCheck[]; checksError: string; refreshChecks: () => void
}) {
  const picker = useRecruitmentJob(token)
  const [params, setParams] = useSearchParams()
  const location = useLocation()
  const navigate = useNavigate()
  const [sources, setSources] = useState<CVSource[] | null>(null)
  const [sourceError, setSourceError] = useState('')
  const [revision, setRevision] = useState(0)
  const [sourceRevision, setSourceRevision] = useState(0)
  const [candidates, setCandidates] = useState<{ jobId: string; revision: number; applications: CandidateApplication[]; error: string } | null>(null)
  const [sourceJob, setSourceJob] = useState<string | null>(null)
  const sourceDialog = useRef<HTMLDialogElement>(null)
  const shortlistDialog = useRef<HTMLDialogElement>(null)
  const shortlistOpen = params.get('shortlist') === '1' || location.hash === '#interview-shortlist'
  const job = picker.job
  const jobId = job?.id ?? ''
  const source = sources?.find(item => item.job_description_id === jobId)
  const connected = !!source && source.spreadsheet_id != null && source.sheet_tab_id != null
  const check = sourceChecks.find(item => item.job_description_id === jobId)
  const importError = check?.error_code === 'CV_SOURCE_CONFIGURATION_INVALID'
    ? 'Source setup is invalid. Check the selected Sheet tab and folder. The response Sheet requires exactly one Name, Email, CV File and Image column. The worker will retry in 1 minute.'
    : check?.error_code === 'CV_SOURCE_IMPORT_INCOMPLETE'
      ? 'Some CV files or Sheet rows could not be imported. Check the response columns, file links and access permissions. The worker will retry in 1 minute.'
      : 'The last automatic import failed. Check source setup and access permissions. The worker will retry in 1 minute.'
  const current = candidates?.jobId === jobId && candidates.revision === revision ? candidates : null
  const lastImportCheck = check?.last_checked_at
    ? new Date(check.last_checked_at).toLocaleString('en-GB', { timeZone: 'Asia/Dhaka' })
    : null

  useEffect(() => {
    const dialog = shortlistDialog.current
    if (shortlistOpen && jobId && !dialog?.open) dialog?.showModal()
    else if (!shortlistOpen && dialog?.open) dialog.close()
  }, [shortlistOpen, jobId])

  function closeShortlist() {
    if (!shortlistOpen) return
    const next = new URLSearchParams(params)
    next.delete('shortlist')
    navigate({ pathname: location.pathname, search: next.toString(), hash: '' }, { replace: true })
  }

  useEffect(() => {
    const controller = new AbortController()
    void listSources(token, controller.signal).then(items => {
      if (!controller.signal.aborted) { setSources(items); setSourceError('') }
    }).catch(error => { if (!controller.signal.aborted) setSourceError(error instanceof Error ? error.message : 'Could not load source connections.') })
    return () => controller.abort()
  }, [token, sourceRevision])

  useEffect(() => {
    if (!jobId) return
    const controller = new AbortController()
    void listApplications(token, jobId, controller.signal).then(applications => {
      if (!controller.signal.aborted) setCandidates({ jobId, revision, applications, error: '' })
    }).catch(error => { if (!controller.signal.aborted) setCandidates({ jobId, revision, applications: [], error: error instanceof Error ? error.message : 'Could not load applications.' }) })
    return () => controller.abort()
  }, [token, jobId, revision, check?.last_checked_at])
  function openSource() { setSourceJob(jobId); sourceDialog.current?.showModal() }
  function linked(next: CVSource) { setSources(current => [...(current ?? []).filter(item => item.id !== next.id), next]); refreshChecks() }

  return <section className="cv-redesign">
    <header className="redesign-header"><div><p className="workspace-eyebrow">Recruitment workspace</p><h1>CV Screening</h1><p className="muted">Review candidates against approved job criteria.</p></div><div className="screening-header-actions">
      {job && <div className="screening-import-status">
        <button type="button" className={`screening-import-indicator${check?.error_code && connected ? ' needs-attention' : ''}`} disabled={sources === null} onClick={openSource} aria-describedby="screening-import-help">
          <span className={`screening-import-dot${connected ? ' is-connected' : ''}`} aria-hidden="true" />
          {connected ? check?.error_code ? 'Import needs attention' : 'Auto-import · 1 min' : sources === null ? 'Checking source…' : 'Connect source'}
        </button>
        <span id="screening-import-help" className="screening-import-tooltip" role="tooltip">
          {connected ? 'Checks the Sheet and CV folder every minute, even with this page closed.' : 'Connect a response Sheet and CV folder to import applications.'}
          {connected && lastImportCheck && <span>Last checked: {lastImportCheck} (Bangladesh time)</span>}
          <span>Click for source details.</span>
        </span>
      </div>}
      <label>Job<select aria-label="Job" value={jobId} disabled={picker.loading} onChange={event => setParams({ job: event.target.value })}>{!job && <option value="">Select a job</option>}{picker.jobs.map(item => <option value={item.id} key={item.id}>{item.title} (JD {item.number})</option>)}</select></label>
    </div></header>
    {(picker.error || sourceError) && <div className="screening-notice is-danger" role="alert"><p>{picker.error || sourceError}</p><button className="button-secondary" onClick={() => { picker.retry(); setSourceRevision(value => value + 1) }}>Try again</button></div>}
    {checksError && <p className="muted" role="status">Source notifications could not be refreshed. {checksError}</p>}
    {picker.loading && <div className="ui-state" role="status">Loading jobs…</div>}
    {!picker.loading && !picker.error && !job && <div className="ui-state"><h3>No approved jobs yet</h3><p>Create and approve a job description before screening candidates.</p></div>}
    {job && <>
      {connected && check?.error_code && <p className="screening-notice is-warning" role="status">{importError}</p>}
      <ScreeningWorkspace key={job.id} token={token} jobId={job.id} jobTitle={job.title} applications={current?.applications ?? []} applicationsLoading={!current} applicationsError={current?.error ?? ''} connected={connected} sourceReady={sources !== null} onShortlist={() => setParams(current => { const next = new URLSearchParams(current); next.set('job', jobId); next.set('shortlist', '1'); return next })} onSource={openSource} onReload={() => setRevision(value => value + 1)} />
    </>}
    <dialog className="screening-dialog" ref={sourceDialog} aria-label="Application source setup"><header><h2>Source setup</h2><button className="button-secondary" onClick={() => sourceDialog.current?.close()}>Close</button></header>{job && sourceJob === jobId && <>{!connected ? <ConnectionForm key={jobId} token={token} job={job} source={source} onLinked={linked} /> : <><p className="muted">The response Sheet and CV folder are connected for {job.title}.</p><div className="screening-source-links"><a className="text-link" href={`https://drive.google.com/drive/folders/${encodeURIComponent(source!.drive_folder_id)}`} target="_blank" rel="noopener noreferrer">Open CV folder ↗</a><a className="text-link" href={`https://docs.google.com/spreadsheets/d/${encodeURIComponent(source!.spreadsheet_id!)}/edit#gid=${source!.sheet_tab_id}`} target="_blank" rel="noopener noreferrer">Open response Sheet ↗</a></div><p className="muted">Required response columns: Name, Email, CV File and Image. Timestamp is optional; extra fields are ignored.</p><p className="muted">New applications are imported every minute. Use Screen applications to parse their CVs, match the criteria and calculate scores in one action.</p></>}</>}</dialog>
    <dialog className="screening-dialog is-wide" ref={shortlistDialog} aria-label="Interview shortlist" onKeyDown={event => { if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); closeShortlist() } }} onCancel={event => { event.preventDefault(); closeShortlist() }} onClose={closeShortlist}><header><h2>Interview shortlist</h2><button className="button-secondary" onClick={closeShortlist}>Close</button></header>{job && shortlistOpen && <ShortlistWorkspace key={job.id} token={token} job={job} />}</dialog>
  </section>
}
