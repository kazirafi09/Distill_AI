// Development-only visual preview. Never validates invitations or connects media.
import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import CandidateInterviewView from './CandidateInterviewView'
import type { CandidatePhase } from './CandidateInterviewView'
import { CallIcon, CallStageView } from './InterviewRoom'

const states: { value: CandidatePhase; label: string }[] = [
  { value: 'devices', label: 'Device check' }, { value: 'waiting', label: 'Waiting room' },
  { value: 'ready', label: 'Ready to join' }, { value: 'call', label: 'In call' },
  { value: 'finished', label: 'Finished' }, { value: 'unavailable', label: 'Unavailable link' },
]

export default function CandidateInterviewPreview() {
  const [params, setParams] = useSearchParams()
  const phase = states.find(state => state.value === params.get('state'))?.value ?? 'devices'
  const [microphone, setMicrophone] = useState(false)
  const [camera, setCamera] = useState(false)
  const [swapped, setSwapped] = useState(false)
  const change = (state: CandidatePhase) => setParams({ state })
  const placeholder = <div className="stage-camera-placeholder"><div className="stage-person" /><p>Camera off</p></div>
  return <div className="candidate-preview-shell">
    <aside className="candidate-preview-banner" aria-label="Preview states">
      <strong>UI preview</strong><span>Sample layout · no camera, microphone or interview connection</span>
      <nav aria-label="Preview states">{states.map(state => <button key={state.value} aria-pressed={phase === state.value} onClick={() => change(state.value)}>{state.label}</button>)}</nav>
    </aside>
    <CandidateInterviewView phase={phase} jobTitle="Senior Python Developer" statusText={states.find(state => state.value === phase)!.label}
      instructions="Please have your CV ready. Choose a quiet place and check your camera and microphone before joining."
      onJoin={() => change('call')} onRecheck={() => change('devices')}
      canReturnToWaiting onWaiting={() => change('waiting')}
      deviceCheck={phase === 'devices' ? <div className="interview-device-check">
        <p>Check your devices. Camera and microphone start switched off.</p>
        <div className="lk-prejoin"><div className="lk-video-container candidate-preview-video">{placeholder}</div>
          <div className="candidate-device-options"><div><button className="button-secondary" aria-pressed={microphone} onClick={() => setMicrophone(value => !value)}><CallIcon kind="microphone" />Microphone {microphone ? 'on' : 'off'}</button>
            <label>Microphone device<select><option>System default</option><option>Sample microphone</option></select></label></div>
            <div><button className="button-secondary" aria-pressed={camera} onClick={() => setCamera(value => !value)}><CallIcon kind="camera" />Camera {camera ? 'on' : 'off'}</button>
              <label>Camera device<select><option>System default</option><option>Sample camera</option></select></label></div></div>
          <button className="button-primary candidate-preview-continue" onClick={() => change('waiting')}>Continue</button>
        </div>
      </div> : undefined}
      call={<div className="interview-live-room is-stage"><CallStageView main={placeholder} self={placeholder}
        mainName={swapped ? 'You · Candidate' : 'Interviewer'} selfName={swapped ? 'Interviewer' : 'You'} onSwap={() => setSwapped(value => !value)}
        controls={<><button aria-pressed={microphone} onClick={() => setMicrophone(value => !value)}><CallIcon kind="microphone" />{microphone ? 'Mute' : 'Unmute'}</button>
          <button aria-pressed={camera} onClick={() => setCamera(value => !value)}><CallIcon kind="camera" />{camera ? 'Video off' : 'Video on'}</button>
          <button onClick={() => change('waiting')}><CallIcon kind="leave" />Leave</button></>} /></div>} />
  </div>
}
