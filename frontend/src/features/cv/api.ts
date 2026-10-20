import { apiRequest } from '../../services/api'

export type CVSource = {
  id: string
  job_description_id: string
  drive_folder_id: string
  spreadsheet_id: string | null
  sheet_tab_id: number | null
}

export type CVSourceCheck = {
  source_id: string
  job_description_id: string
  new_cv_count: number
  last_checked_at: string | null
  last_success_at: string | null
  error_code: string | null
}

export async function listSourceChecks(token: string, signal?: AbortSignal) {
  const checks: CVSourceCheck[] = []
  for (let offset = 0; ; offset += 100) {
    const page = await apiRequest<CVSourceCheck[]>(`/cv/source-checks?limit=100&offset=${offset}`, { token, signal })
    checks.push(...page)
    if (page.length < 100) return checks
  }
}

export type CandidateApplication = {
  id: string
  candidate_id: string
  full_name: string
  email: string
  submitted_at_text: string | null
  created_at: string
  cv_documents: {
    id: string
    drive_file_id: string
    original_filename: string | null
    download_status: string | null
    file_version_id: string | null
  }[]
}

export type ParseResult = {
  file_version_id: string
  status: string
  ocr_used: boolean
  extraction_method?: string
  warnings: string[]
  error_code?: string | null
  profile: Record<string, unknown> | null
  evidence: { claim_path: string; quote: string; source_id: string; ocr_derived: boolean }[]
}

export function parseCV(token: string, id: string) {
  return apiRequest<ParseResult>(`/cv/versions/${encodeURIComponent(id)}/parse`, { method: 'POST', token })
}

export function getCVProfile(token: string, id: string, signal?: AbortSignal) {
  return apiRequest<ParseResult>(`/cv/versions/${encodeURIComponent(id)}/profile`, { token, signal })
}

export function parseJobCVs(token: string, jobId: string, offset: number) {
  return apiRequest<{ results: ParseResult[]; next_offset: number | null }>(
    `/cv/jobs/${encodeURIComponent(jobId)}/parse?offset=${offset}&limit=1`, { method: 'POST', token })
}

export type FetchResult = {
  downloaded: number
  skipped: number
  failed: number
  errors: { filename?: string; error_code: string }[]
  sheet: {
    rows_imported: number
    rows_skipped: number
    rows_failed: number
    candidates_created: number
    applications_created: number
    cvs_linked: number
    errors: { row_number?: number; error_code: string }[]
  } | null
}

export async function listSources(token: string, signal?: AbortSignal) {
  const sources: CVSource[] = []
  for (let offset = 0; ; offset += 100) {
    const page = await apiRequest<CVSource[]>(`/cv/sources?limit=100&offset=${offset}`, { token, signal })
    sources.push(...page)
    if (page.length < 100) return sources
  }
}

export async function listApplications(token: string, jobId: string, signal?: AbortSignal) {
  const applications: CandidateApplication[] = []
  for (let offset = 0; ; offset += 100) {
    const page = await apiRequest<CandidateApplication[]>(
      `/cv/applications?job_description_id=${encodeURIComponent(jobId)}&limit=100&offset=${offset}`,
      { token, signal },
    )
    applications.push(...page)
    if (page.length < 100) return applications
  }
}

export async function getApplication(token: string, jobId: string, applicationId: string, signal?: AbortSignal) {
  const query = new URLSearchParams({ job_description_id: jobId, application_id: applicationId, limit: '1' })
  const items = await apiRequest<CandidateApplication[]>(`/cv/applications?${query}`, { token, signal })
  return items.find(item => item.id === applicationId) ?? null
}

export function linkSource(token: string, jobId: string, folderId: string) {
  return apiRequest<CVSource>('/cv/sources', {
    method: 'POST', token,
    body: { job_description_id: jobId, drive_folder_id: folderId },
  })
}

export function linkSheet(token: string, sourceId: string, spreadsheetId: string, tabId: number) {
  return apiRequest<CVSource>(`/cv/sources/${sourceId}/sheet`, {
    method: 'PUT', token,
    body: { spreadsheet_id: spreadsheetId, sheet_tab_id: tabId },
  })
}

export function fetchSource(token: string, sourceId: string) {
  return apiRequest<FetchResult>(`/cv/sources/${sourceId}/fetch`, { method: 'POST', token })
}

export function parseFolder(value: string) {
  const input = value.trim()
  if (/^[A-Za-z0-9_-]+$/.test(input)) return input
  try {
    const url = new URL(input)
    if (url.protocol === 'https:' && url.hostname === 'drive.google.com') {
      const id = url.pathname.match(/^\/drive\/(?:u\/\d+\/)?folders\/([A-Za-z0-9_-]+)(?:\/|$)/)?.[1]
      if (id) return id
    }
  } catch { /* A plain ID or supported URL is required. */ }
  throw new Error('Enter a Google Drive folder URL or folder ID.')
}

export function parseSheet(value: string, tabValue: string) {
  const input = value.trim()
  let spreadsheetId = input
  let gid = tabValue.trim()
  if (!/^[A-Za-z0-9_-]+$/.test(input)) {
    try {
      const url = new URL(input)
      const id = url.pathname.match(/^\/spreadsheets\/d\/([A-Za-z0-9_-]+)(?:\/|$)/)?.[1]
      if (url.protocol !== 'https:' || url.hostname !== 'docs.google.com' || !id) throw new Error()
      spreadsheetId = id
      if (!gid) gid = new URLSearchParams(url.hash.slice(1)).get('gid') ?? url.searchParams.get('gid') ?? ''
    } catch { throw new Error('Enter a Google Sheets URL or spreadsheet ID.') }
  }
  const tabId = Number(gid)
  if (!/^\d+$/.test(gid) || !Number.isSafeInteger(tabId)) {
    throw new Error('Enter the response tab ID (gid), or paste a Sheet URL containing gid.')
  }
  return { spreadsheetId, tabId }
}
