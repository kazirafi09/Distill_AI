import { useRef, useState } from 'react'
import type { FormEvent } from 'react'

import {
  getCurrentUser,
  loginRecruiter,
} from '../../services/api'
import type { RecruiterSession } from '../../services/api'

type LoginFormProps = {
  onLogin: (session: RecruiterSession) => void
}

export default function LoginForm({ onLogin }: LoginFormProps) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
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
      const result = await loginRecruiter(email.trim(), password)
      const user = await getCurrentUser(result.access_token)

      if (user.role !== 'recruiter') {
        throw new Error('Please sign in with a recruiter account.')
      }

      setPassword('')
      onLogin({
        token: result.access_token,
        user,
      })
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
        Recruiter sign in
      </h2>

      <p className="mt-2 text-sm text-slate-500">
        Sign in to create and manage job descriptions.
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
            autoComplete="current-password"
            required
            maxLength={1024}
            value={password}
            disabled={busy}
            onChange={(event) => setPassword(event.target.value)}
            className="mt-1 block w-full rounded-xl border
              border-slate-300 px-3 py-2 disabled:opacity-60"
          />
        </div>

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
          {busy ? 'Signing in...' : 'Sign in'}
        </button>
      </form>
    </section>
  )
}