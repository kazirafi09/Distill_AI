// Room services (including transcription) must never occupy an interview video tile.
export function isInterviewParticipant(participant: { identity: string; isLocal: boolean }) {
  return participant.isLocal || /^(recruiter|candidate)-/.test(participant.identity)
}
