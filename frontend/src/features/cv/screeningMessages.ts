export const errorLabels: Record<string, string> = {
  PROFILE_CORRECTION_NEEDED: 'Correct the approved screening profile: confirm criteria priorities and weights totaling 100%, with criteria in every positive-weight dimension.',
  CV_TEXT_NEEDS_REVIEW: 'CV text is incomplete or unreadable. Review the original and retry parsing before screening.',
  NO_DOWNLOADED_CV: 'This application has no linked downloaded CV.',
  INDEXING_FAILED: 'Evidence indexing failed. The parsed profile is retained; resume to retry.',
  MATCH_INPUT_TOO_LARGE: 'Candidate evidence exceeds the configured model context. Review this application manually.',
  SCREENING_INPUTS_CHANGED: 'Inputs changed during processing. Start a new screening run.',
  MATCH_SCHEMA_OR_EVIDENCE_INVALID: 'The model output did not pass evidence validation. Resume to retry.',
}
