import { Loader2, Mic } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import type { MicHost, MicState } from '@/engine/host'
import { pressKind, rms, SilenceDetector } from '@/engine/vad'
import { cn } from '@/lib/utils'
import { t } from '@/lib/i18n'
import { usePlayer } from '@/store/player'

// The STT provider accepts at most ~30 s per request
const MAX_RECORD_MS = 25_000
const MIN_RECORD_MS = 400
const VAD_INTERVAL_MS = 50

/**
 * Ask a doubt by voice. A quick tap starts tap-to-talk, which stops by itself when the
 * student goes quiet (or on a second tap). Holding the button (or Space) is push-to-talk.
 */
/** `state` overrides the scene player's mic state (the video page passes its own). */
export function MicButton({ tutor, state }: { tutor: MicHost; state?: MicState }) {
  const mode = usePlayer((s) => s.mode)
  const check = usePlayer((s) => s.check)
  const lesson = usePlayer((s) => s.lesson)
  const recorder = useRef<MediaRecorder | null>(null)
  const pressed = useRef(false)
  const pressedAt = useRef(0)
  const startedAt = useRef(0)
  const discard = useRef(false)
  /** Tap-to-talk: keep recording after release, until silence */
  const tapRef = useRef(false)
  const timer = useRef<number>(0)
  const vadTimer = useRef<number>(0)
  const audioCtx = useRef<AudioContext | null>(null)
  const [tapMode, setTapMode] = useState(false)
  const listening = state ? state.listening : mode === 'listening'
  const thinking = state ? state.thinking : mode === 'thinking'
  const enabled = state
    ? state.enabled
    : mode === 'answering' // a follow-up on the answer board (the tutor has the final say: canAsk)
      ? true
      : check
        ? mode === 'check' && check.followup && !check.busy
        : mode === 'playing' || mode === 'paused'

  /** Watch the mic level; in tap mode, end the recording once the student stops talking. */
  function watchSilence(stream: MediaStream) {
    const ctx = new AudioContext()
    const analyser = ctx.createAnalyser()
    analyser.fftSize = 1024
    ctx.createMediaStreamSource(stream).connect(analyser)
    audioCtx.current = ctx
    const detector = new SilenceDetector()
    const buf = new Float32Array(analyser.fftSize)
    vadTimer.current = window.setInterval(() => {
      analyser.getFloatTimeDomainData(buf)
      const state = detector.push(rms(buf), performance.now())
      if (!tapRef.current) return
      if (state === 'end' || state === 'max') stop()
      else if (state === 'no_speech') {
        discard.current = true
        stop()
      }
    }, VAD_INTERVAL_MS)
  }

  async function start() {
    if (recorder.current || pressed.current || !tutor.beginQuestion()) return
    pressed.current = true
    pressedAt.current = performance.now()
    tapRef.current = false
    setTapMode(false)
    discard.current = false
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } })
      // Released before the mic was ready (e.g. while the permission prompt was open)
      if (!pressed.current && !tapRef.current) {
        stream.getTracks().forEach((t) => t.stop())
        tutor.cancelQuestion()
        return
      }
      const rec = new MediaRecorder(stream)
      const chunks: Blob[] = []
      rec.ondataavailable = (e) => chunks.push(e.data)
      rec.onstop = () => {
        stream.getTracks().forEach((t) => t.stop())
        const tooShort = Date.now() - startedAt.current < MIN_RECORD_MS
        if (discard.current) {
          toast(t(lesson, 'heardNothing'))
          tutor.cancelQuestion()
        } else if (tooShort) {
          toast(t(lesson, 'tooShort'))
          tutor.cancelQuestion()
        } else {
          void tutor.submitQuestion(new Blob(chunks, { type: rec.mimeType }))
        }
      }
      recorder.current = rec
      startedAt.current = Date.now()
      rec.start()
      watchSilence(stream)
      timer.current = window.setTimeout(stop, MAX_RECORD_MS)
    } catch {
      pressed.current = false
      toast.error(t(lesson, 'micPermission'))
      tutor.cancelQuestion()
    }
  }

  /** Button or Space released: a short tap keeps listening, a long hold sends. */
  function release() {
    if (!pressed.current) return
    pressed.current = false
    if (pressKind(pressedAt.current, performance.now()) === 'tap') {
      tapRef.current = true
      setTapMode(true)
      return
    }
    stop()
  }

  function stop() {
    pressed.current = false
    tapRef.current = false
    setTapMode(false)
    window.clearTimeout(timer.current)
    window.clearInterval(vadTimer.current)
    void audioCtx.current?.close()
    audioCtx.current = null
    const rec = recorder.current
    recorder.current = null
    if (rec && rec.state !== 'inactive') rec.stop()
  }

  function press() {
    // A second tap while tap-to-talk is listening sends right away
    if (tapRef.current) return stop()
    void start()
  }

  useEffect(() => () => stop(), [])

  // Space works like the button
  useEffect(() => {
    const down = (e: KeyboardEvent) => {
      if (e.code === 'Space' && !e.repeat && !(e.target instanceof HTMLInputElement)) {
        e.preventDefault()
        press()
      }
    }
    const up = (e: KeyboardEvent) => {
      if (e.code === 'Space') release()
    }
    window.addEventListener('keydown', down)
    window.addEventListener('keyup', up)
    return () => {
      window.removeEventListener('keydown', down)
      window.removeEventListener('keyup', up)
    }
  })

  return (
    <div className="flex flex-col items-center gap-2">
      <Button
        size="icon"
        aria-label="Ask a question"
        disabled={!enabled && !listening}
        onPointerDown={(e) => {
          e.currentTarget.setPointerCapture(e.pointerId)
          press()
        }}
        onPointerUp={release}
        onPointerCancel={release}
        className={cn(
          'size-20 rounded-full shadow-lg transition-transform select-none touch-none',
          listening && 'scale-110 bg-red-600 hover:bg-red-600 animate-pulse',
        )}
      >
        {thinking ? <Loader2 className="size-8 animate-spin" /> : <Mic className="size-8" />}
      </Button>
      <p className="text-muted-foreground text-xs text-center">
        {listening
          ? tapMode
            ? t(lesson, 'speakTap')
            : t(lesson, 'speakHold')
          : thinking
            ? t(lesson, 'thinking')
            : t(lesson, 'askHint')}
      </p>
    </div>
  )
}
