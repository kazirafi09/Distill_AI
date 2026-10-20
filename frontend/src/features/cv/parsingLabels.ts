export function statusLabel(status: string) {
  return status === 'not_started' ? 'Not parsed' : status.replaceAll('_', ' ').replace(/^./, letter => letter.toUpperCase())
}

export function sourceLabel(source: string) {
  return source.replace(/^pdf-page-(\d+)$/, 'PDF page $1').replace(/^docx-image-(\d+)$/, 'DOCX image $1').replace('docx-body', 'DOCX body')
}

export function warningLabel(code: string) {
  const labels: Record<string, string> = {
    CV_SHEET_NAME_DISCREPANCY: 'The name in the CV differs from the application. Check that the correct CV is attached.',
    CV_SHEET_EMAIL_DISCREPANCY: 'The email in the CV differs from the application email. Check both before contacting the candidate. This does not affect the score.',
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
    UNSUPPORTED_PROFILE_CLAIMS_OMITTED: 'Some CV details could not be verified and were left blank. Review the original CV if those details matter.',
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
