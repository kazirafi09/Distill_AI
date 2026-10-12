import { useEffect, useRef, useState } from 'react'
import { getCVProfile, parseCV, parseJobCVs } from './api'
import type { ParseResult } from './api'

const terminal = new Set(['completed', 'needs_review', 'failed', 'not_started'])

function statusLabel(status: string) {
  return status === 'not_started' ? 'Not parsed' : status.replaceAll('_', ' ').replace(/^./, letter => letter.toUpperCase())
}

function sourceLabel(source: string) {
  return source.replace(/^pdf-page-(\d+)$/, 'PDF page $1').replace(/^docx-image-(\d+)$/, 'DOCX image $1').replace('docx-body', 'DOCX body')
}

function warningLabel(code: string) {
  const labels: Record<string, string> = {
    CV_SHEET_NAME_DISCREPANCY: 'The name in the CV differs from the form response. Review both identities.',
    CV_SHEET_EMAIL_DISCREPANCY: 'The CV email differs from the upload/form email. This does not affect screening or scores.',
    CV_CONTACT_FIELDS_UNKNOWN: 'The CV name or email could not be established from the document.',
    DOCX_IMAGES_APPENDED_AFTER_BODY_ORDER_UNKNOWN: 'Embedded image text follows the document body; its original layout order is unknown.',
    DOCX_HEADERS_FOOTERS_TEXTBOXES_DRAWINGS_NOT_EXTRACTED: 'DOCX headers, footers, text boxes and drawings may contain additional information.',
    TEXT_UNUSABLE_OR_INCOMPLETE: 'Some document content could not be read reliably. Review the original CV.',
    MODEL_UNAVAILABLE: 'The configured CV extraction model is unavailable.',
    MODEL_TIMEOUT: 'The model timed out. Saved text can be reused when you resume.',
    MODEL_AUTH_FAILED: 'The model API key is invalid or lacks access.',
    MODEL_NOT_AVAILABLE: 'The configured model is unavailable for this API key.',
    MODEL_RATE_LIMITED: 'The model API quota or rate limit was reached. Retry later.',
    MODEL_REQUEST_INVALID: 'The model rejected the extraction request. Check server configuration.',
    MODEL_RESPONSE_BLOCKED: 'The model did not return a usable response.',
    MODEL_INPUT_TOO_LARGE: 'The CV text exceeds the parser context limit. Review the document before continuing.',
    MODEL_OUTPUT_TOO_LARGE: 'The model response exceeded its output limit. Review this CV.',
    MODEL_SCHEMA_OR_EVIDENCE_INVALID: 'The extracted profile could not be validated against the CV. Resume to retry.',
    UNSUPPORTED_PROFILE_CLAIMS_OMITTED: 'Some model-extracted fields lacked verified excerpts and were left unknown. Screening uses the original CV evidence.',
    FILE_HASH_MISMATCH: 'The stored file has changed. Review its integrity before continuing.',
    FILE_SIZE_MISMATCH: 'The stored file size does not match the downloaded version.',
    LOCAL_FILE_MISSING: 'The downloaded CV file is missing from local storage.',
    ENCRYPTED_PDF: 'This PDF is password-protected. Provide a readable version.',
    INVALID_FILE: 'The file format could not be read.',
    INVALID_DOCUMENT: 'This document is corrupted or unreadable.',
    PARSING_BUSY: 'Another CV is being processed. Resume this CV when it finishes.',
    PARSING_UNAVAILABLE: 'CV parsing is currently unavailable.',
  }
  if (labels[code]) return labels[code]
  const ocr = code.match(/^(pdf-page-\d+|docx-image-\d+):OCR_(TIMEOUT|FAILED)$/)
  if (ocr) return `${sourceLabel(ocr[1])}: OCR ${ocr[2] === 'TIMEOUT' ? 'timed out' : 'failed'}. Review the original content.`
  return code.replaceAll('_', ' ').toLowerCase().replace(/^./, letter => letter.toUpperCase())
}

function ProfileValue({ value }: { value: unknown }) {
  if (value == null) return <span>Unknown</span>
  if (Array.isArray(value)) return value.length ? <ul>{value.map((item, index) => <li key={index}><ProfileValue value={item} /></li>)}</ul> : <span>Not stated</span>
  if (typeof value === 'object') {
    const record = value as Record<string, unknown>
    if ('value' in record) return <span>{String(record.value)}</span>
    return <dl>{Object.entries(record).map(([key, item]) => <div key={key}><dt>{key.replaceAll('_', ' ')}</dt><dd><ProfileValue value={item} /></dd></div>)}</dl>
  }
  return <span>{String(value)}</span>
}

export function CVParseControl({ token, versionId, enabled, filename, readOnly = false }: { token: string; versionId: string; enabled: boolean; filename: string; readOnly?: boolean }) {
  const [result, setResult] = useState<ParseResult | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const lock = useRef(false)
  useEffect(() => {
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout>
    async function poll() {
      try {
        const next = await getCVProfile(token, versionId, controller.signal)
        if (!controller.signal.aborted) {
          setResult(next)
          timer = setTimeout(() => void poll(), busy || !terminal.has(next.status) ? 2500 : 10000)
        }
      } catch (caught) {
        if (!controller.signal.aborted) setError(caught instanceof Error ? caught.message : 'Could not load parsing status.')
      }
    }
    void poll()
    return () => { controller.abort(); clearTimeout(timer) }
  }, [token, versionId, busy])

  async function parse() {
    if (lock.current) return
    lock.current = true
    setBusy(true); setError('')
    try { setResult(await parseCV(token, versionId)) }
    catch (caught) { setError(caught instanceof Error ? caught.message : 'Parsing failed.') }
    finally { lock.current = false; setBusy(false) }
  }
  return <div className="cv-parsing" data-file-version-id={versionId}>
    <strong className="cv-parsing-filename">{filename}</strong>
    {!readOnly && <button className="button-secondary" disabled={!enabled || busy} onClick={() => void parse()}>{busy ? 'Processing…' : result?.status === 'completed' ? 'Check saved parse' : 'Parse / resume CV'}</button>}
    <p role="status">{result ? statusLabel(result.status) : 'Loading status…'}{result && ` · OCR ${result.ocr_used ? 'used' : 'not used'}`}</p>
    {error && <p className="cv-error" role="alert">{error}</p>}
    {result?.error_code && <p className="cv-error">{warningLabel(result.error_code)}</p>}
    {!!result?.warnings.length && <ul>{result.warnings.map(warning => <li key={warning}>{warningLabel(warning)}</li>)}</ul>}
    {result?.profile && <details><summary>View CV profile and evidence</summary>
      <ProfileValue value={result.profile} />
      <h4>Supporting excerpts</h4>
      {result.evidence.map((item, index) => <blockquote key={index}><p>{item.quote}</p><small>{item.claim_path.replaceAll('_', ' ').replaceAll('.', ' › ')} · {sourceLabel(item.source_id)}{item.ocr_derived ? ' · OCR-derived' : ''}</small></blockquote>)}
    </details>}
  </div>
}

export function CVBatchParse({ token, jobId, onFinished }: { token: string; jobId: string; onFinished: () => void }) {
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const lock = useRef(false)
  const stop = useRef(false)
  useEffect(() => () => { stop.current = true }, [])
  async function run() {
    if (lock.current) return
    lock.current = true; stop.current = false; setBusy(true)
    let offset: number | null = 0, processed = 0
    try {
      while (offset !== null && !stop.current) {
        setMessage(`Processing CV ${processed + 1}…`)
        const next = await parseJobCVs(token, jobId, offset)
        processed += next.results.length
        const last = next.results.at(-1)
        setMessage(`${processed} processed${last ? ` · latest: ${statusLabel(last.status)}${last.error_code ? ` (${warningLabel(last.error_code)})` : ''}` : ''}`)
        offset = next.next_offset
      }
    } catch (caught) { setMessage(caught instanceof Error ? caught.message : 'Batch failed.') }
    finally { lock.current = false; setBusy(false); onFinished() }
  }
  return <div className="cv-parsing cv-batch-parsing"><button className="button-secondary" disabled={busy} onClick={() => void run()}>{busy ? 'Parsing downloaded CVs…' : 'Parse downloaded CVs for this JD'}</button>
    {busy && <button className="button-secondary" onClick={() => { stop.current = true }}>Stop after current CV</button>}
    <p role="status">{message}</p></div>
}
