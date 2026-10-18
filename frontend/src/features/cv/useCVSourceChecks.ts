import { useCallback, useEffect, useState } from 'react'
import { listSourceChecks } from './api'
import type { CVSourceCheck } from './api'

export function useCVSourceChecks(token?: string) {
  const [result, setResult] = useState<{ token: string; checks: CVSourceCheck[]; error: string } | null>(null)
  const [revision, setRevision] = useState(0)
  const refresh = useCallback(() => setRevision(value => value + 1), [])
  useEffect(() => {
    if (!token) return
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout>
    async function poll() {
      try {
        const checks = await listSourceChecks(token!, controller.signal)
        if (!controller.signal.aborted) setResult({ token: token!, checks, error: '' })
      } catch (caught) {
        if (!controller.signal.aborted) setResult(current => ({ token: token!,
          checks: current && current.token === token ? current.checks : [],
          error: caught instanceof Error ? caught.message : 'Could not refresh CV notifications.' }))
      } finally {
        if (!controller.signal.aborted) timer = setTimeout(() => void poll(), 30000)
      }
    }
    void poll()
    return () => { controller.abort(); clearTimeout(timer) }
  }, [token, revision])
  const active = result && result.token === token ? result : null
  const checks = active?.checks ?? []
  return { checks, error: active?.error ?? '', refresh,
    total: checks.reduce((sum, item) => sum + item.new_cv_count, 0) }
}
