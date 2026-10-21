import { Loader2, Mic } from 'lucide-react'
import { useEffect, useRef } from 'react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import type { Tutor } from '@/engine/tutor'
import { cn } from '@/lib/utils'
import { usePlayer } from '@/store/player'

// The STT provider accepts at most ~30 s per request
const MAX_RECORD_MS = 25_000
const MIN_RECORD_MS = 400

/** Push-to-talk: hold the button (or the space bar) while speaking. */
export function MicButton({ tutor }: { tutor: Tutor }) {
  const mode = usePlayer((s) => s.mode)
  const recorder = useRef<MediaRecorder | null>(null)
  const pressed = useRef(false)
  const startedAt = useRef(0)
  const timer = useRef<number>(0)
  const listening = mode === 'listening'
  const thinking = mode === 'thinking'
  const enabled = mode === 'playing' || mode === 'paused' || mode === 'answering'

  async function start() {
    if (recorder.current || pressed.current || !tutor.beginQuestion()) return
    pressed.current = true
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      // Released before the mic was ready (e.g. while the permission prompt was open)
      if (!pressed.current) {
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
        if (tooShort) {
          toast('Mic button dabake rakho jab tak bol rahe ho.')
          tutor.cancelQuestion()
        } else {
          void tutor.submitQuestion(new Blob(chunks, { type: rec.mimeType }))
        }
      }
      recorder.current = rec
      startedAt.current = Date.now()
      rec.start()
      timer.current = window.setTimeout(stop, MAX_RECORD_MS)
    } catch {
      pressed.current = false
      toast.error('Microphone permission chahiye sawal poochne ke liye.')
      tutor.cancelQuestion()
    }
  }

  function stop() {
    pressed.current = false
    window.clearTimeout(timer.current)
    const rec = recorder.current
    recorder.current = null
    if (rec && rec.state !== 'inactive') rec.stop()
  }

  // Hold space to talk
  useEffect(() => {
    const down = (e: KeyboardEvent) => {
      if (e.code === 'Space' && !e.repeat && !(e.target instanceof HTMLInputElement)) {
        e.preventDefault()
        void start()
      }
    }
    const up = (e: KeyboardEvent) => {
      if (e.code === 'Space') stop()
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
        aria-label="Hold to ask a question"
        disabled={!enabled && !listening}
        onPointerDown={(e) => {
          e.currentTarget.setPointerCapture(e.pointerId)
          void start()
        }}
        onPointerUp={stop}
        onPointerCancel={stop}
        className={cn(
          'size-20 rounded-full shadow-lg transition-transform select-none touch-none',
          listening && 'scale-110 bg-red-600 hover:bg-red-600 animate-pulse',
        )}
      >
        {thinking ? <Loader2 className="size-8 animate-spin" /> : <Mic className="size-8" />}
      </Button>
      <p className="text-muted-foreground text-xs text-center">
        {listening ? 'Bolo… chhodne par bhej denge' : thinking ? 'Soch rahi hoon…' : 'Hold to ask (or hold Space)'}
      </p>
    </div>
  )
}
