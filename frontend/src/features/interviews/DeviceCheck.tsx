import { useCallback, useEffect, useRef, useState } from 'react'
import { usePreviewTracks } from '@livekit/components-react'
import type { LocalUserChoices } from '@livekit/components-react'
import { Track } from 'livekit-client'
import { CallIcon } from './InterviewRoom'
import { useAvailableDevices } from './useAvailableDevices'

export default function DeviceCheck({ name, choices, onReady }: {
  name: string; choices?: LocalUserChoices | null; onReady: (choices: LocalUserChoices) => void
}) {
  const [settings, setSettings] = useState<LocalUserChoices>(() => choices ?? {
    username: name, audioEnabled: false, videoEnabled: false, audioDeviceId: 'default', videoDeviceId: 'default',
  })
  const [error, setError] = useState('')
  const reportAudio = useCallback((caught: Error) => { setError(caught.message); setSettings(value => ({ ...value, audioEnabled: false })) }, [])
  const reportVideo = useCallback((caught: Error) => { setError(caught.message); setSettings(value => ({ ...value, videoEnabled: false })) }, [])
  const audioTracks = usePreviewTracks({ video: false, audio: settings.audioEnabled ? { deviceId: settings.audioDeviceId } : false }, reportAudio)
  const videoTracks = usePreviewTracks({ audio: false, video: settings.videoEnabled ? { deviceId: settings.videoDeviceId } : false }, reportVideo)
  const audioTrack = settings.audioEnabled ? audioTracks?.find(track => track.kind === Track.Kind.Audio && track.mediaStreamTrack.readyState === 'live') : undefined
  const videoTrack = settings.videoEnabled ? videoTracks?.find(track => track.kind === Track.Kind.Video && track.mediaStreamTrack.readyState === 'live') : undefined
  const { devices, error: deviceError } = useAvailableDevices(`${videoTrack?.mediaStreamTrack.id}:${audioTrack?.mediaStreamTrack.id}`)
  const preview = useRef<HTMLVideoElement>(null)
  useEffect(() => {
    const element = preview.current
    if (!videoTrack || !element) return
    videoTrack.attach(element)
    return () => { videoTrack.detach(element) }
  }, [videoTrack])
  const controls = [
    { kind: 'audioinput', label: 'Microphone', icon: 'microphone', enabled: 'audioEnabled', device: 'audioDeviceId' },
    { kind: 'videoinput', label: 'Camera', icon: 'camera', enabled: 'videoEnabled', device: 'videoDeviceId' },
  ] as const
  const checking = (settings.audioEnabled && !audioTrack) || (settings.videoEnabled && !videoTrack)
  return <div data-lk-theme="default" className="interview-device-check">
    <p>Choose your devices before switching them on. Camera and microphone start switched off.</p>
    {(error || deviceError) && <p role="alert">{error || deviceError} You can continue with devices switched off.</p>}
    <div className="lk-prejoin">
      <div className="lk-video-container"><video ref={preview} autoPlay muted playsInline hidden={!videoTrack} />
        {!videoTrack && <div className="lk-camera-off-note"><CallIcon kind="camera" /></div>}
      </div>
      <div className="candidate-device-options">{controls.map(control => {
        const available = devices.filter(device => device.kind === control.kind)
        return <div key={control.kind}>
          <button type="button" className="lk-button" aria-pressed={settings[control.enabled]} onClick={() => {
            setError(''); setSettings(value => ({ ...value, [control.enabled]: !value[control.enabled] }))
          }}><CallIcon kind={control.icon} />{control.label} {settings[control.enabled] ? 'on' : 'off'}</button>
          <label>{control.label} device<select aria-label={`${control.label} device`} value={settings[control.device]} onChange={event => setSettings(value => ({ ...value, [control.device]: event.target.value }))}>
            <option value="default">System default</option>
            {available.filter(device => device.deviceId !== 'default').map((device, index) =>
              <option key={device.deviceId || index} value={device.deviceId || `unavailable-${index}`} disabled={!device.deviceId}>{device.label || `${control.label} ${index + 1}`}</option>)}
          </select></label>
        </div>
      })}</div>
      {devices.some(device => !device.label) && <p className="candidate-device-note">Your browser may hide device names until you allow camera or microphone access.</p>}
      <form className="lk-username-container" onSubmit={event => {
        event.preventDefault()
        if (settings.username.trim() && !checking) onReady(settings)
      }}>
        <input name="username" aria-label="Your name" autoComplete="name" required value={settings.username} onChange={event => setSettings(value => ({ ...value, username: event.target.value }))} />
        <button className="lk-button lk-join-button" disabled={!settings.username.trim() || checking} type="submit">{checking ? 'Checking devices…' : 'Continue'}</button>
      </form>
    </div>
  </div>
}
