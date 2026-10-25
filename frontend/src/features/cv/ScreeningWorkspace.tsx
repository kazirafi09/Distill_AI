import { useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { apiRequest } from '../../services/api'
import { CandidateFiles, ScoreDetails } from './CandidateSource'
import CandidateProfile from './CandidateProfile'
import ScreeningProgress from './ScreeningProgress'
import { errorLabels } from './screeningMessages'
import type { Assessment, Results, Run } from './CVResults'
import type { CandidateApplication } from './api'
import { candidateStatus, evidenceLink, reviewCount, sourceLabel, validScore } from './screeningView'

function ScoreCard({ item }: { item?: Assessment }) {
  const score = validScore(item)
  return <div className="screening-score"><span>Match score</span><strong>{score === null ? '—' : Math.round(score)}{score !== null && <small> / 100</small>}</strong>{item?.outdated && <small>Older result</small>}</div>
}

function Requirements({ application, item, profile }: { application: CandidateApplication; item?: Assessment; profile: Results['profile'] }) {
  const criteria = profile?.criteria ?? []
  if (!item?.matches.length) return <div className="ui-state"><h3>No requirement evidence yet</h3><p>{item?.error_code ? errorLabels[item.error_code] ?? item.error_code.replaceAll('_', ' ').toLowerCase() : 'Screen this application to see its requirement evidence.'}</p></div>
  const groups = [
    { title: 'Essential criteria · needs review first', preferred: false },
    { title: 'Preferred criteria', preferred: true },
  ]
  return <>{groups.map(group => {
    const matches = item.matches.filter(match => (criteria.find(criterion => criterion.id === match.requirement_id)?.priority === 'Preferred') === group.preferred)
      .sort((a, b) => Number(a.decision === 'met') - Number(b.decision === 'met'))
    if (!matches.length) return null
    return <section className="screening-requirement-group" key={group.title}><h3>{group.title}</h3>{matches.map(match => {
      const criterion = criteria.find(criterion => criterion.id === match.requirement_id)
      const needsReview = !group.preferred && match.decision !== 'met'
      const badge = match.decision === 'met' ? 'Evidenced' : needsReview ? 'Needs your review' : match.decision === 'insufficient_evidence' ? 'No evidence found' : 'Partial or missing evidence'
      return <article id={`requirement-${match.requirement_id}`} className={`screening-requirement${needsReview ? ' needs-review' : ''}`} key={match.requirement_id}>
        <header><h4>{criterion?.text ?? match.requirement_id}</h4><span className={`ui-badge ${match.decision === 'met' ? 'is-success' : needsReview ? 'is-warning' : ''}`}>{badge}</span></header>
        <p>{match.justification}</p>
        {!!match.unsupported_requirement_parts?.length && <p className="muted">Not established: {match.unsupported_requirement_parts.join('; ')}</p>}
        {!match.evidence.length && <p className="muted">No supporting excerpt found. This does not establish inability.</p>}
        {match.evidence.map((evidence, index) => <div key={`${evidence.source_id}:${index}`}><blockquote>“{evidence.quote}”</blockquote><footer><span>{sourceLabel(evidence.source_id)}{evidence.ocr_derived ? ' · OCR' : ''}</span>{evidenceLink(application, evidence) && <a href={evidenceLink(application, evidence)!} target="_blank" rel="noopener noreferrer">Review evidence in the CV ↗</a>}</footer></div>)}
      </article>
    })}</section>
  })}</>
}

function CandidateDetail({ token, jobTitle, application, item, results, opened, onBack }: { token: string; jobTitle: string; application: CandidateApplication; item?: Assessment; results: Results; opened: boolean; onBack: () => void }) {
  const [tab, setTab] = useState('requirements')
  const [profileFileId, setProfileFileId] = useState('')
  const heading = useRef<HTMLHeadingElement>(null)
  useEffect(() => {
    if (opened && window.matchMedia('(max-width: 700px)').matches) {
      heading.current?.focus({ preventScroll: true })
      document.querySelector('.workspace-main')?.scrollTo({ top: 0 })
    }
  }, [opened])
  const status = candidateStatus(application, item)
  const count = reviewCount(item)
  return <section className="screening-candidate-detail" aria-label={`Candidate details for ${application.full_name}`}>
    <button className="screening-back text-link" onClick={onBack}>‹ Back to candidates (filters kept)</button>
    <p className="screening-mobile-context muted">{jobTitle}{results.run && ` · criteria v${results.run.profile_version}`}</p>
    <header className="screening-detail-heading"><div><h2 ref={heading} tabIndex={-1}>{application.full_name}</h2><p className="muted">{application.email}{application.email && ' · '}Applied {new Date(application.created_at).toLocaleDateString('en-GB', { timeZone: 'Asia/Dhaka', day: 'numeric', month: 'short' })}</p></div>{evidenceLink(application) && <a className="button-secondary" href={evidenceLink(application)!} target="_blank" rel="noopener noreferrer">Open CV file ↗</a>}</header>
    {tab === 'requirements' && <div className="screening-score-explanation"><ScoreCard item={item} /><p>How much of the approved criteria the CV documents. This is not a hiring recommendation or a prediction. Missing evidence does not mean the candidate cannot do it. Nothing is rejected automatically.</p></div>}
    <div className="screening-detail-status">{tab !== 'requirements' && <span className="screening-score-inline">Match score: {validScore(item) === null ? 'Not scored' : `${Math.round(validScore(item)!)} / 100`}</span>}<span className={`ui-badge ${status.tone}`}>{status.label}</span>{count > 0 && <span className="ui-badge is-warning">{count} essential {count === 1 ? 'criterion needs' : 'criteria need'} review</span>}</div>
    <nav className="redesign-tabs" aria-label="Candidate sections">{[['requirements', 'Requirements'], ['profile', 'Profile'], ['score', 'Score details'], ['files', 'CV files']].map(([value, label]) => <button key={value} aria-current={tab === value ? 'page' : undefined} onClick={() => setTab(value)}>{label}</button>)}</nav>
    <div className="screening-detail-scroll" key={tab}>
      {item?.error_code && <div className="screening-notice is-warning" role="status"><p>{errorLabels[item.error_code] ?? item.error_code.replaceAll('_', ' ').toLowerCase()}</p>{item.warnings.filter(warning => warning in errorLabels).map(warning => <p key={warning}>{errorLabels[warning]}</p>)}</div>}
      {item?.outdated && <div className="screening-notice is-info"><strong>These scores use older inputs</strong><p>Screen again to calculate results with the current criteria and CV files. The saved evidence below belongs to the earlier run.</p></div>}
      {tab === 'requirements' && <Requirements application={application} item={item} profile={results.profile} />}
      {tab === 'profile' && <CandidateProfile token={token} application={application} initialFileId={profileFileId} onSource={() => setTab('files')} />}
      {tab === 'score' && <ScoreDetails item={item} run={results.run} onRequirements={() => setTab('requirements')} />}
      {tab === 'files' && <CandidateFiles token={token} application={application} onProfile={id => { setProfileFileId(id); setTab('profile') }} />}
    </div>
  </section>
}

export default function ScreeningWorkspace({ token, jobId, jobTitle, applications, applicationsLoading, applicationsError, connected, sourceReady, onSource, onShortlist, onReload }: {
  token: string; jobId: string; jobTitle: string; applications: CandidateApplication[]; applicationsLoading: boolean; applicationsError: string; connected: boolean; sourceReady: boolean; onSource: () => void; onShortlist: () => void; onReload: () => void
}) {
  const [params, setParams] = useSearchParams()
  const [results, setResults] = useState<Results | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [revision, setRevision] = useState(0)
  const [lastChecked, setLastChecked] = useState('')
  const lock = useRef(false)
  const requestVersion = useRef(0)
  const listRef = useRef<HTMLDivElement>(null)
  const returnFocus = useRef<HTMLButtonElement | null>(null)
  const query = params.get('search') ?? ''
  const show = params.get('show') ?? 'all'
  const sort = params.get('sort') ?? 'score'
  const onlyReview = params.get('review') === '1'
  const requestedId = params.get('candidate')
  const run = results?.run
  const active = !!run && ['queued', 'running'].includes(run.status)

  useEffect(() => {
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout>
    async function poll() {
      const version = requestVersion.current
      try {
        const next = await apiRequest<Results>(`/cv/screening/jobs/${jobId}/results`, { token, signal: controller.signal })
        if (!controller.signal.aborted) {
          if (version === requestVersion.current) { setResults(next); setLastChecked(new Date().toISOString()); setError('') }
          timer = setTimeout(() => void poll(), next.run && ['queued', 'running'].includes(next.run.status) ? 3000 : 10000)
        }
      } catch (caught) {
        if (!controller.signal.aborted) {
          if (version === requestVersion.current) setError(caught instanceof Error ? caught.message : 'Could not load screening results.')
          timer = setTimeout(() => void poll(), 10000)
        }
      }
    }
    void poll()
    return () => { controller.abort(); clearTimeout(timer) }
  }, [token, jobId, revision])

  async function screen(resume = false) {
    if (lock.current || active) return
    requestVersion.current += 1
    lock.current = true; setBusy(true); setError('')
    try {
      const started = await apiRequest<Run>(resume && run ? `/cv/screening/runs/${run.id}/resume` : `/cv/screening/jobs/${jobId}/start`, { token, method: 'POST' })
      setResults(current => current ? { ...current, run: started } : current)
      setRevision(value => value + 1)
    } catch (caught) { setError(caught instanceof Error ? caught.message : 'Screening could not start.') }
    finally { lock.current = false; setBusy(false) }
  }

  function filter(name: string, value: string) {
    setParams(current => { const next = new URLSearchParams(current); if (value) next.set(name, value); else next.delete(name); next.delete('candidate'); return next }, { replace: true })
  }
  function back() {
    setParams(current => { const next = new URLSearchParams(current); next.delete('candidate'); return next }, { replace: true })
    requestAnimationFrame(() => returnFocus.current?.focus())
  }
  function showGroup(group: 'issue' | 'review') {
    setParams(current => {
      const next = new URLSearchParams(current)
      ;['search', 'show', 'review', 'candidate'].forEach(key => next.delete(key))
      next.set(group === 'issue' ? 'show' : 'review', group === 'issue' ? 'issue' : '1')
      return next
    }, { replace: true })
  }
  const assessments = new Map([...(results?.ranked ?? []), ...(results?.incomplete ?? [])].map(item => [item.application_id, item]))
  const rows = applications.map(application => ({ application, item: assessments.get(application.id), status: candidateStatus(application, assessments.get(application.id)) }))
  const visible = rows.filter(row => `${row.application.full_name} ${row.application.email}`.toLowerCase().includes(query.toLowerCase()) && (show === 'all' || row.status.key === show) && (!onlyReview || reviewCount(row.item) > 0)).sort((a, b) => sort === 'name' ? a.application.full_name.localeCompare(b.application.full_name) : sort === 'newest' ? Date.parse(b.application.created_at) - Date.parse(a.application.created_at) : (validScore(b.item) ?? -1) - (validScore(a.item) ?? -1))
  const selected = visible.find(row => row.application.id === requestedId) ?? visible[0]
  const scored = rows.filter(row => row.status.key === 'screened').length
  const issues = rows.filter(row => row.status.key === 'issue').length
  const needsReview = rows.filter(row => reviewCount(row.item) > 0).length
  const loading = applicationsLoading || (!results && !error)
  const resume = !!run && ['failed', 'completed_with_issues'].includes(run.status)

  return <div className={`screening-workspace${requestedId && selected?.application.id === requestedId ? ' has-mobile-selection' : ''}`}>
    <div className="screening-workflow-toolbar" aria-label="Screening actions">
      <div className="screening-action-group"><span>Application source</span><div><button className="button-secondary" disabled={!sourceReady} onClick={onSource}>Source setup</button></div></div>
      <div className="screening-action-group"><span>Parse CVs and screen</span><div><button className="button-secondary" disabled={applicationsLoading || !!applicationsError || !applications.length} onClick={onShortlist}>Interview shortlist</button><button className="button-primary screening-start-button" data-motion-processing={active || busy} aria-busy={active || busy} disabled={!results || !connected || busy || active || !applications.length} onClick={() => void screen(resume)}>{busy ? 'Starting…' : active ? 'Screening…' : resume ? 'Resume screening' : 'Screen applications'}</button></div></div>
    </div>
    <ScreeningProgress run={run} starting={busy} lastChecked={lastChecked} refreshError={!!error} />
    <div className="screening-summary" aria-label="Screening summary">
      <div><strong>{applicationsLoading ? '—' : applications.length}</strong><span>Applications</span></div><div><strong>{loading ? '—' : scored}</strong><span>Scored</span></div>
      <div className={issues > 0 ? 'has-issues' : ''}><strong>{loading ? '—' : issues}</strong><span>Processing issues</span>{!loading && issues > 0 && <button onClick={() => showGroup('issue')}>View candidates</button>}</div>
      <div className={needsReview > 0 ? 'has-review' : ''}><strong>{loading ? '—' : needsReview}</strong><span>Need your judgment</span>{!loading && needsReview > 0 && <button onClick={() => showGroup('review')}>View candidates</button>}</div>
    </div>
    {!connected && <p className="muted">{sourceReady ? 'Screening is disabled: use Source setup above to connect the application source.' : 'Checking the application source before enabling screening…'}</p>}
    {(error || applicationsError) && <div className="screening-notice is-danger" role="alert"><strong>Some results could not be loaded</strong><p>{error || applicationsError}</p>{results && <p>Showing the last confirmed results. A refresh error does not confirm that processing has stopped.</p>}<button className="button-secondary" onClick={() => { setRevision(value => value + 1); onReload() }}>Try again</button></div>}
    {run?.error_code && <div className="screening-notice is-warning" role="status">{errorLabels[run.error_code] ?? run.error_code.replaceAll('_', ' ').toLowerCase()}</div>}
    {!!results?.warnings.length && <div className="screening-notice is-warning">{results.warnings.map(warning => <p key={warning}>{warning.replaceAll('_', ' ').toLowerCase()}</p>)}</div>}
    <div className="screening-split">
      <section className="screening-candidates" aria-label="Candidates"><header><h2>Candidates <small>· showing {visible.length} of {applications.length}</small></h2></header>
        <div className="screening-filters"><label>Search candidates<input type="search" placeholder="Name or email" value={query} onChange={event => filter('search', event.target.value)} /></label><label>Show<select aria-label="Show" value={show} onChange={event => filter('show', event.target.value)}><option value="all">All</option><option value="screened">Screened</option><option value="waiting">Waiting</option><option value="issue">Processing issues</option><option value="outdated">Older results</option></select></label><label>Sort by<select aria-label="Sort by" value={sort} onChange={event => filter('sort', event.target.value)}><option value="score">Score</option><option value="name">Name</option><option value="newest">Newest</option></select></label></div>
        <label className="screening-review-filter"><input type="checkbox" checked={onlyReview} onChange={event => filter('review', event.target.checked ? '1' : '')} />Only those with essential criteria to review</label>
        <div className="screening-list-head"><span>Candidate</span><span>Match score</span><span>Essential criteria</span></div>
        <div className="screening-candidate-list" ref={listRef}>
          {loading ? <div className="ui-state" role="status"><h3>Loading candidates</h3><p>Fetching the latest screening results.</p><div className="screening-skeleton" /><div className="screening-skeleton" /><div className="screening-skeleton" /></div> : applicationsError || (!results && error) ? <div className="ui-state">Use Try again above to reload the candidate results.</div> : !applications.length ? <div className="ui-state"><h3>No applications yet</h3><p>{connected ? "New applications are imported automatically every minute. They will appear here after the next import." : "Use Source setup above to connect your applications."}</p></div> : !visible.length ? <div className="ui-state"><h3>No candidates match these filters</h3><p>Change the search or filters to see more candidates.</p><button className="button-primary" onClick={() => setParams(current => { const next = new URLSearchParams(current); ['search', 'show', 'review', 'candidate'].forEach(key => next.delete(key)); return next }, { replace: true })}>Clear filters</button></div> : visible.map(({ application, item, status }) => {
            const score = validScore(item), review = reviewCount(item)
            return <button key={application.id} className={`screening-candidate-row${selected?.application.id === application.id ? ' is-selected' : ''}`} aria-pressed={selected?.application.id === application.id} onClick={event => { returnFocus.current = event.currentTarget; setParams(current => { const next = new URLSearchParams(current); next.set('candidate', application.id); return next }) }}>
              <span><strong>{application.full_name}</strong><small>Applied {new Date(application.created_at).toLocaleDateString('en-GB', { timeZone: 'Asia/Dhaka', day: 'numeric', month: 'short' })}</small></span>
              <span className="screening-row-score">{score === null ? <small>No score yet</small> : <><span><strong>{Math.round(score)}</strong> / 100</span><progress value={score} max="100" aria-label={`Match score ${score} out of 100`} /></>}{status.key !== 'screened' && <small>{status.label}</small>}</span>
              <span><span className={`ui-badge ${review ? 'is-warning' : status.key === 'screened' ? 'is-success' : status.tone}`}>{review ? `${review} needs review` : status.key === 'screened' ? 'All evidenced' : status.label}</span></span>
            </button>
          })}
        </div><footer className="muted">Search, filters, and list position stay when you open a candidate and come back.</footer>
      </section>
      {!loading && selected && results ? <CandidateDetail key={selected.application.id} token={token} jobTitle={jobTitle} application={selected.application} item={selected.item} results={results} opened={requestedId === selected.application.id} onBack={back} /> : <section className="screening-candidate-detail"><div className="ui-state"><h3>{loading ? 'Loading screening evidence…' : 'Select a candidate'}</h3><p>Requirement evidence and source information will appear here.</p></div></section>}
    </div>
    {!!results?.history.length && <details className="screening-history"><summary>Previous screening runs ({results.history.length})</summary>{results.history.map(old => <p key={old.id}>{old.status.replaceAll('_', ' ')} · criteria v{old.profile_version} · {old.completed}/{old.total} scored</p>)}</details>}
  </div>
}
