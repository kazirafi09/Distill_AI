import { useRef, useState } from 'react'
import type { FormEvent } from 'react'

import {
  acceptSession,
  loginRecruiter,
  signupRecruiter,
} from '../../services/api'
import type { RecruiterSession } from '../../services/api'

type LoginFormProps = {
  onLogin: (session: RecruiterSession) => void
}

export default function LoginForm({ onLogin }: LoginFormProps) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [signup, setSignup] = useState(false)
  const [confirmation, setConfirmation] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const submittingRef = useRef(false)

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()

    if (submittingRef.current) return

    submittingRef.current = true
    setBusy(true)
    setError(null)

    try {
      if (signup && password !== confirmation) throw new Error('Passwords do not match.')
      const result = await (signup ? signupRecruiter : loginRecruiter)(email.trim(), password)
      const session = await acceptSession(result.access_token)

      setPassword('')
      setConfirmation('')
      onLogin(session)
    } catch (error) {
      setError(
        error instanceof Error
          ? error.message
          : 'Unable to sign in. Please try again.',
      )
    } finally {
      submittingRef.current = false
      setBusy(false)
    }
  }

  return (
    <section
      aria-labelledby="login-title"
      className="mx-auto mt-8 max-w-md rounded-2xl border
        border-slate-200 bg-white p-6 shadow-sm"
    >
      <h2 id="login-title" className="text-xl font-semibold">
        {signup ? 'Create recruiter account' : 'Recruiter sign in'}
      </h2>

      <p className="mt-2 text-sm text-slate-500">
        {signup ? 'Create an account to start your recruiter workspace.' : 'Sign in to create and manage job descriptions.'}
      </p>

      <form
        className="mt-6 space-y-4"
        onSubmit={(event) => void handleSubmit(event)}
        aria-busy={busy}
      >
        <div>
          <label htmlFor="login-email" className="text-sm font-medium">
            Email
          </label>

          <input
            id="login-email"
            type="email"
            autoComplete="username"
            required
            maxLength={254}
            value={email}
            disabled={busy}
            onChange={(event) => setEmail(event.target.value)}
            className="mt-1 block w-full rounded-xl border
              border-slate-300 px-3 py-2 disabled:opacity-60"
          />
        </div>

        <div>
          <label htmlFor="login-password" className="text-sm font-medium">
            Password
          </label>

          <input
            id="login-password"
            type="password"
            autoComplete={signup ? 'new-password' : 'current-password'}
            minLength={signup ? 12 : undefined}
            required
            maxLength={1024}
            value={password}
            disabled={busy}
            onChange={(event) => setPassword(event.target.value)}
            className="mt-1 block w-full rounded-xl border
              border-slate-300 px-3 py-2 disabled:opacity-60"
          />
        </div>

        {signup && <div>
          <p className="text-sm text-slate-500">Use at least 12 characters.</p>
          <label htmlFor="confirm-password" className="text-sm font-medium">Confirm password</label>
          <input id="confirm-password" type="password" autoComplete="new-password"
            required minLength={12} maxLength={1024} value={confirmation} disabled={busy}
            onChange={event => setConfirmation(event.target.value)}
            className="mt-1 block w-full rounded-xl border border-slate-300 px-3 py-2 disabled:opacity-60" />
        </div>}

        {error && (
          <p role="alert" className="text-sm text-red-700">
            {error}
          </p>
        )}

        <button
          type="submit"
          className="button-primary w-full"
          disabled={busy || !email.trim() || !password}
        >
          {busy ? (signup ? 'Creating account...' : 'Signing in...') : (signup ? 'Create account' : 'Sign in')}
        </button>
      </form>
      <button type="button" disabled={busy} className="mt-4 text-sm underline"
        onClick={() => { setSignup(!signup); setError(null); setPassword(''); setConfirmation('') }}>
        {signup ? 'Already have an account? Sign in' : 'New here? Create an account'}
      </button>
    </section>
  )
}
