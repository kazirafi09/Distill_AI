import { useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useRecruitmentJob } from '../interviews/useRecruitmentJob'
import ShortlistWorkspace from '../interviews/ShortlistWorkspace'
import { fetchSource, listApplications, listSources } from './api'
import type { CandidateApplication, CVSource, CVSourceCheck, FetchResult } from './api'
import { CVBatchParse } from './CVParsing'
import ConnectionForm from './CVSourceSetup'
import ScreeningWorkspace from './ScreeningWorkspace'
import './CVScreening.css'
import '../jd/JobDescriptions.css'
import './ScreeningWorkspace.css'

export default function CVScreening({ token, sourceChecks, checksError, refreshChecks }: {
  token: string; sourceChecks: CVSourceCheck[]; checksError: string; refreshChecks: () => void
}) {
  const picker = useRecruitmentJob(token)
  const [, setParams] = useSearchParams()
  const [sources, setSources] = useState<CVSource[] | null>(null)
  const [sourceError, setSourceError] = useState('')
  const [revision, setRevision] = useState(0)
  const [sourceRevision, setSourceRevision] = useState(0)
  const [candidates, setCandidates] = useState<{ jobId: string; revision: number; applications: CandidateApplication[]; error: string } | null>(null)
  const [fetching, setFetching] = useState(false)
  const [feedback, setFeedback] = useState<{ jobId: string; result?: FetchResult; error?: string } | null>(null)
  const [sourceJob, setSourceJob] = useState<string | null>(null)
  const sourceDialog = useRef<HTMLDialogElement>(null)
  const shortlistDialog = useRef<HTMLDialogElement>(null)
  const [shortlistOpen, setShortlistOpen] = useState(false)
  const fetchLock = useRef(false)
  const job = picker.job
  const jobId = job?.id ?? ''
  const source = sources?.find(item => item.job_description_id === jobId)
  const connected = !!source && source.spreadsheet_id != null && source.sheet_tab_id != null
  const check = sourceChecks.find(item => item.job_description_id === jobId)
  const current = candidates?.jobId === jobId && candidates.revision === revision ? candidates : null

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
  }, [token, jobId, revision])

  async function sync() {
    if (!source || !connected || fetchLock.current) return
    fetchLock.current = true; setFetching(true); setFeedback(null)
    try { setFeedback({ jobId, result: await fetchSource(token, source.id) }) }
    catch (error) { setFeedback({ jobId, error: error instanceof Error ? error.message : 'Application sync failed.' }) }
    finally { setRevision(value => value + 1); refreshChecks(); fetchLock.current = false; setFetching(false) }
  }
  function openSource() { setSourceJob(jobId); sourceDialog.current?.showModal() }
  function linked(next: CVSource) { setSources(current => [...(current ?? []).filter(item => item.id !== next.id), next]); refreshChecks() }

  return <section className="cv-redesign">
    <header className="redesign-header"><div><p className="workspace-eyebrow">Recruitment workspace</p><h1>CV Screening</h1><p className="muted">Review candidates against approved job criteria.</p></div><div className="screening-header-actions"><label>Job<select value={jobId} disabled={picker.loading || fetching} onChange={event => setParams({ job: event.target.value })}>{!job && <option value="">Select a job</option>}{picker.jobs.map(item => <option value={item.id} key={item.id}>{item.title} (JD {item.number})</option>)}</select></label></div></header>
    {(picker.error || sourceError) && <div className="screening-notice is-danger" role="alert"><p>{picker.error || sourceError}</p><button className="button-secondary" onClick={() => { picker.retry(); setSourceRevision(value => value + 1) }}>Try again</button></div>}
    {checksError && <p className="muted" role="status">Source notifications could not be refreshed. {checksError}</p>}
    {picker.loading && <div className="ui-state" role="status">Loading jobs…</div>}
    {!picker.loading && !picker.error && !job && <div className="ui-state"><h3>No approved jobs yet</h3><p>Create and approve a job description before screening candidates.</p></div>}
    {job && <>
      {(!connected || !!check?.new_cv_count || !!check?.error_code) && <div className="screening-sync-bar"><div><strong>{check?.new_cv_count ? `${check.new_cv_count} new source files available to sync` : connected ? 'Application source connected' : sources === null ? 'Checking application source…' : 'Connect an application source'}</strong><p className="muted">{check?.error_code ? 'The last source check failed. Review source setup and access permissions.' : check?.last_success_at ? `Last checked ${new Date(check.last_success_at).toLocaleString('en-GB', { timeZone: 'Asia/Dhaka' })}` : connected ? 'Sync applications before screening newly submitted CVs.' : 'Connect the response Sheet and uploaded CV folder.'}</p></div></div>}
      {feedback?.jobId === jobId && feedback.error && <div className="screening-notice is-danger" role="alert">{feedback.error}</div>}
      {feedback?.jobId === jobId && feedback.result && <div className="screening-notice" role="status"><strong>Sync completed</strong><p>{feedback.result.downloaded} files downloaded · {feedback.result.skipped} skipped · {feedback.result.failed} failed{feedback.result.sheet ? ` · ${feedback.result.sheet.rows_imported} responses imported` : ''}</p>{feedback.result.errors.map((error, index) => <p key={index}>{error.filename ?? 'CV file'}: {error.error_code.replaceAll('_', ' ').toLowerCase()}</p>)}{feedback.result.sheet?.errors.map((error, index) => <p key={`sheet-${index}`}>Sheet row {error.row_number ?? '?'}: {error.error_code.replaceAll('_', ' ').toLowerCase()}</p>)}</div>}
      <ScreeningWorkspace key={job.id} token={token} jobId={job.id} jobTitle={job.title} applications={current?.applications ?? []} applicationsLoading={!current} applicationsError={current?.error ?? ''} connected={connected} syncing={fetching} sourceReady={sources !== null} onShortlist={() => { setShortlistOpen(true); shortlistDialog.current?.showModal() }} onSync={() => void sync()} onSource={openSource} onReload={() => setRevision(value => value + 1)} />
    </>}
    <dialog className="screening-dialog" ref={sourceDialog} aria-label="Application source setup"><header><h2>Source setup</h2><button className="button-secondary" onClick={() => sourceDialog.current?.close()}>Close</button></header>{job && sourceJob === jobId && <>{!connected ? <ConnectionForm key={jobId} token={token} job={job} source={source} onLinked={linked} /> : <><p className="muted">The response Sheet and CV folder are connected for {job.title}.</p><div className="screening-source-links"><a className="text-link" href={`https://drive.google.com/drive/folders/${encodeURIComponent(source!.drive_folder_id)}`} target="_blank" rel="noopener noreferrer">Open CV folder ↗</a><a className="text-link" href={`https://docs.google.com/spreadsheets/d/${encodeURIComponent(source!.spreadsheet_id!)}/edit#gid=${source!.sheet_tab_id}`} target="_blank" rel="noopener noreferrer">Open response Sheet ↗</a></div><CVBatchParse key={jobId} token={token} jobId={jobId} onFinished={() => setRevision(value => value + 1)} /></>}</>}</dialog>
    <dialog className="screening-dialog is-wide" ref={shortlistDialog} aria-label="Interview shortlist" onClose={() => setShortlistOpen(false)}><header><h2>Interview shortlist</h2><button className="button-secondary" onClick={() => shortlistDialog.current?.close()}>Close</button></header>{job && shortlistOpen && <ShortlistWorkspace key={job.id} token={token} job={job} />}</dialog>
  </section>
}
