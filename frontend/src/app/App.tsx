import { useEffect, useState } from 'react'

import LoginForm from '../features/auth/LoginForm'
import type { RecruiterSession } from '../services/api'
import './App.css'
import RecruiterChat from '../features/jd/RecruiterChat'
import MyJobDescriptions from '../features/jd/MyJobDescriptions'


export default function App() {
  const [connection, setConnection] = useState('Checking connection…')
  const [session, setSession] = useState<RecruiterSession | null>(null)
  const [jdRefreshKey, setJDRefreshKey] = useState(0)

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

  return (
    <main className="app">
      <h1 className="text-3xl font-bold">Distill AI</h1>
      <p>Recruitment & Interview Intelligence Platform</p>
      <p role="status" className="text-sm text-slate-500">
        {connection}
      </p>

      {session ? (
        <section
          className="mt-8 rounded-2xl border border-slate-200
            bg-white p-6"
        >
          <div className="flex flex-wrap items-center justify-between gap-4">
            <div>
              <h2 className="text-xl font-semibold">
                Recruiter workspace
              </h2>
              <p className="mt-1 text-sm text-slate-500">
                Signed in as {session.user.email}
              </p>
            </div>

            <button
              type="button"
              className="button-secondary"
              onClick={() => setSession(null)}
            >
              Sign out
            </button>
          </div>
          <RecruiterChat
            key={session.user.id}
            token={session.token}
            userId={session.user.id}
            onSaved={() => setJDRefreshKey((value) => value + 1)}
          />

          <MyJobDescriptions
            key={session.user.id}
            token={session.token}
            refreshKey={jdRefreshKey}
          />
        </section>
      ) : (
        <LoginForm onLogin={setSession} />
      )}
    </main>
  )
}