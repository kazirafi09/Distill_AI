import { useEffect, useRef, useState } from 'react'
import Markdown from 'react-markdown'

import { ApiError } from '../../services/api'
import {
  getJobDescription,
  listJobDescriptions,
} from './api'
import type {
  JobDescriptionSummary,
  SavedJobDescription,
} from './api'
import ReferenceDocuments from './ReferenceDocuments'

type MyJobDescriptionsProps = {
  token: string
  refreshKey: number
}

function formatDate(value: string) {
  return new Date(value).toLocaleString('en-GB', {
    timeZone: 'Asia/Dhaka',
  })
}

function errorMessage(error: unknown) {
  if (error instanceof ApiError && error.status === 401) {
    return 'Your session expired. Sign out and sign in again.'
  }

  return error instanceof Error
    ? error.message
    : 'Unable to load job descriptions.'
}

export default function MyJobDescriptions({
  token,
  refreshKey,
}: MyJobDescriptionsProps) {
  const [pagination, setPagination] = useState({ offset: 0, refreshKey })
  // New saves are sorted first. Return to that page when the chat saves a JD.
  const offset = pagination.refreshKey === refreshKey ? pagination.offset : 0
  function setOffset(next: number | ((current: number) => number)) {
    setPagination((current) => {
      const currentOffset = current.refreshKey === refreshKey ? current.offset : 0
      return { offset: typeof next === 'function' ? next(currentOffset) : next, refreshKey }
    })
  }
  const [reloadCount, setReloadCount] = useState(0)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const detailDialogRef = useRef<HTMLDialogElement>(null)

  const [listResult, setListResult] = useState<{
    key: string
    items: JobDescriptionSummary[]
    error: string | null
  } | null>(null)

  const [detailResult, setDetailResult] = useState<{
    id: string
    document: SavedJobDescription | null
    error: string | null
  } | null>(null)

  const listKey = `${offset}:${refreshKey}:${reloadCount}`
  const loadingList = listResult?.key !== listKey
  const items = loadingList ? [] : listResult?.items ?? []
  const listError = loadingList ? null : listResult?.error

  const currentDetail =
    detailResult?.id === selectedId ? detailResult : null

  useEffect(() => {
    const controller = new AbortController()

    async function loadList() {
      try {
        const response = await listJobDescriptions(
          token,
          offset,
          controller.signal,
        )

        if (!controller.signal.aborted) {
          setListResult({
            key: listKey,
            items: response.items,
            error: null,
          })
        }
      } catch (error) {
        if (!controller.signal.aborted) {
          setListResult({
            key: listKey,
            items: [],
            error: errorMessage(error),
          })
        }
      }
    }

    void loadList()
    return () => controller.abort()
  }, [token, offset, listKey])

  useEffect(() => {
    if (!selectedId) return

    const controller = new AbortController()
    const descriptionId = selectedId

    async function loadDocument() {
      try {
        const document = await getJobDescription(
          token,
          descriptionId,
          controller.signal,
        )

        if (!controller.signal.aborted) {
          setDetailResult({
            id: descriptionId,
            document,
            error: null,
          })
        }
      } catch (error) {
        if (!controller.signal.aborted) {
          setDetailResult({
            id: descriptionId,
            document: null,
            error: errorMessage(error),
          })
        }
      }
    }

    void loadDocument()
    return () => controller.abort()
  }, [token, selectedId])

  useEffect(() => {
    const dialog = detailDialogRef.current

    if (selectedId && dialog && !dialog.open) {
      dialog.showModal()
    }
  }, [selectedId])

  return (
    <section
      aria-labelledby="saved-jds-title"
      className="jd-library"
    >
      <ReferenceDocuments token={token} />
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 id="saved-jds-title" className="text-lg font-semibold">
          Your Job Descriptions
        </h2>

        <button
          type="button"
          className="button-secondary"
          disabled={loadingList}
          onClick={() => {
            setOffset(0)
            setReloadCount((count) => count + 1)
          }}
        >
          Refresh list
        </button>
      </div>

      <p className="mt-2 text-sm text-slate-500">
        Approved job descriptions. Times shown in Bangladesh time.
      </p>

      {loadingList ? (
        <p role="status" className="mt-4 text-sm text-slate-500">
          Loading job descriptions...
        </p>
      ) : listError ? (
        <p role="alert" className="mt-4 text-sm text-red-700">
          {listError}
        </p>
      ) : items.length === 0 ? (
        <p className="mt-4 text-sm text-slate-500">
          {offset === 0
            ? 'No saved job descriptions yet. Approve a draft to save one.'
            : 'No more job descriptions on this page.'}
        </p>
      ) : (
        <ul className="mt-4 space-y-3">
          {items.map((item) => (
            <li key={item.id}>
              <button
                type="button"
                aria-haspopup="dialog"
                aria-pressed={selectedId === item.id}
                onClick={() => setSelectedId(item.id)}
                className={`w-full rounded-xl border p-4 text-left ${
                  selectedId === item.id
                    ? 'border-emerald-500 bg-emerald-50'
                    : 'border-slate-200 hover:bg-slate-50'
                }`}
              >
                <span className="block font-semibold">
                  Job #{item.number} — {item.title}
                </span>

                <time
                  dateTime={item.created_at}
                  className="mt-1 block text-sm text-slate-500"
                >
                  Created {formatDate(item.created_at)}
                </time>
              </button>
            </li>
          ))}
        </ul>
      )}

      <div className="mt-4 flex items-center justify-between gap-3">
        <button
          type="button"
          className="button-secondary"
          disabled={loadingList || offset === 0}
          onClick={() => setOffset((value) => Math.max(0, value - 10))}
        >
          Previous
        </button>

        <span className="text-sm text-slate-500">
          Page {offset / 10 + 1}
        </span>

        <button
          type="button"
          className="button-secondary"
          disabled={loadingList || Boolean(listError) || items.length < 10}
          onClick={() => setOffset((value) => value + 10)}
        >
          Next
        </button>
      </div>

      {selectedId && (
        <dialog
          ref={detailDialogRef}
          className="jd-detail-dialog"
          aria-labelledby="saved-jd-detail-title"
          onClose={() => setSelectedId(null)}
        >
          <div className="mb-4 flex items-center justify-between gap-3">
            <h3 id="saved-jd-detail-title" className="font-semibold">
              {currentDetail?.document
                ? `Job #${currentDetail.document.number} — ${currentDetail.document.title}`
                : 'Saved job description'}
            </h3>

            <button
              type="button"
              className="button-secondary"
              onClick={() => detailDialogRef.current?.close()}
            >
              Close
            </button>
          </div>

          {!currentDetail ? (
            <p role="status" className="text-sm text-slate-500">
              Loading job description...
            </p>
          ) : currentDetail.error ? (
            <p role="alert" className="text-sm text-red-700">
              {currentDetail.error}
            </p>
          ) : currentDetail.document ? (
            <>
              <p className="mb-4 text-sm text-slate-500">
                Created {formatDate(currentDetail.document.created_at)}
              </p>

              <div className="markdown text-sm text-slate-800">
                <Markdown skipHtml>
                  {currentDetail.document.content}
                </Markdown>
              </div>
            </>
          ) : null}
        </dialog>
      )}
    </section>
  )
}
