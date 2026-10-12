import { useEffect, useState } from 'react'

type Entry = {
  job_title: string
  instructions: string
  expires_at: string
  interview_ready: boolean
}

export default function InterviewEntry() {
  const [token] = useState(() => window.location.hash.slice(1))
  const [entry, setEntry] = useState<Entry | null>(null)
  const [error, setError] = useState(() => /^[A-Za-z0-9_-]{43}$/.test(token)
    ? '' : 'This interview link is invalid or no longer active.')

  useEffect(() => {
    const controller = new AbortController()
    if (!/^[A-Za-z0-9_-]{43}$/.test(token)) {
      return
    }

    async function load() {
      try {
        const response = await fetch('/api/interviews/entry', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ token }),
          signal: controller.signal,
          cache: 'no-store',
          referrerPolicy: 'no-referrer',
        })
        if (!response.ok) {
          throw new Error(response.status === 410
            ? 'This interview link is invalid or no longer active.'
            : 'Could not verify the interview link. Please try again later.')
        }
        const data: Entry = await response.json()
        if (!controller.signal.aborted) setEntry(data)
      } catch (caught) {
        if (!controller.signal.aborted) {
          setError(caught instanceof Error ? caught.message : 'Could not verify the interview link.')
        }
      }
    }
    void load()
    return () => controller.abort()
  }, [token])

  return <main className="app">
    <h1>Join your interview</h1>
    {error && <p role="alert">{error}</p>}
    {!entry && !error && <p role="status">Checking your interview link…</p>}
    {entry && <>
      <h2>{entry.job_title}</h2>
      <p>{entry.instructions}</p>
      <p>Your link is valid until {new Date(entry.expires_at).toLocaleString()}.</p>
      {!entry.interview_ready && <p role="status">The interview session is not available yet. Please contact your recruiter for the next steps.</p>}
    </>}
  </main>
}
