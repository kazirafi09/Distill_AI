import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'

import { ApiError, apiRequest } from '../../services/api'
import { listJobDescriptions } from '../jd/api'
import type { JobDescriptionSummary } from '../jd/api'

type CVSource = {
  id: string
  job_description_id: string
  drive_folder_id: string
  created_at: string
  updated_at: string
}

type FetchResult = {
  source_id: string
  downloaded: number
  skipped: number
  failed: number
  errors: {
    drive_file_id: string
    filename: string
    error_code: string
    error_type: string
  }[]
}

type PageData = {
  key: string
  jobs: JobDescriptionSummary[]
  sources: CVSource[]
  error: string | null
}

function errorMessage(error: unknown) {
  if (error instanceof ApiError && error.status === 401) {
    return 'Your session expired. Sign out and sign in again.'
  }

  return error instanceof Error
    ? error.message
    : 'Unable to complete the request.'
}

async function loadSources(token: string, signal: AbortSignal) {
  const sources: CVSource[] = []

  for (let offset = 0; ; offset += 100) {
    const page = await apiRequest<CVSource[]>(
      `/cv/sources?limit=100&offset=${offset}`,
      { token, signal },
    )
    sources.push(...page)

    if (page.length < 100) return sources
  }
}

export default function CVScreening({ token }: { token: string }) {
  const [offset, setOffset] = useState(0)
  const [reloadCount, setReloadCount] = useState(0)
  const [selectedId, setSelectedId] = useState('')
  const [folderId, setFolderId] = useState('')
  const [data, setData] = useState<PageData | null>(null)
  const [busy, setBusy] = useState<'linking' | 'fetching' | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [result, setResult] = useState<FetchResult | null>(null)
  const submittingRef = useRef(false)

  const pageKey = `${offset}:${reloadCount}`
  const loading = data?.key !== pageKey
  const jobs = loading ? [] : data?.jobs ?? []
  const sources = loading ? [] : data?.sources ?? []
  const listError = loading ? null : data?.error
  const job = jobs.find((item) => item.id === selectedId) ?? jobs[0]
  const source = sources.find(
    (item) => item.job_description_id === job?.id,
  )
  const currentResult =
    result?.source_id === source?.id ? result : null

  useEffect(() => {
    const controller = new AbortController()

    async function loadPage() {
      try {
        const [jobPage, sourceList] = await Promise.all([
          listJobDescriptions(token, offset, controller.signal),
          loadSources(token, controller.signal),
        ])

        if (!controller.signal.aborted) {
          setData({
            key: pageKey,
            jobs: jobPage.items,
            sources: sourceList,
            error: null,
          })
        }
      } catch (loadError) {
        if (!controller.signal.aborted) {
          setData({
            key: pageKey,
            jobs: [],
            sources: [],
            error: errorMessage(loadError),
          })
        }
      }
    }

    void loadPage()
    return () => controller.abort()
  }, [token, offset, pageKey])

  function clearFeedback() {
    setError(null)
    setNotice(null)
    setResult(null)
  }

  function changePage(nextOffset: number) {
    setOffset(nextOffset)
    setSelectedId('')
    setFolderId('')
    clearFeedback()
  }

  async function handleLink(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!job || submittingRef.current) return

    submittingRef.current = true
    setBusy('linking')
    clearFeedback()

    try {
      await apiRequest<CVSource>('/cv/sources', {
        method: 'POST',
        token,
        body: {
          job_description_id: job.id,
          drive_folder_id: folderId.trim(),
        },
      })

      setNotice('CV folder linked successfully.')
      setFolderId('')
      setReloadCount((value) => value + 1)
    } catch (linkError) {
      setError(errorMessage(linkError))
    } finally {
      submittingRef.current = false
      setBusy(null)
    }
  }

  async function handleFetch() {
    if (!source || submittingRef.current) return

    submittingRef.current = true
    setBusy('fetching')
    clearFeedback()

    try {
      const response = await apiRequest<FetchResult>(
        `/cv/sources/${source.id}/fetch`,
        { method: 'POST', token },
      )
      setResult(response)
    } catch (fetchError) {
      setError(errorMessage(fetchError))
    } finally {
      submittingRef.current = false
      setBusy(null)
    }
  }

  return (
    <section className="space-y-6">
      <header className="workspace-header">
        <div>
          <p className="workspace-eyebrow">Recruitment workspace</p>
          <h1 className="text-3xl font-bold">CV Screening</h1>
          <p className="mt-2 text-sm text-slate-500">
            Fetch original CV files from the Drive folder linked to a job.
            Parsing and scoring will be added later.
          </p>
        </div>

        <button
          type="button"
          className="button-secondary"
          disabled={loading || busy !== null}
          onClick={() => {
            clearFeedback()
            setReloadCount((value) => value + 1)
          }}
        >
          Refresh list
        </button>
      </header>

      <div className="rounded-xl border border-slate-200 bg-white p-5">
        {loading ? (
          <p role="status">Loading jobs and CV folders...</p>
        ) : listError ? (
          <p role="alert" className="text-red-700">{listError}</p>
        ) : !job ? (
          <p className="text-sm text-slate-500">
            {offset === 0
              ? 'Approve a job description before linking a CV folder.'
              : 'No more job descriptions on this page.'}
          </p>
        ) : (
          <>
            <label htmlFor="cv-job" className="block font-semibold">
              Job description
            </label>
            <select
              id="cv-job"
              value={job.id}
              disabled={busy !== null}
              onChange={(event) => {
                setSelectedId(event.target.value)
                setFolderId('')
                clearFeedback()
              }}
              className="mt-2 w-full rounded-lg border border-slate-300 p-3"
            >
              {jobs.map((item) => (
                <option key={item.id} value={item.id}>
                  Job #{item.number} - {item.title}
                </option>
              ))}
            </select>

            {source ? (
              <div className="mt-5 space-y-3">
                <p className="text-sm text-slate-600">
                  Linked Drive folder:{' '}
                  <a
                    href={`https://drive.google.com/drive/folders/${source.drive_folder_id}`}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="break-all text-emerald-700 underline"
                  >
                    Open folder
                  </a>
                </p>

                <button
                  type="button"
                  className="button-secondary"
                  disabled={busy !== null}
                  onClick={() => void handleFetch()}
                >
                  {busy === 'fetching' ? 'Fetching CVs...' : 'Fetch CVs'}
                </button>

                <p className="text-sm text-slate-500">
                  Completed, unchanged files are skipped.
                  New or changed CVs are saved locally.
                </p>
              </div>
            ) : (
              <form onSubmit={handleLink} className="mt-5 space-y-3">
                <label htmlFor="cv-folder" className="block font-semibold">
                  Google Drive folder ID
                </label>
                <input
                  id="cv-folder"
                  value={folderId}
                  onChange={(event) => setFolderId(event.target.value)}
                  required
                  maxLength={255}
                  pattern="[A-Za-z0-9_-]+"
                  disabled={busy !== null}
                  placeholder="Paste the folder ID"
                  className="w-full rounded-lg border border-slate-300 p-3"
                />
                <p className="text-sm text-slate-500">
                  Enter the ID after /folders/ in the Drive URL.
                  Use a direct child folder of your configured CV parent.
                </p>
                <button
                  type="submit"
                  className="button-secondary"
                  disabled={busy !== null || !folderId.trim()}
                >
                  {busy === 'linking' ? 'Linking...' : 'Link CV folder'}
                </button>
              </form>
            )}
          </>
        )}

        <div className="mt-5 flex items-center justify-between gap-3">
          <button
            type="button"
            className="button-secondary"
            disabled={loading || busy !== null || offset === 0}
            onClick={() => changePage(Math.max(0, offset - 10))}
          >
            Previous
          </button>
          <span className="text-sm text-slate-500">
            Page {offset / 10 + 1}
          </span>
          <button
            type="button"
            className="button-secondary"
            disabled={
              loading || busy !== null || Boolean(listError) || jobs.length < 10
            }
            onClick={() => changePage(offset + 10)}
          >
            Next
          </button>
        </div>
      </div>

      {busy === 'fetching' && (
        <p role="status" className="text-sm text-slate-600">
          Downloading CVs. Please keep this page open until fetching finishes.
        </p>
      )}

      {error && (
        <p role="alert" className="text-sm text-red-700">{error}</p>
      )}
      {notice && (
        <p role="status" className="text-sm text-emerald-700">{notice}</p>
      )}

      {currentResult && (
        <section
          aria-labelledby="cv-result-title"
          className="rounded-xl border border-slate-200 bg-white p-5"
        >
          <h2 id="cv-result-title" className="font-semibold">
            Latest fetch result
          </h2>
          <p role="status" className="mt-2 text-sm">
            Downloaded: {currentResult.downloaded}
            {' | '}Skipped: {currentResult.skipped}
            {' | '}Failed: {currentResult.failed}
          </p>

          {currentResult.errors.length > 0 && (
            <ul className="mt-3 space-y-2 text-sm text-red-700">
              {currentResult.errors.map((item) => (
                <li key={item.drive_file_id}>
                  {item.filename}: {item.error_code}
                </li>
              ))}
            </ul>
          )}
        </section>
      )}
    </section>
  )
}