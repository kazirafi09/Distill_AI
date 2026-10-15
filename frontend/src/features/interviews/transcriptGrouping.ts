import type { TranscriptSnapshot } from './sessionApi'

type Segment = TranscriptSnapshot['turns'][number]
export type TranscriptUtterance = {
  id: string
  stream_id: string
  speaker_role: Segment['speaker_role']
  stream_start_ms: number
  stream_end_ms: number
  text: string
  segments: Segment[]
}

export function groupTranscriptTurns(turns: Segment[]): TranscriptUtterance[] {
  const groups: TranscriptUtterance[] = []
  for (const turn of turns) {
    const group = groups.at(-1)
    const previous = group?.segments.at(-1)
    const gap = previous ? turn.stream_start_ms - previous.stream_end_ms : Infinity
    // Stream clocks are independent. Only join adjacent, nonoverlapping source
    // segments from the same speaker/connection, never across a known endpoint.
    const canJoin = group && previous && previous.speech_final !== true
      && group.stream_id === turn.stream_id && group.speaker_role === turn.speaker_role
      && gap >= 0 && gap <= 1500
      && turn.stream_end_ms - group.stream_start_ms <= 30000
      && group.text.length + turn.text.length < 3000
    if (canJoin) {
      group.segments.push(turn)
      group.text += ` ${turn.text}`
      group.stream_end_ms = turn.stream_end_ms
    } else {
      groups.push({ id: turn.id, stream_id: turn.stream_id, speaker_role: turn.speaker_role,
        stream_start_ms: turn.stream_start_ms, stream_end_ms: turn.stream_end_ms,
        text: turn.text, segments: [turn] })
    }
  }
  return groups
}
