import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'

import { ApiError } from '../../services/api'
import {
  listReferences,
  retryReferenceIndex,
  uploadReference,
  listQuestionBanks,
  uploadQuestionBank,
  retryQuestionBankIndex,
  archiveQuestionBank,
} from './api'
import type { JDReference, ReferenceDocumentType, QuestionBank } from './api'

type UploadDocumentType = ReferenceDocumentType | 'question_bank'
type ReferenceItem = Omit<JDReference, 'document_type'> & {
  document_type: UploadDocumentType
  is_archived?: boolean
  warnings?: string[]
}

function asReferenceItem(bank: QuestionBank): ReferenceItem {
  const statuses = { pending: 'pending', processing: 'indexing', ready: 'indexed', failed: 'failed' } as const
  return { ...bank, document_type: 'question_bank', index_status: statuses[bank.status] }
}

type ReferenceDocumentsProps = {
  token: string
}

const statusLabels = {
  pending: 'Pending',
  indexing: 'Indexing',
  indexed: 'Ready',
  failed: 'Indexing failed',
}

function getErrorMessage(error: unknown) {
  if (error instanceof ApiError && error.status === 401) {
    return 'Your session expired. Sign out and sign in again.'
  }

  return error instanceof Error
    ? error.message
    : 'Unable to complete the reference request.'
}

export default function ReferenceDocuments({
  token,
}: ReferenceDocumentsProps) {
  const [file, setFile] = useState<File | null>(null)
  const [documentType, setDocumentType] =
    useState<UploadDocumentType>('prior_jd')

  const [offset, setOffset] = useState(0)
  const [reloadCount, setReloadCount] = useState(0)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const fileInputRef = useRef<HTMLInputElement>(null)
  const requestRef = useRef(false)

  const [listResult, setListResult] = useState<{
    key: string
    items: ReferenceItem[]
    error: string | null
  } | null>(null)

  const questionBanks = documentType === 'question_bank'
  const listKey = `${questionBanks}:${offset}:${reloadCount}`
  const loading = listResult?.key !== listKey
  const items = loading ? [] : listResult?.items ?? []
  const listError = loading ? null : listResult?.error

  useEffect(() => {
    const controller = new AbortController()

    async function load() {
      try {
        const items = questionBanks
          ? (await listQuestionBanks(token, offset, controller.signal)).map(asReferenceItem)
          : await listReferences(token, offset, controller.signal)

        if (!controller.signal.aborted) {
          setListResult({ key: listKey, items, error: null })
        }
      } catch (error) {
        if (!controller.signal.aborted) {
          setListResult({
            key: listKey,
            items: [],
            error: getErrorMessage(error),
          })
        }
      }
    }

    void load()
    return () => controller.abort()
  }, [token, offset, listKey, questionBanks])

  function refresh() {
    setReloadCount((value) => value + 1)
  }

  async function runOperation(
    operation: () => Promise<ReferenceItem>,
    clearFile = false,
  ) {
    if (requestRef.current) return

    requestRef.current = true
    setBusy(true)
    setError(null)
    setNotice(null)

    try {
      const reference = await operation()

      setNotice(
        reference.is_archived
          ? `${reference.filename} is archived and excluded from interview searches.`
          : reference.index_status === 'indexed'
          ? `${reference.filename} is ready for reference retrieval.`
          : `${reference.filename} was saved, but processing did not finish. Review its status below.`,
      )

      if (clearFile) {
        setFile(null)
        setOffset(0)

        if (fileInputRef.current) {
          fileInputRef.current.value = ''
        }
      }

      refresh()
    } catch (error) {
      setError(getErrorMessage(error))
    } finally {
      requestRef.current = false
      setBusy(false)
    }
  }

  function handleUpload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()

    if (!file || requestRef.current) return

    if (!/\.(pdf|docx)$/i.test(file.name)) {
      setError('Choose a PDF or DOCX file.')
      setNotice(null)
      return
    }

    if (file.size === 0 || file.size > 10 * 1024 * 1024) {
      setError('Choose a nonempty file no larger than 10 MB.')
      setNotice(null)
      return
    }

    void runOperation(
      () => questionBanks
        ? uploadQuestionBank(token, file).then(asReferenceItem)
        : uploadReference(token, file, documentType as ReferenceDocumentType),
      true,
    )
  }

  return (
    <section
      aria-labelledby="reference-documents-title"
      className="mb-6 border-b border-slate-200 pb-6"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2
          id="reference-documents-title"
          className="text-lg font-semibold"
        >
          Reference Documents
        </h2>

        <button
          type="button"
          className="button-secondary"
          disabled={busy || loading}
          onClick={refresh}
        >
          Refresh
        </button>
      </div>

      <p className="mt-2 text-sm text-slate-500">
        Upload prior JDs or company policies for JD creation, or question banks
        for interview assistance. Question banks can be reused across your jobs.
      </p>

      <form
        className="mt-4 space-y-3"
        onSubmit={handleUpload}
        aria-busy={busy}
      >
        <label className="block text-sm font-medium">
          Document type
          <select
            value={documentType}
            disabled={busy}
            onChange={(event) => {
              setDocumentType(event.target.value as UploadDocumentType)
              setOffset(0)
              setNotice(null)
              setError(null)
            }}
            className="mt-1 block w-full rounded-xl border
              border-slate-200 bg-white px-3 py-2 text-sm"
          >
            <option value="prior_jd">Prior job description</option>
            <option value="company_policy">Company policy</option>
            <option value="question_bank">Question bank</option>
          </select>
        </label>

        <label className="block text-sm font-medium">
          Reference file
          <input
            ref={fileInputRef}
            type="file"
            accept=".pdf,.docx"
            disabled={busy}
            onChange={(event) => {
              setFile(event.target.files?.[0] ?? null)
              setError(null)
              setNotice(null)
            }}
            className="mt-1 block w-full rounded-xl border
              border-slate-200 p-2 text-xs
              file:mr-2 file:rounded-lg file:border-0
              file:bg-emerald-50 file:px-3 file:py-2
              file:text-emerald-800"
          />
        </label>

        <p className="text-xs text-slate-500">
          PDF or DOCX · Maximum 10 MB
        </p>

        <button
          type="submit"
          className="button-primary w-full"
          disabled={busy || !file}
        >
          {busy ? 'Working…' : 'Upload and index'}
        </button>
      </form>

      {busy && (
        <p role="status" className="mt-3 text-xs text-slate-500">
          Processing the reference. Embedding may take a moment.
        </p>
      )}

      {error && (
        <p role="alert" className="mt-3 text-sm text-red-700">
          {error}
        </p>
      )}

      {notice && (
        <p role="status" className="mt-3 text-sm text-emerald-800">
          {notice}
        </p>
      )}

      {loading ? (
        <p role="status" className="mt-4 text-sm text-slate-500">
          Loading references…
        </p>
      ) : listError ? (
        <p role="alert" className="mt-4 text-sm text-red-700">
          {listError}
        </p>
      ) : items.length === 0 ? (
        <p className="mt-4 text-sm text-slate-500">
          {offset === 0
            ? (questionBanks ? 'No question banks uploaded yet.' : 'No reference documents uploaded yet.')
            : 'No more references on this page.'}
        </p>
      ) : (
        <ul className="mt-4 space-y-2">
          {items.map((reference) => (
            <li
              key={reference.id}
              className="rounded-xl border border-slate-200 p-3"
            >
              <p className="break-words text-sm font-semibold">
                {reference.filename}
              </p>

              <div className="mt-2 flex flex-wrap items-center gap-2">
                <span className="text-xs text-slate-500">
                  {reference.document_type === 'question_bank'
                    ? 'Question bank'
                    : reference.document_type === 'prior_jd'
                    ? 'Prior JD'
                    : 'Company policy'}
                </span>

                <span
                  className={`rounded-full px-2 py-1 text-xs ${
                    reference.index_status === 'indexed'
                      ? 'bg-emerald-50 text-emerald-800'
                      : reference.index_status === 'failed'
                        ? 'bg-red-50 text-red-700'
                        : 'bg-amber-50 text-amber-800'
                  }`}
                >
                  {reference.is_archived ? 'Archived' : statusLabels[reference.index_status]}
                </span>
              </div>

              {reference.error_code && <p className="mt-2 text-xs text-red-700">
                Processing error: {reference.error_code}. {reference.error_code !== 'QUESTION_BANK_INDEX_FAILED' && reference.document_type === 'question_bank' ? 'Upload the same document again to retry extraction.' : ''}
              </p>}
              {reference.warnings?.length ? <p className="mt-2 text-xs text-slate-500">Extraction warnings: {reference.warnings.join(', ')}</p> : null}
              {!reference.is_archived && (reference.document_type !== 'question_bank' || reference.error_code === 'QUESTION_BANK_INDEX_FAILED' || reference.index_status === 'pending') && (reference.index_status === 'pending' ||
                reference.index_status === 'failed') && (
                <button
                  type="button"
                  className="button-secondary mt-3"
                  disabled={busy}
                  onClick={() => {
                    void runOperation(
                      () => reference.document_type === 'question_bank'
                        ? retryQuestionBankIndex(token, reference.id).then(asReferenceItem)
                        : retryReferenceIndex(token, reference.id),
                    )
                  }}
                >
                  Retry indexing
                </button>
              )}
              {reference.document_type === 'question_bank' && !reference.is_archived && <button
                type="button" className="button-secondary mt-3 ml-2" disabled={busy}
                onClick={() => { void runOperation(() => archiveQuestionBank(token, reference.id).then(asReferenceItem)) }}
              >Archive</button>}
            </li>
          ))}
        </ul>
      )}

      <div className="mt-4 flex items-center justify-between gap-2">
        <button
          type="button"
          className="button-secondary"
          disabled={busy || loading || offset === 0}
          onClick={() => setOffset((value) => Math.max(0, value - 10))}
        >
          Previous
        </button>

        <span className="text-xs text-slate-500">
          Page {offset / 10 + 1}
        </span>

        <button
          type="button"
          className="button-secondary"
          disabled={
            busy || loading || Boolean(listError) || items.length < 10
          }
          onClick={() => setOffset((value) => value + 10)}
        >
          Next
        </button>
      </div>

      <p className="mt-3 text-xs text-slate-500">
        {questionBanks
          ? 'Ready question banks are searched by JD title when you ask for questions in the interview chat. PDF/DOCX extraction includes local OCR when needed.'
          : 'Ready references are searched when drafting or revising a JD. Uploading a reference does not change an existing draft.'}
      </p>
    </section>
  )
}
