import { useCallback, useEffect, useRef, useState } from 'react'
import {
  Navigate,
  NavLink,
  Route,
  Routes,
  useNavigate,
  useLocation,
} from 'react-router-dom'

import LoginForm from '../features/auth/LoginForm'
import JobDescriptions from '../features/jd/JobDescriptions'
import CVScreening from '../features/cv/CVScreening'
import { useCVSourceChecks } from '../features/cv/useCVSourceChecks'
import Interviews from '../features/interviews/Interviews'
import Invitations from '../features/interviews/Invitations'
import InterviewEntry from '../features/interviews/InterviewEntry'
import CandidateInterviewPreview from '../features/interviews/CandidateInterviewPreview'
import Assessments from '../features/assessments/Assessments'
import Dashboard from '../features/dashboard/Dashboard'
import SidebarDrawer from '../components/motion/SidebarDrawer'
import { useSidebarIndicator, useWorkspaceMotion } from '../components/motion/useWorkspaceMotion'
import type { RecruiterSession } from '../services/api'
import { logoutRecruiter, restoreSession } from '../services/api'
import './App.css'
import '../components/motion/motion.css'


const navigation = [
  { path: '/recruiter/dashboard', label: 'Dashboard', mark: 'D' },
  { path: '/recruiter/job-descriptions', label: 'Job Descriptions', mark: 'JD' },
  { path: '/recruiter/cv-screening', label: 'CV Screening', mark: 'CV' },
  { path: '/recruiter/invitations', label: 'Invitations', mark: 'E' },
  { path: '/recruiter/interviews', label: 'Interviews', mark: 'I' },
  { path: '/recruiter/assessments', label: 'Assessments', mark: 'A' },
]


export default function App() {
  const navigate = useNavigate()
  const location = useLocation()
  const isInterviewPreview = import.meta.env.DEV && location.pathname === '/interview-preview'
  const isInterviewEntry = location.pathname === '/interview' || isInterviewPreview

  const [connection, setConnection] = useState('Checking connection…')
  const [session, setSession] = useState<RecruiterSession | null>(null)
  const [restoring, setRestoring] = useState(true)
  const [restoreFailed, setRestoreFailed] = useState(false)
  const [authError, setAuthError] = useState<string | null>(null)
  const [restoreAttempt, setRestoreAttempt] = useState(0)
  const [signingOut, setSigningOut] = useState(false)
  const [dashboardWelcome, setDashboardWelcome] = useState(false)
  const finishDashboardWelcome = useCallback(() => setDashboardWelcome(false), [])
  const [navigationOpen, setNavigationOpen] = useState(false)
  const menuRef = useRef<HTMLButtonElement>(null)
  const drawerRef = useRef<HTMLDivElement>(null)
  const mainRef = useRef<HTMLElement>(null)
  const navigationRef = useRef<HTMLElement>(null)
  const closeNavigation = useCallback(() => setNavigationOpen(false), [])
  const animatedPage = ['/recruiter/job-descriptions', '/recruiter/cv-screening', '/recruiter/interviews', '/recruiter/invitations', '/recruiter/assessments'].includes(location.pathname)
  useWorkspaceMotion(mainRef, session ? location.pathname : '')
  useSidebarIndicator(navigationRef, `${session?.user.id ?? ''}:${location.pathname}`)
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
    setDashboardWelcome(true)
    setSession(nextSession)
    navigate('/recruiter/dashboard', { replace: true })
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
    return isInterviewPreview ? <CandidateInterviewPreview /> : <InterviewEntry key={location.hash} />
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
        <Routes>
          <Route path="/" element={<LoginForm onLogin={handleLogin} connection={connection} sessionError={authError} />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
    )
  }

  return (
    <div className="workspace-shell">
      <aside className={`workspace-sidebar${navigationOpen ? ' is-open' : ''}`}>
        <button ref={menuRef} className="sidebar-menu" aria-label="Toggle navigation" aria-controls="workspace-navigation" aria-expanded={navigationOpen} onClick={() => setNavigationOpen(value => !value)}>☰</button>
        <NavLink
          to="/recruiter/dashboard"
          className="sidebar-brand"
          aria-label="Distill AI home"
          onClick={() => setNavigationOpen(false)}
        >
          <span className="brand-mark" aria-hidden="true">D</span>
          <span>Distill AI</span>
        </NavLink>

        <span className="mobile-page-label">{navigation.find(item => location.pathname.startsWith(item.path))?.label ?? 'Recruiter workspace'}</span>
        <SidebarDrawer open={navigationOpen} onClose={closeNavigation} trigger={menuRef} content={mainRef} drawer={drawerRef}>
        <p className="sidebar-caption">Recruiter workspace</p>

        <nav ref={navigationRef} id="workspace-navigation" aria-label="Main navigation" className="sidebar-navigation" onKeyDown={event => { if (event.key === 'Escape') { setNavigationOpen(false); menuRef.current?.focus() } }}>
          {navigation.map((item) => (
            <NavLink
              key={item.path}
              onClick={() => {
                setNavigationOpen(false)
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
        </SidebarDrawer>
      </aside>
      <button className={`sidebar-backdrop${navigationOpen ? ' is-open' : ''}`} aria-label="Close navigation" tabIndex={-1} inert={!navigationOpen} onClick={closeNavigation} />

      <main ref={mainRef} className={`workspace-main${animatedPage ? ' motion-workspace' : ''}`}>
        <Routes>
          <Route
            path="/recruiter/invitations"
            element={<Invitations key={session.user.id} token={session.token} />}
          />
          <Route path="/recruiter/candidates" element={<Navigate to="/recruiter/cv-screening" replace />} />
          <Route
            path="/recruiter/job-descriptions"
            element={
              <JobDescriptions key={session.user.id} token={session.token} userId={session.user.id} />
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
          <Route path="/recruiter/dashboard" element={<Dashboard key={session.user.id} token={session.token} userId={session.user.id} welcomeOnArrival={dashboardWelcome} onWelcomeHandled={finishDashboardWelcome} />} />

          <Route
            path="*"
            element={<Navigate to="/recruiter/dashboard" replace />}
          />
        </Routes>
      </main>
    </div>
  )
}
