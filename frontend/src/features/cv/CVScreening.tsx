import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { ApiError } from '../../services/api'
import { useSearchParams } from 'react-router-dom'
import { loadRecruitmentJobs } from '../interviews/useRecruitmentJob'
import ShortlistWorkspace from '../interviews/ShortlistWorkspace'
import type { JobDescriptionSummary } from '../jd/api'
import {
  fetchSource, linkSheet, linkSource, listApplications, listSources,
  parseFolder, parseSheet,
} from './api'
import type { CandidateApplication, CVSource, CVSourceCheck, FetchResult } from './api'
import './CVScreening.css'
import { CVParseControl } from './CVParsing'
import CVResults from './CVResults'

function errorMessage(error: unknown) {
  if (error instanceof ApiError && error.status === 401) return 'Your session expired. Sign out and sign in again.'
  return error instanceof Error ? error.message : 'Something went wrong. Please try again.'
}

function FetchSummary({ result }: { result: FetchResult }) {
  const errors = [
    ...result.errors.map((error) => `${error.filename ?? 'CV file'}: ${error.error_code}`),
    ...(result.sheet?.errors ?? []).map((error) => `Sheet row ${error.row_number ?? '?'}: ${error.error_code}`),
  ]
  return (
    <div className="cv-fetch-summary" role="status">
      <strong>Fetch completed</strong>
      <p>{result.downloaded} downloaded · {result.skipped} skipped · {result.failed} failed</p>
      {result.sheet && <p>{result.sheet.rows_imported} responses imported · {result.sheet.rows_skipped} skipped · {result.sheet.rows_failed} failed</p>}
      {errors.length > 0 && <ul>{errors.map((error, index) => <li key={`${index}:${error}`}>{error}</li>)}</ul>}
    </div>
  )
}

function ConnectionForm({ token, job, source, onLinked }: {
  token: string
  job: JobDescriptionSummary
  source?: CVSource
  onLinked: (source: CVSource) => void
}) {
  const [folder, setFolder] = useState('')
  const [sheet, setSheet] = useState('')
  const [tab, setTab] = useState('')
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
        <p>Share the response Sheet and uploaded CV folder with your service account.</p>
      </div>
      {!source && <label>CV upload folder URL or ID
        <input required value={folder} onChange={(event) => setFolder(event.target.value)} placeholder="https://drive.google.com/drive/folders/…" disabled={saving} />
        <span className="cv-hint">Use the Form’s upload folder inside your configured CV parent folder.</span>
      </label>}
      {source && <p className="cv-hint">CV folder is linked. Complete the response Sheet connection below.</p>}
      <div className="cv-connect-fields">
        <label>Response Sheet URL or ID
          <input required value={sheet} onChange={(event) => setSheet(event.target.value)} placeholder="https://docs.google.com/spreadsheets/d/…/edit#gid=…" disabled={saving} />
        </label>
        <label>Response tab ID (gid)
          <input value={tab} onChange={(event) => setTab(event.target.value)} placeholder="Optional when URL has gid" inputMode="numeric" disabled={saving} />
        </label>
      </div>
      <p className="cv-hint">The response tab needs Name, Email and CV File columns. Timestamp is optional.</p>
      {error && <p className="cv-error" role="alert">{error}</p>}
      <button className="button-primary" disabled={saving}>{saving ? 'Connecting…' : 'Connect responses'}</button>
    </form>
  )
}

function CandidateTable({ applications, token }: { applications: CandidateApplication[]; token: string }) {
  const [page, setPage] = useState(0)
  const safePage = Math.min(page, Math.max(0, Math.ceil(applications.length / 10) - 1))
  const start = safePage * 10
  const visible = applications.slice(start, start + 10)
  if (applications.length === 0) return <p className="cv-empty">No candidates imported yet. Connect the response Sheet, then click Fetch CVs.</p>
  return (
    <>
      <div className="cv-table-scroll">
        <table className="cv-candidate-table">
          <thead><tr><th scope="col">Candidate</th><th scope="col">CV file</th><th scope="col">Submitted</th><th scope="col">Download status</th><th scope="col">Parsing</th></tr></thead>
          <tbody>{visible.map((application) => <tr key={application.id}>
            <td><strong>{application.full_name}</strong><span className="cv-email">{application.email}</span></td>
            <td>{application.cv_documents.length === 0 ? 'No linked CV' : application.cv_documents.map((cv) => (
              <a className="cv-file" key={cv.id} href={`https://drive.google.com/file/d/${encodeURIComponent(cv.drive_file_id)}/view`} target="_blank" rel="noopener noreferrer">
                <span className="cv-file-icon" aria-hidden="true">CV</span>{cv.original_filename ?? 'CV document'}<span className="cv-sr-only"> (opens in Google Drive)</span>
              </a>
            ))}</td>
            <td className="cv-submitted">{application.submitted_at_text || 'Not provided'}</td>
            <td>{application.cv_documents.length === 0 ? '—' : application.cv_documents.map((cv) => (
              <span key={cv.id} className={`cv-status${cv.download_status === 'downloaded' ? ' cv-status-success' : cv.download_status === 'failed' ? ' cv-status-failed' : ''}`}>
                {cv.download_status ?? 'Unknown'}
              </span>
            ))}</td>
            <td>{application.cv_documents.map(cv => cv.file_version_id && <CVParseControl readOnly key={cv.file_version_id} token={token} versionId={cv.file_version_id} filename={cv.original_filename ?? 'CV document'} enabled={cv.download_status === 'downloaded'} />)}</td>
          </tr>)}</tbody>
        </table>
      </div>
      <div className="cv-candidate-pagination">
        <span>Showing {start + 1}–{Math.min(start + 10, applications.length)} of {applications.length} candidates</span>
        {applications.length > 10 && <label>Candidate page <select value={safePage} onChange={event => setPage(Number(event.target.value))}>
          {Array.from({ length: Math.ceil(applications.length / 10) }, (_, i) => <option key={i} value={i}>{i + 1}</option>)}
        </select></label>}
      </div>
    </>
  )
}

type CandidatesState = { jobId: string; applications: CandidateApplication[]; loading: boolean; error: string }

export default function CVScreening({ token, sourceChecks, checksError, refreshChecks }: {
  token: string; sourceChecks: CVSourceCheck[]; checksError: string; refreshChecks: () => void
}) {
  const [searchParams, setSearchParams] = useSearchParams()
  const [offset, setOffset] = useState(0)
  const activeId = searchParams.get('job') ?? ''
  const [page, setPage] = useState<{ key: string; jobs: JobDescriptionSummary[]; sources: CVSource[]; error: string } | null>(null)
  const [candidates, setCandidates] = useState<CandidatesState | null>(null)
  const [candidateRefresh, setCandidateRefresh] = useState(0)
  const [fetching, setFetching] = useState(false)
  const [feedback, setFeedback] = useState<{ jobId: string; result?: FetchResult; error?: string; time?: string } | null>(null)
  const fetchLock = useRef(false)
  const key = `${offset}`
  const ready = page?.key === key
  const allJobs = ready ? page.jobs : []
  const requestedIndex = allJobs.findIndex(job => job.id === activeId)
  const effectiveOffset = requestedIndex >= 0 ? Math.floor(requestedIndex / 10) * 10 : offset
  const jobs = allJobs.slice(effectiveOffset, effectiveOffset + 10)
  const sources = ready ? page.sources : []
  const activeJob = jobs.find((job) => job.id === activeId)
  const jobId = activeJob?.id ?? ''
  const activeSource = sources.find((source) => source.job_description_id === jobId)
  const activeCheck = sourceChecks.find(item => item.job_description_id === jobId)
  const otherNewChecks = sourceChecks.filter(item => item.job_description_id !== jobId && item.new_cv_count > 0)
  const connected = activeSource?.spreadsheet_id != null && activeSource.sheet_tab_id != null
  const applications = candidates?.jobId === jobId ? candidates.applications : []
  const candidatesLoading = !!jobId && (candidates?.jobId !== jobId || candidates.loading)

  useEffect(() => {
    const controller = new AbortController()
    void Promise.all([loadRecruitmentJobs(token, controller.signal), listSources(token, controller.signal)])
      .then(([descriptions, nextSources]) => {
        if (!controller.signal.aborted) setPage({ key, jobs: descriptions, sources: nextSources, error: '' })
      })
      .catch((caught: unknown) => {
        if (!controller.signal.aborted) setPage({ key, jobs: [], sources: [], error: errorMessage(caught) })
      })
    return () => controller.abort()
  }, [token, offset, key])

  useEffect(() => {
    if (!jobId) return
    const controller = new AbortController()
    void (async () => {
      setCandidates({ jobId, applications: [], loading: true, error: '' })
      try {
        const next = await listApplications(token, jobId, controller.signal)
        if (!controller.signal.aborted) setCandidates({ jobId, applications: next, loading: false, error: '' })
      } catch (caught) {
        if (!controller.signal.aborted) setCandidates({ jobId, applications: [], loading: false, error: errorMessage(caught) })
      }
    })()
    return () => controller.abort()
  }, [token, jobId, candidateRefresh, key])

  function handleLinked(source: CVSource) {
    setPage((current) => current && ({ ...current, sources: [...current.sources.filter((item) => item.job_description_id !== source.job_description_id), source] }))
    refreshChecks()
  }

  async function handleFetch() {
    if (!activeSource || !connected || fetchLock.current) return
    fetchLock.current = true
    setFetching(true)
    setFeedback(null)
    try {
      const result = await fetchSource(token, activeSource.id)
      setFeedback({ jobId, result, time: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) })
    } catch (caught) {
      setFeedback({ jobId, error: errorMessage(caught) })
    } finally {
      setCandidateRefresh((value) => value + 1)
      refreshChecks()
      fetchLock.current = false
      setFetching(false)
    }
  }

  return (
    <section className="cv-page">
      <header className="cv-page-header">
        <div><p className="workspace-eyebrow">Recruitment workspace</p><h1>CV Screening</h1>
          <p className="cv-description">Connect form responses and view candidates for each job.</p></div>
        <div className="cv-fetch-action"><button className="button-primary" onClick={() => void handleFetch()} disabled={!ready || !connected || fetching}>
          {fetching ? 'Fetching CVs…' : 'Fetch CVs'}</button>
          <span>{activeJob ? `For: ${activeJob.title}` : 'Select a job to get started'}</span>
          {feedback?.jobId === jobId && feedback.time && <span>Last fetched at {feedback.time}</span>}
        </div>
      </header>
      {checksError && <p className="cv-check-warning" role="status">CV notifications could not be refreshed. {checksError}</p>}
      {activeSource && <section className={`cv-source-notification${activeCheck?.error_code ? ' has-error' : ''}`} aria-label="CV source monitoring" aria-live="polite">
        <div><strong>{activeCheck?.new_cv_count ? `${activeCheck.new_cv_count} new CV${activeCheck.new_cv_count === 1 ? '' : 's'} available` : activeCheck?.error_code ? 'The last source check failed' : 'Connected CV sources are checked every 5 minutes'}</strong>
          <p>{activeCheck?.error_code ? 'Check the Drive folder, response Sheet and service-account access. We will retry automatically.' : activeCheck?.last_success_at ? `Last checked ${new Date(activeCheck.last_success_at).toLocaleString('en-GB', { timeZone: 'Asia/Dhaka' })} · Checks continue while this page is closed.` : 'The first check is pending. Checks continue while this page is closed.'}</p>
          {!!activeCheck?.new_cv_count && activeCheck.error_code && <p>The available count is from the last successful check.</p>}
        </div>{!!activeCheck?.new_cv_count && <button className="button-primary" disabled={!connected || fetching} onClick={() => void handleFetch()}>{fetching ? 'Fetching CVs…' : 'Fetch new CVs'}</button>}
      </section>}
      {!!otherNewChecks.length && <div className="cv-other-notifications"><strong>New CVs for other jobs</strong>{otherNewChecks.map(check => <button key={check.source_id} disabled={fetching} onClick={() => setSearchParams({ job: check.job_description_id })}>{allJobs.find(item => item.id === check.job_description_id)?.title ?? 'View job'} · {check.new_cv_count} new</button>)}</div>}
      <div className="cv-list-toolbar"><span>Job descriptions</span></div>
      {!ready && <p className="cv-empty" role="status">Loading jobs…</p>}
      {ready && page.error && <p className="cv-error" role="alert">{page.error}</p>}
      {ready && !page.error && jobs.length === 0 && <p className="cv-empty">No saved job descriptions. Create a job description first.</p>}
      <div className="cv-job-list">{jobs.map((job) => {
        const expanded = job.id === jobId
        const source = sources.find((item) => item.job_description_id === job.id)
        const hasSheet = source?.spreadsheet_id != null && source.sheet_tab_id != null
        return <article key={job.id} className={`cv-job-card${expanded ? ' is-expanded' : ''}`}>
          <h2><a href={`#cv-job-${job.id}`} className="cv-job-toggle" aria-expanded={expanded} aria-controls={`cv-job-${job.id}`} onClick={event => {
            event.preventDefault()
            if (fetching) return
            setSearchParams(current => {
              const next = new URLSearchParams(current)
              if (expanded) next.delete('job')
              else next.set('job', job.id)
              return next
            })
          }}>
            <span className="cv-job-number">JD {job.number}</span><span className="cv-job-heading"><span>{job.title}</span>
              {!!sourceChecks.find(item => item.job_description_id === job.id)?.new_cv_count && <span className="cv-new-badge">{sourceChecks.find(item => item.job_description_id === job.id)!.new_cv_count} new CVs</span>}
              <small>{expanded && !candidatesLoading && !candidates?.error ? `${applications.length} candidates · ${applications.reduce((count, application) => count + application.cv_documents.length, 0)} CVs` : hasSheet ? 'Responses connected' : 'Connect responses to import candidates'}</small></span>
            <span className="cv-chevron" aria-hidden="true">{expanded ? '⌃' : '⌄'}</span>
          </a></h2>
          {expanded && <div id={`cv-job-${job.id}`} className="cv-job-body">
            {!hasSheet && <ConnectionForm key={`connection-${job.id}`} token={token} job={job} source={source} onLinked={handleLinked} />}
            {feedback?.jobId === job.id && feedback.error && <p className="cv-error" role="alert">{feedback.error}</p>}
            {feedback?.jobId === job.id && feedback.result && <FetchSummary result={feedback.result} />}
            <CVResults key={`screening-${job.id}`} token={token} jobId={job.id} />
            <section id="interview-shortlist" className="cv-shortlist" aria-label="Interview shortlist"><h3>Interview shortlist</h3><p>Review screened candidates and save the recipients you want to invite.</p><ShortlistWorkspace key={`shortlist-${job.id}-${candidateRefresh}`} token={token} job={job} /></section>
            {candidatesLoading ? <p className="cv-empty" role="status">Loading candidates…</p> : candidates?.error ? <p className="cv-error" role="alert">{candidates.error}</p> : <CandidateTable key={`candidates-${job.id}-${candidateRefresh}`} token={token} applications={applications} />}
            {source && <footer className="cv-job-footer"><a href={`https://drive.google.com/drive/folders/${encodeURIComponent(source.drive_folder_id)}`} target="_blank" rel="noopener noreferrer">Open Drive folder ↗</a>
              {hasSheet && <a href={`https://docs.google.com/spreadsheets/d/${encodeURIComponent(source.spreadsheet_id!)}/edit#gid=${source.sheet_tab_id}`} target="_blank" rel="noopener noreferrer">Open response Sheet ↗</a>}</footer>}
          </div>}
        </article>
      })}</div>
      {ready && <nav className="cv-jobs-pagination" aria-label="Job pages">
        {effectiveOffset > 0 && <a href="#previous-jobs" onClick={event => { event.preventDefault(); if (!fetching) { setSearchParams({}); setOffset(Math.max(0, effectiveOffset - 10)) } }}>Previous jobs</a>}
        <span>Job page {Math.floor(effectiveOffset / 10) + 1}</span>
        {effectiveOffset + 10 < allJobs.length && <a href="#next-jobs" onClick={event => { event.preventDefault(); if (!fetching) { setSearchParams({}); setOffset(effectiveOffset + 10) } }}>Next jobs</a>}
      </nav>}
    </section>
  )
}
