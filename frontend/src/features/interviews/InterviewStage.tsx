import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import type { SavedInterviewee } from './api'
import InterviewTranscript from './InterviewTranscript'
import './InterviewStage.css'

type Tab = 'Assistant' | 'Transcript' | 'Notes' | 'Profile'
type Props = {
  token: string
  jobId: string
  candidate: SavedInterviewee
  jobTitle: string
  jobNumber: number
  status: string
  startedAt?: string | null
  endedAt?: string | null
  connected: boolean
  video: ReactNode
  assistant: ReactNode
  notices: ReactNode
  canEnd: boolean
  busy: boolean
  onEnd: () => void
  onClose: () => void
}

export default function InterviewStage(props: Props) {
  const [tab, setTab] = useState<Tab>('Assistant')
  const [notes, setNotes] = useState('')
  const [menu, setMenu] = useState(false)
  const stageRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const root = document.getElementById('root')
    const previousFocus = document.activeElement
    const wasInert = root?.inert ?? false
    if (root) root.inert = true
    stageRef.current?.focus()
    return () => {
      if (root) root.inert = wasInert
      if (previousFocus instanceof HTMLElement && previousFocus.isConnected) previousFocus.focus()
    }
  }, [])
  const [now, setNow] = useState(() => Date.now())
  const [compact, setCompact] = useState(() => window.matchMedia('(max-height: 679px), (max-width: 700px)').matches)
  const [phone, setPhone] = useState(() => window.matchMedia('(max-width: 700px)').matches)
  useEffect(() => {
    const query = window.matchMedia('(max-height: 679px), (max-width: 700px)')
    const phoneQuery = window.matchMedia('(max-width: 700px)')
    const update = () => { setCompact(query.matches); setPhone(phoneQuery.matches) }
    query.addEventListener('change', update)
    phoneQuery.addEventListener('change', update)
    const timer = setInterval(() => setNow(Date.now()), 1000)
    return () => { query.removeEventListener('change', update); phoneQuery.removeEventListener('change', update); clearInterval(timer) }
  }, [])
  const clockEnd = props.endedAt ? new Date(props.endedAt).getTime() : now
  const elapsed = props.startedAt ? Math.max(0, Math.floor((clockEnd - new Date(props.startedAt).getTime()) / 1000)) : 0
  const clock = [Math.floor(elapsed / 3600), Math.floor(elapsed / 60) % 60, elapsed % 60].map(value => String(value).padStart(2, '0')).join(':')
  const initials = props.candidate.name.split(/\s+/).slice(0, 2).map(word => word[0]).join('').toUpperCase()
  const profile = <div className="stage-profile"><span className="stage-avatar">{initials}</span><h2>{props.candidate.name}</h2><p>{props.candidate.email}</p><dl><dt>Screening score</dt><dd>{Number(props.candidate.score.aggregate).toFixed(2)} / 100</dd><dt>Saved rank</dt><dd>#{props.candidate.rank}</dd></dl><p>These are screening results. Nothing is rated during the call.</p></div>
  const tabs: Tab[] = compact ? ['Assistant', 'Transcript', 'Notes', ...(!phone ? ['Profile' as const] : [])] : ['Assistant', 'Notes', 'Profile']
  const shownTab = !compact && tab === 'Transcript' ? 'Assistant' : tab
  return createPortal(<div ref={stageRef} tabIndex={-1} role="dialog" aria-modal="true" aria-label="Live interview workspace" className="interview-stage">
    <aside className={`stage-navigation${menu ? ' is-open' : ''}`} aria-label="Interview navigation">
      <Link className="stage-logo" to="/recruiter/job-descriptions" onClick={props.onClose}>D</Link>
      <nav>{[['dashboard', 'D', 'Dashboard'], ['job-descriptions', 'JD', 'Job Descriptions'], ['cv-screening', 'CV', 'CV Screening'], ['invitations', 'E', 'Invitations'], ['interviews', 'I', 'Interviews'], ['assessments', 'A', 'Assessments']].map(([path, mark, label]) => <Link key={path} title={label} aria-label={label} className={path === 'interviews' ? 'is-active' : ''} to={`/recruiter/${path}`} onClick={props.onClose}>{mark}</Link>)}</nav>
      <button onClick={props.onClose} title="Return to interviews" aria-label="Return to interviews">↩</button>
    </aside>
    <div className="stage-phone-brand"><span className="stage-logo">D</span><strong>Distill AI</strong></div>
    <header className="stage-header">
      <button className="stage-menu" aria-label="Toggle navigation" aria-expanded={menu} onClick={() => setMenu(!menu)}>☰</button>
      <div className="stage-heading"><div><button onClick={props.onClose}>Interviews</button><span> / Live interview</span></div><h1>{props.jobTitle} <span>· JD {props.jobNumber}</span></h1></div>
      <div className="stage-status"><span className={`stage-badge${props.status === 'in_progress' ? '' : ' muted'}`}>● {props.status.replaceAll('_', ' ')}</span><time>{clock}</time><span className={`stage-badge ${props.connected ? '' : 'muted'}`}>{props.connected ? phone ? '● Online' : '● Connected' : 'Offline'}</span><button className="stage-danger" disabled={!props.canEnd || props.busy} onClick={props.onEnd}>End interview</button></div>
    </header>
    <div className="stage-body">
      <div className="stage-left"><div className="stage-video-area">{props.video}</div>{!compact && <div className="stage-desktop-transcript"><InterviewTranscript token={props.token} jobId={props.jobId} applicationId={props.candidate.application_id} /></div>}</div>
      <div className="stage-mobile-summary"><span className="stage-avatar">{initials}</span><div><strong>{props.candidate.name}</strong><small>Score {Number(props.candidate.score.aggregate).toFixed(2)} / 100 · Rank #{props.candidate.rank}</small></div><button onClick={() => setTab(tab === 'Profile' ? 'Assistant' : 'Profile')}>Details ▾</button></div>
      <section className="stage-panel" aria-label="Interview tools">
        <div className="stage-tabs" role="tablist" aria-label="Interview tools">{tabs.map((item, index) => <button key={item} id={`stage-tab-${item}`} role="tab" tabIndex={shownTab === item ? 0 : -1} aria-selected={shownTab === item} aria-controls={`stage-panel-${item}`} onClick={() => setTab(item)} onKeyDown={event => {
          if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
          event.preventDefault()
          const next = event.key === 'Home' ? tabs[0] : event.key === 'End' ? tabs.at(-1)! : tabs[(index + (event.key === 'ArrowRight' ? 1 : tabs.length - 1)) % tabs.length]
          setTab(next)
          document.getElementById(`stage-tab-${next}`)?.focus()
        }}>{item}</button>)}</div>
        <div className="stage-panel-content" role="tabpanel" id={`stage-panel-${shownTab}`} aria-label={shownTab}>
          <div className="stage-assistant" hidden={shownTab !== 'Assistant'}>{props.assistant}</div>
          {shownTab === 'Transcript' && <InterviewTranscript token={props.token} jobId={props.jobId} applicationId={props.candidate.application_id} />}
          <div className="stage-notes" hidden={shownTab !== 'Notes'}><label htmlFor="interview-notes">Interview notes</label><p>Notes stay on this screen and are not saved.</p><textarea id="interview-notes" value={notes} onChange={event => setNotes(event.target.value)} placeholder="Write notes while you interview…" /></div>
          {shownTab === 'Profile' && profile}
        </div>
      </section>
    </div>
    {props.notices && <div className="stage-notices">{props.notices}</div>}
  </div>, document.body)
}
