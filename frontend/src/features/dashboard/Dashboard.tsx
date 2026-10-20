import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import RecruiterChat from '../jd/RecruiterChat'
import type { RecruiterChatHandle } from '../jd/RecruiterChat'
import { loadDashboard } from './api'
import type { DashboardData } from './api'
import '../jd/JobDescriptions.css'
import './Dashboard.css'

const dateLabel = (value: string, time = false) => new Date(value).toLocaleString('en-GB', {
  ...(time ? { hour: '2-digit', minute: '2-digit' } : { weekday: 'long', day: 'numeric', month: 'long' }),
})
const stageLabel = (value: string) => value.replaceAll('_', ' ')

function AssistantDock({ token, userId, onSaved }: { token: string; userId: string; onSaved: () => void }) {
  const [open, setOpen] = useState(false)
  const [closing, setClosing] = useState(false)
  const [mounted, setMounted] = useState(false)
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const chat = useRef<RecruiterChatHandle>(null)
  const dock = useRef<HTMLElement>(null)
  const trigger = useRef<HTMLInputElement>(null)
  const pendingMessage = useRef('')
  function expand() {
    if (draft.trim()) {
      if (mounted && !busy) chat.current?.send(draft.trim())
      else pendingMessage.current = draft.trim()
      setDraft('')
    }
    setClosing(false)
    setMounted(true); setOpen(true)
  }
  const onChatState = useCallback((_: unknown, working: boolean) => {
    setBusy(working)
    if (!working && pendingMessage.current) {
      const message = pendingMessage.current
      pendingMessage.current = ''
      chat.current?.send(message)
    }
  }, [])
  function collapse() {
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) setOpen(false)
    else setClosing(true)
  }
  useEffect(() => {
    if (!open) { if (mounted) trigger.current?.focus(); return }
    dock.current?.querySelector<HTMLTextAreaElement>('textarea')?.focus()
  }, [open, busy, mounted])
  useEffect(() => {
    const shortcut = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault(); setMounted(true); setOpen(true)
      }
    }
    window.addEventListener('keydown', shortcut)
    return () => window.removeEventListener('keydown', shortcut)
  }, [])
  return <section ref={dock} className={`dashboard-dock${open ? ' is-expanded' : ''}${closing ? ' is-closing' : ''}`} aria-label="Ask Distill"
    onAnimationEnd={event => {
      if (event.target === event.currentTarget && closing) { setOpen(false); setClosing(false) }
    }}
    onKeyDown={event => { if (event.key === 'Escape' && open) { event.preventDefault(); collapse() } }}>
    <header>
      <strong>✦ Ask Distill</strong>
      {open && <><span className="dashboard-muted">Recruiter assistant · review changes before confirming</span>
        <button className="button-secondary" disabled={busy} onClick={() => chat.current?.startNew()}>New chat</button>
        <button className="button-secondary" disabled={closing} aria-expanded={open} aria-controls="dashboard-chat" onClick={collapse}>Collapse ↓</button></>}
    </header>
    <div id="dashboard-chat" className="dashboard-chat" hidden={!open}>
      {mounted && <RecruiterChat compact ref={chat} token={token} userId={userId} onSaved={onSaved}
        onStateChange={onChatState} />}
    </div>
    {!open && <form className="field-focus-group" onSubmit={event => { event.preventDefault(); expand() }}>
      <label className="sr-only" htmlFor="dashboard-question">Ask the recruiter assistant</label>
      <input ref={trigger} id="dashboard-question" value={draft} onChange={event => setDraft(event.target.value)}
        placeholder="Ask about a job description or open your assistant…" />
      <kbd>Ctrl K</kbd><button className="button-primary" aria-expanded={false} aria-controls="dashboard-chat">Ask</button>
    </form>}
  </section>
}

export default function Dashboard({ token, userId, welcomeOnArrival = false, onWelcomeHandled }: {
  token: string; userId: string; welcomeOnArrival?: boolean; onWelcomeHandled?: () => void
}) {
  const [welcome] = useState(welcomeOnArrival)
  const [data, setData] = useState<DashboardData | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [revision, setRevision] = useState(0)
  const refresh = () => setRevision(value => value + 1)
  useEffect(() => {
    if (welcomeOnArrival) onWelcomeHandled?.()
  }, [welcomeOnArrival, onWelcomeHandled])
  useEffect(() => {
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout>
    async function update() {
      try {
        const next = await loadDashboard(token, controller.signal)
        if (!controller.signal.aborted) { setData(next); setError('') }
      } catch (caught) {
        if (!controller.signal.aborted) setError(caught instanceof Error ? caught.message : 'Could not load your dashboard.')
      } finally {
        if (!controller.signal.aborted) { setLoading(false); timer = setTimeout(() => void update(), 60000) }
      }
    }
    void update()
    return () => { controller.abort(); clearTimeout(timer) }
  }, [token, revision])
  const metrics = data ? [
    ['saved jobs', data.totals.jobs], ['applications', data.totals.applications],
    ['without completed screening', data.totals.waiting], ['active interviews', data.active_session_count],
    ['items need you', data.totals.attention],
  ] as const : []
  return <div className={`dashboard-page${welcome ? ' dashboard-welcome' : ''}`}>
    <div className="dashboard-scroll">
      <header className="dashboard-heading">
        <div><p className="workspace-eyebrow">Recruitment workspace</p><h1>Dashboard</h1>
          <p className="dashboard-muted">{dateLabel(new Date().toISOString())} · Here is what needs you today</p></div>
        <div className="dashboard-actions"><button className="button-secondary" onClick={refresh}>Refresh</button>
          <Link className="button-secondary" to="/recruiter/job-descriptions">Open Job Descriptions</Link></div>
      </header>
      {loading && <p role="status" className="dashboard-notice">Loading your recruitment overview…</p>}
      {error && <div role="alert" className="dashboard-notice dashboard-error"><p>{error}</p>
        {data && <p>Showing the last successful snapshot. Counts may have changed.</p>}
        <button className="button-secondary" onClick={refresh}>Try again</button></div>}
      {data && <>
        <div className="dashboard-metrics">{metrics.map(([label, count]) => <div className="dashboard-card" key={label}>
          <strong>{count.toLocaleString()}</strong><span>{label}</span></div>)}</div>
        <div className="dashboard-columns">
          <section className="dashboard-card dashboard-attention"><div className="dashboard-section-heading"><h2>Needs your attention</h2>
            <span className="dashboard-muted">Most urgent first · {data.attention.length} items</span></div>
            {!data.attention.length && <div className="dashboard-empty"><strong>You’re caught up</strong><p>No screening or assessment issues were found in the saved states.</p></div>}
            <ul>{data.attention.map((item, index) => <li className="dashboard-row" key={`${item.href}-${index}`}>
              <span className={`dashboard-count priority-${item.priority}`}>{item.count ?? '!'}</span>
              <div><strong>{item.title}</strong><p className="dashboard-muted">{item.detail}</p></div>
              <Link className="button-secondary" to={item.href}>{item.action}</Link></li>)}</ul>
          </section>
          <div className="dashboard-side">
            <section className="dashboard-card"><div className="dashboard-section-heading"><h2>Interview workspace</h2><Link to="/recruiter/interviews">All interviews</Link></div>
              {!data.sessions.length && <p className="dashboard-empty">No ready or active interview sessions.</p>}
              <ul>{data.sessions.map(session => <li className="dashboard-row" key={session.id}>
                <div><strong>{session.name}</strong><p className="dashboard-muted">{session.job_title}</p>
                  <p className="dashboard-muted">{session.starts_at ? new Date(session.starts_at).toLocaleString() : 'No scheduled time'}</p></div>
                <span className={`dashboard-pill${session.status === 'in_progress' ? ' is-live' : ''}`}>{stageLabel(session.status)}</span>
                <Link className="button-secondary" to={`/recruiter/interviews?job=${session.job_id}`}>Open</Link></li>)}</ul>
              <p className="dashboard-footnote">Up to 8 sessions · Live first · Times in {Intl.DateTimeFormat().resolvedOptions().timeZone}</p>
            </section>
            <section className="dashboard-card"><h2>Workspace checks</h2>
              <div className="dashboard-row"><span className="dashboard-count priority-2">✓</span><div><strong>Project data loaded</strong><p className="dashboard-muted">Updated {dateLabel(data.updated_at, true)}</p></div></div>
              <div className="dashboard-row"><span className="dashboard-count">{data.source_count}</span><div><strong>Application sources configured</strong><p className="dashboard-muted">Saved Drive / Sheets mappings · connectivity not checked here</p></div></div>
              <p className="dashboard-footnote">Screening counts use each job’s latest run. Saved results may require freshness review in their workspace.</p>
            </section>
          </div>
        </div>
        <section className="dashboard-card"><h2>Hiring by job</h2><p className="dashboard-muted">People at each saved stage. Counts are not conversion rates or a ranking.</p>
          {!data.jobs.length ? <div className="dashboard-empty"><p>Create your first job description to start your recruitment pipeline.</p><Link to="/recruiter/job-descriptions">Create a job description →</Link></div> :
            <div className="dashboard-table" tabIndex={0} role="region" aria-label="Hiring by job"><table><thead><tr>
              {['Job', 'Profile', 'Applied', 'Screened', 'Invited', 'Interviewed', 'Assessed', 'Next step'].map(label => <th key={label}>{label}</th>)}
            </tr></thead><tbody>{data.jobs.map(job => <tr key={job.id}>
              <th scope="row">{job.title}<small>Job #{job.number}</small></th><td><span className="dashboard-pill">{job.status}</span></td>
              {[job.applied, job.screened, job.invited, job.interviewed, job.assessed].map((count, i) => <td key={i}>{count}</td>)}
              <td><Link to={`/recruiter/cv-screening?job=${job.id}`}>{job.screening_status && ['queued', 'running'].includes(job.screening_status) ? 'View progress' : 'Open screening'}</Link></td>
            </tr>)}</tbody></table></div>}
          <p className="dashboard-footnote">Invited = email accepted by the provider; assessed = latest real F6 completed. Synthetic assessments are excluded.</p>
        </section>
        <section className="dashboard-card"><h2>Recent activity</h2><p className="dashboard-muted">Latest saved job and interview updates.</p>
          {!data.activity.length && <p className="dashboard-empty">Your recruitment activity will appear here.</p>}
          <ul>{data.activity.map((item, index) => <li className="dashboard-activity" key={index}>
            <time dateTime={item.at}>{new Date(item.at).toLocaleString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })}</time>
            <Link to={item.href}>{item.text}</Link></li>)}</ul>
        </section>
      </>}
    </div>
    <AssistantDock token={token} userId={userId} onSaved={refresh} />
  </div>
}
