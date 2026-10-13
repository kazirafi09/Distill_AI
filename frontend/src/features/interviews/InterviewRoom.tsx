import { useCallback, useEffect, useState } from 'react'
import {
  ControlBar, GridLayout, LiveKitRoom, ParticipantTile, PreJoin,
  RoomAudioRenderer, StartAudio, useConnectionState, useTracks,
} from '@livekit/components-react'
import type { LocalUserChoices } from '@livekit/components-react'
import { ConnectionState, Track } from 'livekit-client'
import type { RoomCredentials } from './sessionApi'
import '@livekit/components-styles'
import './InterviewRoom.css'

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

function RoomContents({ onConnectionChange, onError }: {
  onConnectionChange?: (connected: boolean) => void
  onError: (error: Error) => void
}) {
  const state = useConnectionState()
  const tracks = useTracks([{ source: Track.Source.Camera, withPlaceholder: true }])
  useEffect(() => {
    onConnectionChange?.(state === ConnectionState.Connected)
  }, [state, onConnectionChange])
  return <>
    <p role="status" className="interview-connection-status">Connection: {state}</p>
    <GridLayout tracks={tracks} className="interview-video-grid"><ParticipantTile /></GridLayout>
    <RoomAudioRenderer />
    <StartAudio label="Enable interview audio" />
    <ControlBar saveUserChoices={false}
      onDeviceError={({ error }) => onError(error)}
      controls={{ camera: true, microphone: true, screenShare: false, chat: false, leave: true }} />
  </>
}

export default function InterviewRoom({ credentials, choices, onLeave, onError, onConnectionChange }: {
  credentials: RoomCredentials
  choices: LocalUserChoices
  onLeave: () => void
  onError: (error: Error) => void
  onConnectionChange?: (connected: boolean) => void
}) {
  return <LiveKitRoom data-lk-theme="default" className="interview-live-room"
    serverUrl={credentials.server_url} token={credentials.participant_token} connect
    audio={choices.audioEnabled ? { deviceId: choices.audioDeviceId } : false}
    video={choices.videoEnabled ? { deviceId: choices.videoDeviceId } : false}
    onDisconnected={onLeave} onError={onError}>
    <RoomContents onConnectionChange={onConnectionChange} onError={onError} />
  </LiveKitRoom>
}
