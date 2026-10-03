import { useEffect, useState } from 'react'
import './App.css'

export default function App() {
  const [connection, setConnection] = useState('Checking connection…')

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
      <h1>Distill AI</h1>
      <p>Recruitment & Interview Intelligence Platform</p>
      <p role="status">{connection}</p>
    </main>
  )
}