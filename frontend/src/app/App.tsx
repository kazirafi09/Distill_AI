import { useEffect, useState } from 'react'
import {
  Navigate,
  NavLink,
  Route,
  Routes,
  useNavigate,
  useLocation,
} from 'react-router-dom'

import LoginForm from '../features/auth/LoginForm'
import RecruiterChat from '../features/jd/RecruiterChat'
import MyJobDescriptions from '../features/jd/MyJobDescriptions'
import CVScreening from '../features/cv/CVScreening'
import { useCVSourceChecks } from '../features/cv/useCVSourceChecks'
import Interviews from '../features/interviews/Interviews'
import Invitations from '../features/interviews/Invitations'
import InterviewEntry from '../features/interviews/InterviewEntry'
import Assessments from '../features/assessments/Assessments'
import type { RecruiterSession } from '../services/api'
import { logoutRecruiter, restoreSession } from '../services/api'
import './App.css'


const navigation = [
  { path: '/recruiter/dashboard', label: 'Dashboard', mark: 'D' },
  { path: '/recruiter/job-descriptions', label: 'Job Descriptions', mark: 'JD' },
  { path: '/recruiter/cv-screening', label: 'CV Screening', mark: 'CV' },
  { path: '/recruiter/invitations', label: 'Invitations', mark: 'E' },
  { path: '/recruiter/interviews', label: 'Interviews', mark: 'I' },
  { path: '/recruiter/assessments', label: 'Assessments', mark: 'A' },
]

function FeaturePlaceholder({ title }: { title: string }) {
  return (
    <section className="feature-placeholder">
      <h1 className="text-2xl font-bold">{title}</h1>
      <p className="mt-2 text-sm text-slate-500">
        This feature is planned. Its workspace will be added here.
      </p>
    </section>
  )
}

export default function App() {
  const navigate = useNavigate()
  const location = useLocation()
  const isInterviewEntry = location.pathname === '/interview'

  const [connection, setConnection] = useState('Checking connection…')
  const [session, setSession] = useState<RecruiterSession | null>(null)
  const [restoring, setRestoring] = useState(true)
  const [restoreFailed, setRestoreFailed] = useState(false)
  const [authError, setAuthError] = useState<string | null>(null)
  const [restoreAttempt, setRestoreAttempt] = useState(0)
  const [signingOut, setSigningOut] = useState(false)
  const [jdRefreshKey, setJDRefreshKey] = useState(0)
  const [jdChatKey, setJDChatKey] = useState(0)
  const [navigationOpen, setNavigationOpen] = useState(false)
  const cvChecks = useCVSourceChecks(isInterviewEntry ? undefined : session?.token)

  useEffect(() => {
    if (isInterviewEntry) return
    let active = true
    void restoreSession().then(saved => {
      if (active) { setSession(saved); setAuthError(null); setRestoreFailed(false); setRestoring(false) }
    }).catch(() => {
      if (active) { setAuthError('Unable to restore your session. Check your connection and try again.'); setRestoreFailed(true); setRestoring(false) }
    })
    return () => { active = false }
  }, [restoreAttempt, isInterviewEntry])

  useEffect(() => {
    function expired() {
      setSession(null)
      setAuthError('Your session has expired. Please sign in again.')
    }
    window.addEventListener('distill-session-expired', expired)
    return () => window.removeEventListener('distill-session-expired', expired)
  }, [])

  useEffect(() => {
    const controller = new AbortController()

    async function checkConnection() {
      try {
        const response = await fetch('/api/health/db', {
          signal: controller.signal,
        })

        if (!response.ok) {
          throw new Error('Health check failed')
        }

        const data = await response.json()

        if (data.status !== 'ok' || data.database !== 'connected') {
          throw new Error('Unexpected health response')
        }

        if (!controller.signal.aborted) {
          setConnection('Backend and database connected')
        }
      } catch {
        if (!controller.signal.aborted) {
          setConnection('Connection failed')
        }
      }
    }

    void checkConnection()
    return () => controller.abort()
  }, [])

  function handleLogin(nextSession: RecruiterSession) {
    setAuthError(null)
    setSession(nextSession)
    navigate('/recruiter/job-descriptions', { replace: true })
  }

  async function handleSignOut() {
    setSigningOut(true)
    try {
      await logoutRecruiter()
      setSession(null)
      setAuthError(null)
      navigate('/', { replace: true })
    } catch {
      setAuthError('Unable to sign out. Check your connection and try again.')
    } finally { setSigningOut(false) }
  }

  if (isInterviewEntry) {
    return <InterviewEntry key={location.hash} />
  }

  if (restoring || (!session && restoreFailed)) {
    return <main className="app"><h1 className="text-3xl font-bold">Distill AI</h1>
      <p role="status">{restoring ? 'Restoring your session…' : authError}</p>
      {!restoring && <button className="button-primary" onClick={() => {
        setRestoring(true); setRestoreAttempt(value => value + 1)
      }}>Try again</button>}
    </main>
  }

  if (!session) {
    return (
      <main className="app">
        <h1 className="text-3xl font-bold">Distill AI</h1>
        <p>Recruitment & Interview Intelligence Platform</p>

        <p role="status" className="text-sm text-slate-500">
          {connection}
        </p>
        {authError && <p role="alert">{authError}</p>}

        <Routes>
          <Route path="/" element={<LoginForm onLogin={handleLogin} />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    )
  }

  return (
    <div className="workspace-shell">
      <aside className={`workspace-sidebar${navigationOpen ? ' is-open' : ''}`}>
        <button className="sidebar-menu" aria-label="Toggle navigation" aria-expanded={navigationOpen} onClick={() => setNavigationOpen(value => !value)}>☰</button>
        <NavLink
          to="/recruiter/job-descriptions"
          className="sidebar-brand"
          aria-label="Distill AI home"
          onClick={() => { setNavigationOpen(false); setJDChatKey(value => value + 1) }}
        >
          <span className="brand-mark" aria-hidden="true">D</span>
          <span>Distill AI</span>
        </NavLink>

        <p className="sidebar-caption">Recruiter workspace</p>

        <nav aria-label="Main navigation" className="sidebar-navigation">
          {navigation.map((item) => (
            <NavLink
              key={item.path}
              onClick={() => {
                setNavigationOpen(false)
                if (item.path === '/recruiter/job-descriptions') setJDChatKey(value => value + 1)
              }}
              to={item.path}
              className={({ isActive }) =>
                `sidebar-link${isActive ? ' is-active' : ''}${item.path === '/recruiter/cv-screening' && cvChecks.total ? ' has-notification' : ''}`
              }
            >
              <span className="navigation-mark" aria-hidden="true">
                {item.mark}
              </span>
              <span>{item.label}</span>
              {item.path === '/recruiter/cv-screening' && cvChecks.total > 0 && <span className="sidebar-notification" aria-label={`${cvChecks.total} new CVs available`}>{cvChecks.total > 99 ? '99+' : cvChecks.total}</span>}
            </NavLink>
          ))}
        </nav>

        <div className="sidebar-account">
          <div className="account-avatar" aria-hidden="true">
            {session.user.email.charAt(0).toUpperCase()}
          </div>

          <div className="account-details">
            <p className="font-semibold">Recruiter</p>
            <p className="account-email" title={session.user.email}>
              {session.user.email}
            </p>
          </div>

          <button
            type="button"
            className="sidebar-signout"
            onClick={() => void handleSignOut()}
            disabled={signingOut}
          >
            {signingOut ? 'Signing out…' : 'Sign out'}
          </button>
          {authError && <p role="alert">{authError}</p>}
        </div>
      </aside>

      <main className="workspace-main">
        <Routes>
          <Route
            path="/recruiter/invitations"
            element={<Invitations key={session.user.id} token={session.token} />}
          />
          <Route path="/recruiter/candidates" element={<Navigate to="/recruiter/cv-screening" replace />} />
          <Route
            path="/recruiter/job-descriptions"
            element={
              <section className="jd-page">
                <h1 className="sr-only">Job Descriptions</h1>

                <div className="jd-workspace">
                  <MyJobDescriptions
                    key={session.user.id}
                    token={session.token}
                    refreshKey={jdRefreshKey}
                  />

                  <RecruiterChat
                    key={`${session.user.id}:${jdChatKey}`}
                    startFresh
                    token={session.token}
                    userId={session.user.id}
                    onSaved={() => setJDRefreshKey((value) => value + 1)}
                  />
                </div>
              </section>
            }
          />
          <Route
            path="/recruiter/cv-screening"
            element={
              <CVScreening
                key={session.user.id}
                token={session.token}
                sourceChecks={cvChecks.checks}
                checksError={cvChecks.error}
                refreshChecks={cvChecks.refresh}
              />
            }
          />
          <Route
            path="/recruiter/interviews"
            element={<Interviews key={session.user.id} token={session.token} userId={session.user.id} />}
          />
          <Route path="/recruiter/assessments" element={<Assessments key={session.user.id} token={session.token} />} />
          {navigation
            .filter(
              (item) =>
                item.path !== '/recruiter/job-descriptions' &&
                item.path !== '/recruiter/cv-screening' &&
                item.path !== '/recruiter/invitations' &&
                item.path !== '/recruiter/interviews' &&
                item.path !== '/recruiter/assessments',
            )
            .map((item) => (
              <Route
                key={item.path}
                path={item.path}
                element={<FeaturePlaceholder title={item.label} />}
              />
            ))}

          <Route
            path="*"
            element={<Navigate to="/recruiter/job-descriptions" replace />}
          />
        </Routes>
      </main>
    </div>
  )
}
