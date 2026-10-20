import { apiRequest } from '../../services/api'

export type DashboardData = {
  updated_at: string
  totals: { jobs: number; applications: number; waiting: number; attention: number }
  source_count: number
  active_session_count: number
  jobs: {
    id: string; number: number; title: string; status: string
    applied: number; screened: number; invited: number; interviewed: number; assessed: number
    screening_status: string | null
  }[]
  attention: { count: number | null; title: string; detail: string; action: string; href: string; priority: number }[]
  sessions: { id: string; job_id: string; name: string; job_title: string; status: string; starts_at: string | null }[]
  activity: { at: string; text: string; href: string }[]
}

export function loadDashboard(token: string, signal: AbortSignal) {
  return apiRequest<DashboardData>('/dashboard', { token, signal })
}
