// A new uninterrupted five-second window is required whenever face count changes.
export function advanceFaceCountdown(startedAt: number | null, faceCount: number | null, sampledAt: number) {
  if (faceCount !== 1) return { startedAt: null, seconds: null, ready: false }
  const start = startedAt ?? sampledAt
  const seconds = Math.max(0, Math.ceil((start + 5000 - sampledAt) / 1000))
  return { startedAt: start, seconds, ready: seconds === 0 }
}
