import { ArrowLeft, Pause, Play } from 'lucide-react'
import { lazy, Suspense, useEffect, useState } from 'react'
import { toast } from 'sonner'

import { Board } from '@/components/Board'
import { CheckCard } from '@/components/CheckCard'
import { BoardCard, ControlBar } from '@/components/ControlBar'
import { DoubtList } from '@/components/DoubtList'
import { LessonTags } from '@/components/LessonTags'
import { MicButton } from '@/components/MicButton'
import { SummaryCard } from '@/components/SummaryCard'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Progress } from '@/components/ui/progress'
import { progressPercent } from '@/engine/board'
import { restoreSession } from '@/engine/restore'
import { Tutor } from '@/engine/tutor'
import { api } from '@/lib/api'
import type { Lesson, Resume } from '@/lib/types'
import { useGuestStore } from '@/store/guest'
import { initialPlayerState, setPlayer, usePlayer } from '@/store/player'

// CanvasKit is a few MB of WebAssembly, so only Skia lessons load it
const SkiaBoard = lazy(() => import('@/components/SkiaBoard'))

export function LessonPage({ slug, onExit }: { slug: string; onExit: () => void }) {
  const guest = useGuestStore((s) => s.guest)
  const [lesson, setLesson] = useState<Lesson | null>(null)
  const [tutor, setTutor] = useState<Tutor | null>(null)
  const [resume, setResume] = useState<Resume | null>(null)

  const mode = usePlayer((s) => s.mode)
  const sceneIdx = usePlayer((s) => s.sceneIdx)
  const stepIdx = usePlayer((s) => s.stepIdx)
  const caption = usePlayer((s) => s.caption)
  const layer = usePlayer((s) => s.layer)
  const error = usePlayer((s) => s.error)
  const check = usePlayer((s) => s.check)

  useEffect(() => {
    // One engine per mount; StrictMode's extra mount/unmount gets its own instance
    const t = new Tutor()
    setTutor(t)
    api.getLesson(slug).then(setLesson, () => toast.error('Lesson not found'))
    return () => {
      t.stop()
      setPlayer(initialPlayerState)
    }
  }, [slug])

  useEffect(() => {
    if (guest) api.resume(guest.id, slug).then(setResume, () => setResume(null))
  }, [guest, slug])

  useEffect(() => {
    if (error) {
      toast.error(error)
      setPlayer({ error: null })
    }
  }, [error])

  async function start(from: Resume | null) {
    if (!lesson || !guest || !tutor) return
    if (from) {
      // Bring back the session's earlier doubts; without them the lesson still resumes
      const state = await api.sessionState(from.session_id).catch(() => null)
      const restored = state ? restoreSession(state, from.scene_idx) : undefined
      return tutor.start(lesson, from.session_id, { sceneIdx: from.scene_idx, stepIdx: from.step_idx, restored })
    }
    const session = await api.createSession(guest.id, lesson.slug)
    await tutor.start(lesson, session.id)
  }

  if (!lesson || !tutor) return <p className="text-muted-foreground p-8 text-sm">Loading lesson…</p>

  const scene = lesson.scenes[sceneIdx]
  // The card stays up while the student asks a follow-up from it, and hides while the board is in use
  const overlay = check && ['check', 'listening', 'thinking'].includes(mode) ? <CheckCard tutor={tutor} /> : mode === 'summary' || mode === 'done' ? <SummaryCard onExit={onExit} /> : null

  return (
    <div className="mx-auto flex w-full max-w-7xl flex-col gap-4 pb-36 lg:pb-0">
      <header className="flex items-center gap-2 sm:gap-3">
        <Button variant="ghost" size="icon" onClick={onExit} aria-label="Back">
          <ArrowLeft className="size-4" />
        </Button>
        <div className="min-w-0 flex-1">
          <h1 className="truncate text-lg font-semibold">{lesson.title}</h1>
          <p className="text-muted-foreground truncate text-sm">
            Scene {sceneIdx + 1}/{lesson.scenes.length}: {scene?.title}
          </p>
        </div>
        <div className="hidden md:block">
          <LessonTags tags={lesson.tags} />
        </div>
        <Progress value={progressPercent(lesson, sceneIdx, stepIdx)} className="w-20 shrink-0 sm:w-40" />
      </header>

      <div className="grid gap-4 lg:grid-cols-[1fr_300px]">
        <div className="flex flex-col gap-3">
          <div className="relative">
            <div className="relative aspect-video overflow-hidden rounded-xl border bg-white shadow-sm">
              {lesson.renderer === 'skia' ? (
                <Suspense fallback={null}>
                  <SkiaBoard />
                </Suspense>
              ) : (
                <Board />
              )}
              {layer === 'doubt' && mode !== 'summary' && mode !== 'done' && (
                <Badge className="absolute top-3 right-3 bg-amber-500">Doubt</Badge>
              )}
            </div>
            {mode === 'idle' && (
              <BoardCard dim="md:bg-white/80">
                <div className="flex flex-col items-center gap-4">
                  <p className="text-muted-foreground max-w-sm text-center text-sm">
                    {lesson.scenes.length} scenes. Tap or hold the mic anytime to ask a doubt in Hindi or Hinglish.
                  </p>
                  {resume ? (
                    <div className="flex flex-wrap justify-center gap-3">
                      <Button size="lg" onClick={() => void start(resume)}>
                        <Play className="size-4" /> Continue from scene {resume.scene_idx + 1}
                      </Button>
                      <Button size="lg" variant="outline" onClick={() => void start(null)}>
                        Start over
                      </Button>
                    </div>
                  ) : (
                    <Button size="lg" onClick={() => void start(null)}>
                      <Play className="size-4" /> Start lesson
                    </Button>
                  )}
                </div>
              </BoardCard>
            )}
            {overlay && <BoardCard>{overlay}</BoardCard>}
          </div>
          <p className="min-h-12 rounded-lg bg-muted px-4 py-3 text-sm leading-relaxed">{caption || ' '}</p>
        </div>

        <aside className="flex flex-col gap-6 rounded-xl border p-4">
          <ControlBar>
            <MicButton tutor={tutor} />
            <Button
              variant="outline"
              size="icon"
              className="size-12 rounded-full"
              disabled={!['playing', 'paused', 'answering'].includes(mode)}
              onClick={() => tutor.togglePause()}
              aria-label={mode === 'paused' ? 'Resume' : 'Pause'}
            >
              {mode === 'paused' ? <Play className="size-5" /> : <Pause className="size-5" />}
            </Button>
          </ControlBar>
          <div className="flex min-h-0 flex-col gap-2">
            <h2 className="text-sm font-medium">Your doubts</h2>
            <DoubtList />
          </div>
        </aside>
      </div>
    </div>
  )
}
