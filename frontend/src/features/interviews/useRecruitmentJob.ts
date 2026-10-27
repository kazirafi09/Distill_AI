import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { listJobDescriptions } from '../jd/api'
import type { JobDescriptionSummary } from '../jd/api'

export async function loadRecruitmentJobs(token: string, signal?: AbortSignal) {
  const jobs: JobDescriptionSummary[] = []
  for (let offset = 0; ; offset += 10) {
    const page = await listJobDescriptions(token, offset, signal)
    jobs.push(...page.items)
    if (page.items.length < 10) return jobs
  }
}

export function useRecruitmentJob(token: string) {
  const [params, setParams] = useSearchParams()
  const [jobs, setJobs] = useState<JobDescriptionSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [revision, setRevision] = useState(0)
  useEffect(() => {
    const controller = new AbortController()
    void loadRecruitmentJobs(token, controller.signal).then(next => {
      if (!controller.signal.aborted) { setJobs(next); setError(''); setLoading(false) }
    }).catch(caught => {
      if (!controller.signal.aborted) { setError(caught instanceof Error ? caught.message : 'Could not load jobs.'); setLoading(false) }
    })
    return () => controller.abort()
  }, [token, revision])
  const requested = params.get('job')
  const job = jobs.find(item => item.id === requested) ?? jobs[0]
  function selectJob(id: string) {
    setParams(current => { current.set('job', id); current.delete('candidate'); current.delete('run'); return current })
  }
  return { jobs, job, loading, error, selectJob, retry: () => { setLoading(true); setRevision(value => value + 1) } }
}
