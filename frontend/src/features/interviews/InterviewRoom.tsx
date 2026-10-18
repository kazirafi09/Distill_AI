import { useCallback, useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import {
  ControlBar, GridLayout, LiveKitRoom, ParticipantTile, PreJoin,
  RoomAudioRenderer, StartAudio, useConnectionState, useTracks, useLocalParticipant, useRoomContext,
} from '@livekit/components-react'
import type { LocalUserChoices } from '@livekit/components-react'
import { ConnectionState, Track } from 'livekit-client'
import type { RoomCredentials } from './sessionApi'
import '@livekit/components-styles'
import './InterviewRoom.css'

type StageOptions = {
  stage?: boolean
  candidateName?: string
  onEnd?: () => void
  canEnd?: boolean
  onAdmit?: () => void
  waiting?: boolean
}

export function CallIcon({ kind }: { kind: 'microphone' | 'camera' | 'leave' | 'end' | 'admit' }) {
  const paths = {
    microphone: <><rect x="9" y="2" width="6" height="13" rx="3" /><path d="M5 10v2a7 7 0 0 0 14 0v-2M12 19v3M8 22h8" /></>,
    camera: <><rect x="3" y="6" width="12" height="12" rx="2" /><path d="m15 10 6-3v10l-6-3" /></>,
    leave: <><path d="M10 4H5v16h5M12 12h9m-4-4 4 4-4 4" /></>,
    end: <><circle cx="12" cy="12" r="9" /><path d="m9 9 6 6m0-6-6 6" /></>,
    admit: <path d="m4 12 5 5L20 6" />,
  }
  return <svg width="21" height="21" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[kind]}</svg>
}

export function StageRoom({ candidateName, onEnd, canEnd, onAdmit, waiting, onError }: StageOptions & { onError: (error: Error) => void }) {
  const tracks = useTracks([{ source: Track.Source.Camera, withPlaceholder: true }])
  const room = useRoomContext()
  const { localParticipant, isMicrophoneEnabled, isCameraEnabled } = useLocalParticipant()
  const [swapped, setSwapped] = useState(false)
  const [pending, setPending] = useState(false)
  const remote = tracks.find(track => !track.participant.isLocal)
  const local = tracks.find(track => track.participant.isLocal)
  const main = swapped ? local : remote
  const small = swapped ? remote : local
  const mainHasVideo = main?.publication && !main.publication.isMuted && main.publication.track
  const mainName = swapped ? 'You' : candidateName ?? 'Candidate'
  const mainInitials = mainName.split(/\s+/).slice(0, 2).map(word => word[0]).join('').toUpperCase()
  async function toggle(source: 'microphone' | 'camera') {
    if (pending) return
    setPending(true)
    try {
      if (source === 'microphone') await localParticipant.setMicrophoneEnabled(!isMicrophoneEnabled)
      else await localParticipant.setCameraEnabled(!isCameraEnabled)
    } catch (caught) { onError(caught instanceof Error ? caught : new Error('Could not change device state.')) }
    finally { setPending(false) }
  }
  return <div className="stage-call">
    <StartAudio label="Enable interview audio" />
    <div className="stage-main-video">{main && mainHasVideo ? <ParticipantTile trackRef={main} /> : <div className="stage-camera-placeholder">{main ? <><span className="stage-camera-off">Camera off</span><span className="stage-video-initials">{mainInitials}</span></> : <><div className="stage-person" /><p>{waiting ? 'Waiting for candidate admission' : 'Waiting for candidate to join'}</p></>}</div>}</div>
    <strong className="stage-speaker">{swapped ? 'You · Interviewer' : `${candidateName ?? 'Candidate'} · Candidate`}</strong>
    <button className="stage-pip" onClick={() => setSwapped(!swapped)} aria-label="Swap main and small video"><div>{small ? <ParticipantTile trackRef={small} /> : <div className="stage-person" />}</div><span>{swapped ? candidateName ?? 'Candidate' : 'You'}</span></button>
    <div className="stage-dock" aria-label="Call controls">
      <button className={isMicrophoneEnabled ? 'is-enabled' : ''} aria-pressed={isMicrophoneEnabled} disabled={pending} onClick={() => void toggle('microphone')}><CallIcon kind="microphone" />{isMicrophoneEnabled ? 'Mute' : 'Unmute'}</button>
      <button className={isCameraEnabled ? 'is-enabled' : ''} aria-pressed={isCameraEnabled} disabled={pending} onClick={() => void toggle('camera')}><CallIcon kind="camera" />{isCameraEnabled ? 'Video off' : 'Video on'}</button>
      {waiting && onAdmit && <button className="stage-admit" onClick={onAdmit}><CallIcon kind="admit" />Admit</button>}
      <button onClick={() => void room.disconnect()}><CallIcon kind="leave" />Leave</button>
      <button className="stage-danger" disabled={!canEnd} title={!canEnd ? 'Only the recruiter can end the interview for everyone.' : 'End interview for everyone'} onClick={onEnd}><CallIcon kind="end" />End all</button>
    </div>
  </div>
}

export function DeviceCheck({ name, onReady }: {
  name: string
  onReady: (choices: LocalUserChoices) => void
}) {
  const [error, setError] = useState('')
  const reportError = useCallback((caught: Error) => setError(caught.message), [])
  return <div data-lk-theme="default" className="interview-device-check">
    <p>Check your devices. Camera and microphone start switched off.</p>
    {error && <p role="alert">{error} You can continue with the unavailable device switched off.</p>}
    <PreJoin defaults={{ username: name, videoEnabled: false, audioEnabled: false }}
      persistUserChoices={false} joinLabel="Continue" onSubmit={onReady} onError={reportError} />
  </div>
}

function RoomContents({ onConnectionChange, onError, children, ...options }: {
  onConnectionChange?: (connected: boolean) => void
  onError: (error: Error) => void
  children?: ReactNode
} & StageOptions) {
  const state = useConnectionState()
  const tracks = useTracks([{ source: Track.Source.Camera, withPlaceholder: true }])
  useEffect(() => {
    onConnectionChange?.(state === ConnectionState.Connected)
  }, [state, onConnectionChange])
  return <>
    {children ?? (options.stage ? <StageRoom {...options} onError={onError} /> : <>
      <p role="status" className="interview-connection-status">Connection: {state}</p>
      <GridLayout tracks={tracks} className="interview-video-grid"><ParticipantTile /></GridLayout>
    </>)}
    <RoomAudioRenderer />
    {!children && !options.stage && <StartAudio label="Enable interview audio" />}
    {!options.stage && !children && <ControlBar saveUserChoices={false}
      onDeviceError={({ error }) => onError(error)}
      controls={{ camera: true, microphone: true, screenShare: false, chat: false, leave: true }} />}
  </>
}

export default function InterviewRoom({ credentials, choices, onLeave, onError, onConnectionChange, children, ...options }: {
  credentials: RoomCredentials
  choices: LocalUserChoices
  onLeave: () => void
  onError: (error: Error) => void
  onConnectionChange?: (connected: boolean) => void
  children?: ReactNode
} & StageOptions) {
  return <LiveKitRoom data-lk-theme="default" className={`interview-live-room${children ? ' is-workspace' : options.stage ? ' is-stage' : ''}`}
    serverUrl={credentials.server_url} token={credentials.participant_token} connect
    audio={choices.audioEnabled ? { deviceId: choices.audioDeviceId } : false}
    video={choices.videoEnabled ? { deviceId: choices.videoDeviceId } : false}
    onDisconnected={onLeave} onError={onError}>
    <RoomContents onConnectionChange={onConnectionChange} onError={onError} {...options}>{children}</RoomContents>
  </LiveKitRoom>
}
