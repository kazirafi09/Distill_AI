import { useCallback, useEffect, useRef, useState } from 'react'
import { usePreviewTracks, useTrackVolume } from '@livekit/components-react'
import type { LocalUserChoices } from '@livekit/components-react'
import type { ReactNode } from 'react'
import { Track } from 'livekit-client'
import type { LocalAudioTrack } from 'livekit-client'
import { CallIcon } from './InterviewRoom'
import { useAvailableDevices } from './useAvailableDevices'
import type { SavedInterviewee, InvitationStatus } from './api'

type Props = {
  candidate: SavedInterviewee
  members: SavedInterviewee[]
  invitation?: InvitationStatus
  status: string
  busy: boolean
  canContinue: boolean
  verification?: ReactNode
  onCandidate: (id: string) => void
  onContinue: (choices: LocalUserChoices) => void
  onRecheck: () => void
}

export default function InterviewPreparation(props: Props) {
  const [camera, setCamera] = useState(false)
  const [microphone, setMicrophone] = useState(false)
  const [cameraDevice, setCameraDevice] = useState('default')
  const [microphoneDevice, setMicrophoneDevice] = useState('default')
  const [cameraError, setCameraError] = useState('')
  const [microphoneError, setMicrophoneError] = useState('')
  const reportCamera = useCallback((error: Error) => { setCameraError(error.message); setCamera(false) }, [])
  const reportMicrophone = useCallback((error: Error) => {
    const message = error.name === 'NotAllowedError'
      ? 'Microphone access is blocked. Allow microphone access for this site in your browser settings, then try again.'
      : error.name === 'NotFoundError' ? 'No microphone was found. Connect a microphone and try again.'
        : error.name === 'NotReadableError' ? 'The microphone is unavailable. Check system microphone access and whether another app is using it.'
          : error.message
    setMicrophoneError(message); setMicrophone(false)
  }, [])
  const videoTracks = usePreviewTracks({ audio: false, video: camera ? { deviceId: cameraDevice } : false }, reportCamera)
  const audioTracks = usePreviewTracks({ video: false, audio: microphone ? { deviceId: microphoneDevice } : false }, reportMicrophone)
  const videoTrack = camera ? videoTracks?.find(track => track.kind === Track.Kind.Video && track.mediaStreamTrack.readyState === 'live') : undefined
  const audioTrack = microphone ? audioTracks?.find(track => track.kind === Track.Kind.Audio && track.mediaStreamTrack.readyState === 'live') as LocalAudioTrack | undefined : undefined
  const volume = useTrackVolume(audioTrack)
  const preview = useRef<HTMLVideoElement>(null)
  const checking = (camera && !videoTrack && !cameraError) || (microphone && !audioTrack && !microphoneError)
  useEffect(() => {
    const element = preview.current
    if (!videoTrack || !element) return
    videoTrack.attach(element)
    return () => { videoTrack.detach(element) }
  }, [videoTrack])
  const { devices, error: deviceError } = useAvailableDevices(`${videoTrack?.mediaStreamTrack.id}:${audioTrack?.mediaStreamTrack.id}`)
  const cameras = devices.filter(device => device.kind === 'videoinput')
  const microphones = devices.filter(device => device.kind === 'audioinput')
  const permission = microphoneError ? 'Blocked or unavailable' : audioTrack ? 'Access allowed' : microphone ? 'Requesting access…' : 'Not checked'
  function enableMicrophoneOrContinue() {
    if (!audioTrack) {
      setMicrophoneError('')
      setMicrophone(true)
      return
    }
    continueToWaitingRoom()
  }
  function continueToWaitingRoom() {
    if (!audioTrack || audioTrack.mediaStreamTrack.readyState !== 'live') {
      setMicrophoneError('Turn on your microphone and allow access before continuing.')
      return
    }
    const choices: LocalUserChoices = {
      username: 'Recruiter', videoEnabled: !!videoTrack, audioEnabled: !!audioTrack,
      videoDeviceId: videoTrack?.mediaStreamTrack.getSettings().deviceId ?? cameraDevice,
      audioDeviceId: audioTrack?.mediaStreamTrack.getSettings().deviceId ?? microphoneDevice,
    }
    videoTracks?.forEach(track => track.stop())
    audioTracks?.forEach(track => track.stop())
    props.onContinue(choices)
  }
  return <div className="lobby-content preparation-content">
    <div className="lobby-preview-column">
      <div className="preparation-video"><div className="preparation-video-label"><span className="status-dot" />Camera preview · only you can see this</div>
        <video ref={preview} autoPlay muted playsInline hidden={!videoTrack} />
        {!videoTrack && <div className="lobby-placeholder"><div className="stage-person" /></div>}
        <div className="preparation-video-footer"><strong>You</strong><span>{audioTrack ? 'Microphone on' : 'Microphone off'}</span></div>
      </div>
      <div className="lobby-device-controls">
        <button className={`device-toggle${camera ? ' is-on' : ''}`} disabled={props.busy || checking} aria-pressed={camera} onClick={() => { setCameraError(''); setCamera(value => !value) }}><CallIcon kind="camera" />Camera {camera ? 'on' : 'off'}</button>
        <button className={`device-toggle${microphone ? ' is-on' : ''}`} disabled={props.busy || checking} aria-pressed={microphone} onClick={() => { setMicrophoneError(''); setMicrophone(value => !value) }}><CallIcon kind="microphone" />Microphone {microphone ? 'on' : 'off'}</button>
        <button className="lobby-secondary" disabled={props.busy || checking} onClick={props.onRecheck}>Recheck devices</button>
      </div>
      {(cameraError || microphoneError || deviceError) && <p className="lobby-error" role="alert">{[cameraError, microphoneError, deviceError].filter(Boolean).join(' ')} Camera is optional. A working microphone is required to continue.</p>}
      {props.verification}
    </div>
    <div className="lobby-info-column">
      <label className="lobby-candidate-picker">Saved candidate<select aria-label="Saved candidate" value={props.candidate.application_id} disabled={props.busy} onChange={event => props.onCandidate(event.target.value)}>{props.members.map(member => <option key={member.application_id} value={member.application_id}>{member.name} · Rank {member.rank} · Score {Number(member.score.aggregate).toFixed(0)}</option>)}</select></label>
      <div className="lobby-card"><div className="lobby-card-title"><strong>{props.candidate.name}</strong><span className={`lobby-badge${props.invitation?.link_status === 'active' ? '' : ' is-neutral'}`}>{props.invitation?.link_status === 'active' ? 'Active invitation' : props.invitation?.link_status ?? 'No invitation'}</span></div><p>{props.candidate.email}</p><p>Interview status: {props.status.replaceAll('_', ' ')}{props.invitation?.link_status === 'active' && ` · link valid until ${new Date(props.invitation.expires_at).toLocaleDateString()}`}</p></div>
      <div className="lobby-card device-status-card">
        <div><span>Camera</span><span className={`lobby-badge${cameraError ? ' is-warning' : cameras.length ? '' : ' is-neutral'}`}>{cameraError ? 'Unavailable' : cameras.length ? 'Found' : 'Not found'}</span></div>
        {cameras.length > 1 && <label>Camera device<select aria-label="Camera device" value={cameraDevice} onChange={event => setCameraDevice(event.target.value)} disabled={checking || props.busy}>{cameras.map((device, index) => <option key={device.deviceId || index} value={device.deviceId || 'default'}>{device.label || `Camera ${index + 1}`}</option>)}</select></label>}
        <div><span>Microphone</span><span className={`lobby-badge${microphoneError ? ' is-warning' : microphones.length ? '' : ' is-neutral'}`}>{microphoneError ? 'Unavailable' : microphones.length ? 'Found' : 'Not found'}</span></div>
        {microphones.length > 1 && <label>Microphone device<select aria-label="Microphone device" value={microphoneDevice} onChange={event => setMicrophoneDevice(event.target.value)} disabled={checking || props.busy}>{microphones.map((device, index) => <option key={device.deviceId || index} value={device.deviceId || 'default'}>{device.label || `Microphone ${index + 1}`}</option>)}</select></label>}
        <div className="microphone-level"><div><span>Microphone level</span><small>{audioTrack ? 'Say a few words' : 'Turn on your microphone to test'}</small></div><meter min={0} max={1} value={Math.min(1, volume * 3)} aria-label="Microphone level" /></div>
        <div><span>Microphone access</span><span className={`lobby-badge${audioTrack ? '' : ' is-neutral'}`}>{permission}</span></div>
      </div>
      <div className="lobby-primary-action"><button className="lobby-primary" disabled={props.busy || checking || (!!audioTrack && !props.canContinue)} onClick={enableMicrophoneOrContinue}>{props.busy ? 'Opening waiting room…' : checking ? 'Checking devices…' : !audioTrack ? 'Enable microphone' : 'Continue to waiting room'}</button><p>{!audioTrack ? 'Select Enable microphone and allow browser access if prompted. Say a few words and check the level meter.' : !props.canContinue ? 'An active invitation is required. Manage invitations in the Invitations tab.' : 'Microphone access is ready. Camera is optional.'}</p></div>
    </div>
  </div>
}
