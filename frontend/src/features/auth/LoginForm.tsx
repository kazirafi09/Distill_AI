import { useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { acceptSession, loginRecruiter, signupRecruiter } from '../../services/api'
import type { RecruiterSession } from '../../services/api'
import './LoginForm.css'

type LoginFormProps = {
  onLogin: (session: RecruiterSession) => void
  connection?: string
  sessionError?: string | null
}

function PasswordField({ id, label, value, onChange, signup, disabled, hint }: {
  id: string; label: string; value: string; onChange: (value: string) => void
  signup: boolean; disabled: boolean; hint?: string
}) {
  const [visible, setVisible] = useState(false)
  return <div className="auth-field">
    <label htmlFor={id}>{label}</label>
    <div className="auth-password field-focus-group">
      <input id={id} type={visible ? 'text' : 'password'} value={value}
        onChange={event => onChange(event.target.value)} disabled={disabled}
        autoComplete={signup ? 'new-password' : 'current-password'}
        required minLength={signup ? 12 : undefined} maxLength={1024}
        aria-describedby={hint} placeholder={id === 'confirm-password' ? 'Type your password again' : signup ? 'Choose a password' : 'Enter your password'} />
      <button type="button" onClick={() => setVisible(current => !current)} disabled={disabled}
        aria-label={`${visible ? 'Hide' : 'Show'} ${label.toLowerCase()}`} aria-pressed={visible}>
        {visible ? 'Hide' : 'Show'}
      </button>
    </div>
  </div>
}

function AuthArtwork({ signup }: { signup: boolean }) {
  return <aside className="auth-artwork" aria-label="About Distill AI">
    <div className="auth-eyebrow"><span>{signup ? 'Start here' : 'Why Distill AI'}</span><i /></div>
    <svg className="auth-ribbons" viewBox="0 0 560 480" fill="none" aria-hidden="true">
      <defs><linearGradient id="auth-ribbon" x1="0" y1="0" x2="550" y2="80" gradientUnits="userSpaceOnUse">
        <stop stopColor="#7ee8b4" /><stop offset=".45" stopColor="#20b996" /><stop offset="1" stopColor="#087b87" />
      </linearGradient></defs>
      {[0, 68, 136, 204, 272, 340].map((offset, index) => <g key={offset} className="auth-wave" style={{ animationDelay: `${index * -1.3}s` }}>
        <path d={`M-65 ${60 + offset} C105 ${-30 + offset} 255 ${178 + offset} 635 ${55 + offset}`}
          stroke="url(#auth-ribbon)" strokeWidth={index % 2 ? 26 : 38} />
        {index % 2 === 0 && <path d={`M-65 ${60 + offset} C105 ${-30 + offset} 255 ${178 + offset} 635 ${55 + offset}`}
          stroke="#c5ffe7" strokeOpacity=".5" />}
      </g>)}
    </svg>
    <div className="auth-story" key={signup ? 'signup' : 'signin'}>
      <p>{signup ? <>Less guessing,<br />more record.</> : <>Hire with<br />evidence.</>}</p>
      <span>{signup ? 'Interviews, transcripts and assessments in one place, with sources you can check.'
        : 'Keep the words behind every score in view. Review the evidence and make the hiring decision.'}</span>
    </div>
  </aside>
}

export default function LoginForm({ onLogin, connection, sessionError }: LoginFormProps) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [signup, setSignup] = useState(false)
  const [confirmation, setConfirmation] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const submitting = useRef(false)
  const serviceUnavailable = connection === 'Connection failed'

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (submitting.current) return
    submitting.current = true
    setBusy(true); setError(null)
    try {
      if (signup && password !== confirmation) throw new Error('Passwords do not match.')
      const result = await (signup ? signupRecruiter : loginRecruiter)(email.trim(), password)
      const session = await acceptSession(result.access_token)
      setPassword(''); setConfirmation('')
      onLogin(session)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Unable to sign in. Please try again.')
    } finally {
      submitting.current = false
      setBusy(false)
    }
  }

  function changeMode() {
    setSignup(current => !current)
    setError(null); setPassword(''); setConfirmation('')
  }

  return <main className="auth-page">
    <AuthArtwork signup={signup} />
    <section className="auth-panel" aria-labelledby="login-title">
      <header className="auth-header">
        <a href="/" className="auth-brand" aria-label="Distill AI home"><span className="brand-mark" aria-hidden="true">D</span>Distill AI</a>
        {connection && <span role="status" className={`auth-connection${serviceUnavailable ? ' is-unavailable' : ''}`}>
          <i aria-hidden="true" />{serviceUnavailable ? 'Service unavailable' : connection.startsWith('Checking') ? 'Connecting…' : 'Service available'}
        </span>}
      </header>
      <div className="auth-content">
        <div className="auth-form-content" key={signup ? 'signup' : 'signin'}>
          <div className="auth-introduction"><p className="auth-kicker">Your recruitment workspace</p>
            <h1 id="login-title">{signup ? 'Create your account' : 'Welcome back'}</h1>
            <p>{signup ? 'Bring your recruitment workflow into one place.' : 'Sign in to review candidates, interviews and assessments.'}</p>
          </div>
          <form onSubmit={event => void handleSubmit(event)} aria-busy={busy}>
            <div className="auth-field"><label htmlFor="login-email">Work email</label>
              <input id="login-email" type="email" autoComplete="username" required maxLength={254}
                placeholder="name@company.com" value={email} disabled={busy} onChange={event => setEmail(event.target.value)} />
            </div>
            <PasswordField key={`${signup}-password`} id="login-password" label="Password" value={password}
              onChange={setPassword} signup={signup} disabled={busy} hint={signup ? 'password-hint' : undefined} />
            {signup && <><p id="password-hint" className={`auth-password-hint${password.length >= 12 ? ' is-ready' : ''}`}>
              <span aria-hidden="true">{password.length >= 12 ? '✓' : '○'}</span> Use at least 12 characters.
            </p><PasswordField id="confirm-password" label="Confirm password" value={confirmation}
              onChange={setConfirmation} signup disabled={busy} /></>}
            {(error || sessionError) && <p role="alert" className="auth-error">{error || sessionError}</p>}
            <button type="submit" className="button-primary auth-submit" disabled={busy || !email.trim() || !password}>
              {busy && <span className="auth-spinner" aria-hidden="true" />}
              {busy ? signup ? 'Creating account…' : 'Signing in…' : signup ? 'Create account' : 'Sign in'}
              {!busy && <span aria-hidden="true">↗</span>}
            </button>
          </form>
          <p className="auth-switch">{signup ? 'Already have an account?' : 'New to Distill AI?'}{' '}
            <button type="button" disabled={busy} onClick={changeMode}
              aria-label={signup ? 'Already have an account? Sign in' : 'New here? Create an account'}>
              {signup ? 'Sign in' : 'Create an account'}
            </button>
          </p>
        </div>
      </div>
      <footer className="auth-footer">Recruiter accounts only. Candidates join interviews using their invitation link.</footer>
    </section>
  </main>
}
