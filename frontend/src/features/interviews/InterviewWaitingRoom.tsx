import { useState } from 'react'
import { ParticipantTile, StartAudio, useLocalParticipant, useTracks } from '@livekit/components-react'
import { Track } from 'livekit-client'
import type { SavedInterviewee } from './api'
import { CallIcon } from './InterviewRoom'

type Props = {
  candidate: SavedInterviewee
  jobTitle: string
  jobNumber: number
  connected: boolean
  busy: boolean
  startedAt?: string | null
  onAdmit: () => void
  onRecheck: () => void
  onLeave: () => void
  onError: (error: Error) => void
}

export default function InterviewWaitingRoom(props: Props) {
  const { localParticipant, isCameraEnabled, isMicrophoneEnabled } = useLocalParticipant()
  const tracks = useTracks([{ source: Track.Source.Camera, withPlaceholder: true }])
  const local = tracks.find(track => track.participant.isLocal)
  const [deviceBusy, setDeviceBusy] = useState(false)
  const initials = props.candidate.name.split(/\s+/).slice(0, 2).map(word => word[0]).join('').toUpperCase()
  async function toggle(source: 'camera' | 'microphone') {
    if (deviceBusy) return
    setDeviceBusy(true)
    try {
      if (source === 'camera') await localParticipant.setCameraEnabled(!isCameraEnabled)
      else await localParticipant.setMicrophoneEnabled(!isMicrophoneEnabled)
    } catch (caught) { props.onError(caught instanceof Error ? caught : new Error('Could not change devices.')) }
    finally { setDeviceBusy(false) }
  }
  return <div className="lobby-content waiting-content">
    <div className="lobby-preview-column">
      <div className="waiting-videos">
        <div className="waiting-candidate-video"><div className="lobby-placeholder"><span className="waiting-initials">{initials}</span><strong>{props.candidate.name}</strong><p>Their video starts after admission.</p></div><footer><strong>{props.candidate.name}</strong><span>Awaiting admission</span></footer></div>
        <div className="waiting-self-video">{local && isCameraEnabled ? <ParticipantTile trackRef={local} /> : <div className="lobby-placeholder"><div className="stage-person" /></div>}<footer><strong>You</strong><span>Mic {isMicrophoneEnabled ? 'on' : 'off'}</span></footer></div>
      </div>
      <div className="lobby-device-controls"><button className={`device-toggle${isCameraEnabled ? ' is-on' : ''}`} aria-pressed={isCameraEnabled} disabled={deviceBusy || props.busy} onClick={() => void toggle('camera')}><CallIcon kind="camera" />Camera {isCameraEnabled ? 'on' : 'off'}</button><button className={`device-toggle${isMicrophoneEnabled ? ' is-on' : ''}`} aria-pressed={isMicrophoneEnabled} disabled={deviceBusy || props.busy} onClick={() => void toggle('microphone')}><CallIcon kind="microphone" />Microphone {isMicrophoneEnabled ? 'on' : 'off'}</button><button className="lobby-secondary" disabled={props.busy} onClick={props.onRecheck}>Recheck devices</button></div>
      <StartAudio label="Enable speaker audio" />
      {!isMicrophoneEnabled && <p className="lobby-error" role="status">Your microphone is off. Turn it on before admitting the candidate so your questions can be transcribed.</p>}
    </div>
    <div className="lobby-info-column">
      <div className="lobby-card"><div className="lobby-card-title"><strong>{props.candidate.name}</strong><span className="lobby-badge is-warning">Awaiting admission</span></div><p>{props.candidate.email}</p><p>{props.jobTitle} · JD {props.jobNumber}</p></div>
      <div className="lobby-card device-status-card"><div><span>Your camera and microphone</span><span className="lobby-badge">{isCameraEnabled || isMicrophoneEnabled ? 'Ready' : 'Switched off'}</span></div><div><span>Candidate</span><span className="lobby-badge is-neutral">Not admitted</span></div><div><span>Connection</span><span className={`lobby-badge${props.connected ? '' : ' is-neutral'}`}>{props.connected ? 'Connected' : 'Connecting…'}</span></div><div><span>Interview timer</span><span className="lobby-badge is-neutral">{props.startedAt ? 'Already started' : 'Not started'}</span></div><p className="lobby-presence-note">Confirm the candidate is ready before admitting them.</p></div>
      <div className="lobby-primary-action"><button className="lobby-primary" disabled={!props.connected || props.busy || deviceBusy || !isMicrophoneEnabled} onClick={props.onAdmit}>{props.busy ? 'Admitting…' : 'Admit candidate'}</button><p>{!isMicrophoneEnabled ? 'Enable your microphone before admitting the candidate.' : props.startedAt ? 'Admitting lets the candidate rejoin this interview.' : 'Admitting starts the interview timer and lets the candidate join.'}</p><button className="lobby-secondary" disabled={props.busy} onClick={props.onLeave}>Leave waiting room</button></div>
    </div>
  </div>
}
