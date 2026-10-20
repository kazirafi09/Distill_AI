import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import {
  ControlBar, GridLayout, LiveKitRoom, ParticipantTile,
  RoomAudioRenderer, StartAudio, useConnectionState, useTracks, useLocalParticipant, useRoomContext,
} from '@livekit/components-react'
import type { LocalUserChoices } from '@livekit/components-react'
import { ConnectionState, Track } from 'livekit-client'
import type { RoomCredentials } from './sessionApi'
import { isInterviewParticipant } from './interviewParticipants'
import '@livekit/components-styles'
import './InterviewRoom.css'

type StageOptions = {
  participantRole?: 'recruiter' | 'candidate'
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

export function CallStageView({ main, self, mainName, selfName, onSwap, controls, notice, audioAction }: {
  main: ReactNode; self: ReactNode; mainName: string; selfName: string; onSwap: () => void
  controls: ReactNode; notice?: ReactNode; audioAction?: ReactNode
}) {
  return <div className="stage-call">
    {audioAction}{notice}
    <div className="stage-main-video">{main}</div>
    <strong className="stage-speaker">{mainName}</strong>
    <button className="stage-pip" onClick={onSwap} aria-label="Swap main and small video"><div>{self}</div><span>{selfName}</span></button>
    <div className="stage-dock" aria-label="Call controls">{controls}</div>
  </div>
}

export function StageRoom({ candidateName, onAdmit, waiting, onError, participantRole = 'recruiter' }: StageOptions & { onError: (error: Error) => void }) {
  const tracks = useTracks([{ source: Track.Source.Camera, withPlaceholder: true }])
  const room = useRoomContext()
  const { localParticipant, isMicrophoneEnabled, isCameraEnabled } = useLocalParticipant()
  const [swapped, setSwapped] = useState(false)
  const [pending, setPending] = useState(false)
  const remote = tracks.find(track => !track.participant.isLocal && isInterviewParticipant(track.participant))
  const local = tracks.find(track => track.participant.isLocal)
  const main = swapped ? local : remote
  const small = swapped ? remote : local
  const mainHasVideo = main?.publication && !main.publication.isMuted && main.publication.track
  const remoteRole = participantRole === 'candidate' ? 'Interviewer' : 'Candidate'
  const remoteName = candidateName ?? remoteRole
  const mainName = swapped ? 'You' : remoteName
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
  return <CallStageView audioAction={<StartAudio label="Enable speaker audio" />}
    notice={!isMicrophoneEnabled && <p className="stage-microphone-warning" role="status">{participantRole === 'candidate' ? 'Your microphone is off. Select Unmute so your interviewer can hear you.' : 'Your microphone is off. Your speech is not being transcribed. Select Unmute to speak.'}</p>}
    main={main && mainHasVideo ? <ParticipantTile trackRef={main} /> : <div className="stage-camera-placeholder">{main ? <><span className="stage-camera-off">Camera off</span><span className="stage-video-initials">{mainInitials}</span></> : <><div className="stage-person" /><p>{participantRole === 'candidate' ? 'Waiting for your interviewer' : waiting ? 'Waiting for candidate admission' : 'Waiting for candidate to join'}</p></>}</div>}
    mainName={swapped ? `You · ${participantRole === 'candidate' ? 'Candidate' : 'Interviewer'}` : `${remoteName} · ${remoteRole}`}
    self={small ? <ParticipantTile trackRef={small} /> : <div className="stage-person" />}
    selfName={swapped ? remoteName : 'You'} onSwap={() => setSwapped(!swapped)} controls={<>
      <button className={isMicrophoneEnabled ? 'is-enabled' : ''} aria-pressed={isMicrophoneEnabled} disabled={pending} onClick={() => void toggle('microphone')}><CallIcon kind="microphone" />{isMicrophoneEnabled ? 'Mute' : 'Unmute'}</button>
      <button className={isCameraEnabled ? 'is-enabled' : ''} aria-pressed={isCameraEnabled} disabled={pending} onClick={() => void toggle('camera')}><CallIcon kind="camera" />{isCameraEnabled ? 'Video off' : 'Video on'}</button>
      {waiting && onAdmit && <button className="stage-admit" onClick={onAdmit}><CallIcon kind="admit" />Admit</button>}
      <button onClick={() => void room.disconnect()}><CallIcon kind="leave" />Leave</button>
    </>} />
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
      <GridLayout tracks={tracks.filter(track => isInterviewParticipant(track.participant))} className="interview-video-grid"><ParticipantTile /></GridLayout>
    </>)}
    <RoomAudioRenderer />
    {!children && !options.stage && <StartAudio label="Enable speaker audio" />}
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
