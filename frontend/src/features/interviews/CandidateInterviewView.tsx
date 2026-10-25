import type { ReactNode, Ref } from 'react'
import { CallIcon } from './InterviewRoom'
import type { InvitationSchedule } from './api'
import { scheduleSummary } from './invitationScheduleView'
import './InterviewStage.css'
import './CandidateInterview.css'

export type CandidatePhase = 'checking' | 'devices' | 'waiting' | 'ready' | 'call' | 'ending' | 'finished' | 'unavailable'
type Props = {
  phase: CandidatePhase
  jobTitle?: string
  instructions?: string
  expiresAt?: string
  schedule?: InvitationSchedule | null
  statusText: string
  error?: string
  notice?: string
  busy?: boolean
  deviceCheck?: ReactNode
  verification?: ReactNode
  waitingVideoRef?: Ref<HTMLDivElement>
  call?: ReactNode
  onJoin?: () => void
  onRecheck?: () => void
  onWaiting?: () => void
  canReturnToWaiting?: boolean
}

export default function CandidateInterviewView({ waitingVideoRef, ...props }: Props) {
  const { phase } = props
  const inCall = phase === 'call'
  const terminal = phase === 'finished' || phase === 'ending' || phase === 'unavailable'
  const step = phase === 'devices' || phase === 'checking' ? 0 : phase === 'waiting' || phase === 'ready' ? 1 : 2
  return <main className={`candidate-interview-page${inCall ? ' is-in-call' : ''}`}>
    <header className="candidate-header">
      <div className="candidate-brand"><span className="brand-mark" aria-hidden="true">D</span><strong>Distill AI</strong><span>Interview workspace</span></div>
      <span className={`candidate-status${phase === 'ready' || inCall ? ' is-ready' : ''}`} role="status"><i aria-hidden="true" />{props.statusText}</span>
    </header>
    <div className="candidate-page-content">
      <header className="candidate-heading"><div><p className="workspace-eyebrow">Your interview</p>
        <h1>{inCall ? 'Live interview' : terminal ? phase === 'finished' ? 'Interview complete' : phase === 'ending' ? 'Interview ending' : 'Check your invitation' : 'Join your interview'}</h1>
        <p>{props.jobTitle ?? 'Recruitment interview'}</p></div>
        {!terminal && !inCall && <ol className="candidate-steps" aria-label="Joining steps">{['Check devices', 'Waiting room', 'Interview'].map((label, index) =>
          <li key={label} className={index === step ? 'is-current' : index < step ? 'is-done' : ''} aria-current={index === step ? 'step' : undefined}>
            <button type="button" onClick={index === 0 ? props.onRecheck : index === 1 ? props.onWaiting : props.onJoin}
              disabled={props.busy || phase === 'checking' || (index === 0 ? !props.onRecheck : index === 1 ? !props.canReturnToWaiting || !props.onWaiting : phase !== 'ready' || !props.onJoin)}>
              <span>{index < step ? '✓' : index + 1}</span>{label}
            </button>
          </li>)}</ol>}
      </header>
      {props.error && <p className="candidate-alert" role="alert">{props.error}</p>}
      {props.notice && <p className="candidate-notice" role="status">{props.notice}</p>}
      {terminal ? <section className="candidate-terminal candidate-card">
        <span className="candidate-terminal-mark" aria-hidden="true">{phase === 'finished' ? '✓' : '!'}</span>
        <h2>{phase === 'finished' ? 'Thank you for your time' : phase === 'ending' ? 'Your interviewer is closing the session' : 'This link is not available'}</h2>
        <p>{phase === 'finished' ? 'The interviewer has ended this session. You can close this page.' : phase === 'ending' ? 'You have been disconnected while the interview finishes.' : 'Use the latest invitation link, or contact your recruiter for help.'}</p>
      </section> : <div className="candidate-layout">
        <section className="candidate-video-panel" aria-label={inCall ? 'Interview call' : 'Device preparation'}>
          {inCall ? props.call : props.deviceCheck ? props.deviceCheck : <div className="candidate-waiting-video">
            <span className="candidate-video-caption">Your interview space</span>
            <div className="candidate-waiting-content"><div className="stage-person" aria-hidden="true" />
              <h2>{phase === 'checking' ? 'Checking your invitation' : phase === 'ready' ? 'Ready to join' : 'You’re in the waiting room'}</h2>
              <p>{phase === 'checking' ? 'Your interview details will appear shortly.' : phase === 'ready' ? 'Your interviewer has admitted you. Join when you’re ready.' : 'Your interviewer will let you in when the session is ready.'}</p>
            </div>
            <div className="candidate-preview-controls" aria-hidden="true"><span><CallIcon kind="camera" />Camera</span><span><CallIcon kind="microphone" />Microphone</span></div>
            <div className="candidate-verification-preview" ref={waitingVideoRef} />
          </div>}
        </section>
        <aside className="candidate-info">
          {props.schedule && !inCall && <section className="candidate-card"><p className="candidate-card-kicker">Your scheduled time</p><p>{scheduleSummary(props.schedule)}</p><p>Joining opens 10 minutes before the start. Please wait for your interviewer to admit you.</p></section>}
          {props.verification}
          <section className="candidate-card"><p className="candidate-card-kicker">{inCall ? 'In this interview' : 'Before you join'}</p>
            <h2>{props.jobTitle ?? 'Your upcoming conversation'}</h2>
            <p>{inCall ? 'Use the controls below the video to manage your microphone, camera, or leave the call.' : 'Check your camera and microphone, then wait for your interviewer to admit you.'}</p>
            <div className="candidate-guidance"><CallIcon kind="microphone" /><span>Choose a quiet place and check that your microphone is ready.</span></div>
            <div className="candidate-guidance"><CallIcon kind="camera" /><span>You control when your camera is on.</span></div>
          </section>
          {props.instructions && !inCall && <section className="candidate-card candidate-instructions"><h2>From your recruiter</h2><p>{props.instructions}</p>
            {props.expiresAt && <p className="candidate-expiry">Invitation valid until {new Date(props.expiresAt).toLocaleString()}.</p>}
          </section>}
          {!inCall && (phase === 'waiting' || phase === 'ready') && <section className="candidate-card candidate-join-card">
            <span className={`candidate-join-indicator${phase === 'ready' ? ' is-ready' : ''}`} aria-hidden="true">{phase === 'ready' ? '✓' : '…'}</span>
            <h2>{phase === 'ready' ? 'You can join now' : 'Waiting for your interviewer'}</h2>
            <p role="status">{props.statusText}</p>
            <button className="button-primary" disabled={props.busy || phase !== 'ready'} onClick={props.onJoin}>{props.busy ? 'Joining…' : 'Join interview'}</button>
            <button className="button-secondary" disabled={props.busy} onClick={props.onRecheck}>Check devices again</button>
          </section>}
          <p className="candidate-footnote">Leave exits your call. Your interviewer controls when the interview ends.</p>
        </aside>
      </div>}
    </div>
    <footer className="candidate-page-footer"><span>Distill AI · Candidate interview</span><span>Need help? Contact the recruiter who sent your invitation.</span></footer>
  </main>
}
