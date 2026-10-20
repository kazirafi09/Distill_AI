import { useEffect, useState } from 'react'

// Enumeration lists devices without opening a camera or microphone stream.
export function useAvailableDevices(refreshKey = '') {
  const [devices, setDevices] = useState<MediaDeviceInfo[]>([])
  const [error, setError] = useState('')
  useEffect(() => {
    let cancelled = false
    const media = navigator.mediaDevices
    async function refresh() {
      if (!media) { setError('Media devices are unavailable in this browser.'); return }
      try {
        const next = await media.enumerateDevices()
        if (!cancelled) { setDevices(next); setError('') }
      } catch (caught) {
        if (!cancelled) setError(caught instanceof Error ? caught.message : 'Could not check devices.')
      }
    }
    void refresh()
    media?.addEventListener('devicechange', refresh)
    return () => { cancelled = true; media?.removeEventListener('devicechange', refresh) }
  }, [refreshKey])
  return { devices, error }
}
