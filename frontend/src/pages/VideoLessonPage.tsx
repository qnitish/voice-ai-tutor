import { ArrowLeft, ChevronDown, Pause, Play } from 'lucide-react'
import { useEffect, useState } from 'react'
import { toast } from 'sonner'

import { ControlBar } from '@/components/ControlBar'
import { MicButton } from '@/components/MicButton'
import { Button } from '@/components/ui/button'
import { Progress } from '@/components/ui/progress'
import { ChapterList } from '@/components/video/ChapterList'
import { CheckpointBar } from '@/components/video/CheckpointBar'
import { ThreadPrompt } from '@/components/video/ThreadPrompt'
import { fill } from '@/components/video/format'
import { StartCard } from '@/components/video/StartCard'
import { useFullscreen } from '@/components/video/useFullscreen'
import { VideoDoubtList } from '@/components/video/VideoDoubtList'
import { VideoPlayer } from '@/components/video/VideoPlayer'
import { VideoSummaryCard } from '@/components/video/VideoSummaryCard'
import { VideoTutor } from '@/engine/video/videoTutor'
import { api } from '@/lib/api'
import { askForMic } from '@/lib/mic'
import { t } from '@/lib/i18n'
import type { Lesson, Resume, VideoSessionState } from '@/lib/types'
import { cn } from '@/lib/utils'
import { useGuestStore } from '@/store/guest'
import { initialPlayerState, setPlayer } from '@/store/player'
import { canSeek, initialVideoState, micState, setVideo, useVideo } from '@/store/video'

/** A saved position to continue from, with the session's earlier answers and doubts (fetched before Start). */
interface ResumePoint {
  resume: Resume
  state: VideoSessionState | null
}

const CHAPTERS_OPEN_KEY = 'video.chaptersOpen'

/** Whether the chapter list was left open last time (collapsed by default; storage may be unavailable). */
function readChaptersOpen(): boolean {
  try {
    return localStorage.getItem(CHAPTERS_OPEN_KEY) === '1'
  } catch {
    return false
  }
}

function writeChaptersOpen(open: boolean) {
  try {
    localStorage.setItem(CHAPTERS_OPEN_KEY, open ? '1' : '0')
  } catch {
    // private mode / blocked storage: the choice just isn't remembered
  }
}

export function VideoLessonPage({ slug, onExit }: { slug: string; onExit: () => void }) {
  const guest = useGuestStore((s) => s.guest)
  const [lesson, setLesson] = useState<Lesson | null>(null)
  const [tutor, setTutor] = useState<VideoTutor | null>(null)
  const [resumePoint, setResumePoint] = useState<ResumePoint | null>(null)
  const [chaptersOpen, setChaptersOpen] = useState(readChaptersOpen)
  const { full, toggle: toggleFull } = useFullscreen()
  // The whole store: the seek position changes often anyway, and the mic state derives from several fields
  const video = useVideo()
  const { mode, positionMs, chapterIdx, caption, error } = video

  useEffect(() => {
    // One engine per mount; StrictMode's extra mount/unmount gets its own instance (stop() is permanent)
    const vt = new VideoTutor()
    // oxlint-disable-next-line react/set-state-in-effect -- the instance must be created and disposed with the mount
    setTutor(vt)
    api.getLesson(slug).then(setLesson, () => toast.error('Lesson not found'))
    return () => {
      vt.stop()
      setVideo(initialVideoState)
      setPlayer(initialPlayerState)
    }
  }, [slug])

  useEffect(() => {
    if (!guest) return
    api.resume(guest.id, slug).then(
      async (resume) => {
        if (!resume || resume.position_ms == null) return setResumePoint(null)
        // Earlier answers and doubts; without them the lecture still resumes
        const state = await api.videoState(resume.session_id).catch(() => null)
        setResumePoint({ resume, state })
      },
      () => setResumePoint(null),
    )
  }, [guest, slug])

  useEffect(() => {
    if (error) {
      toast.error(error)
      setVideo({ error: null })
    }
  }, [error])

  /** The Start / Continue click: the user gesture that allows playback */
  async function start(from: Resume | null) {
    if (!lesson || !guest || !tutor) return
    // Safari unlocks media per element and only inside the gesture, so this runs before anything is awaited
    tutor.unlock()
    // Ask for the mic now, before the video plays, so the prompt can't interrupt the first doubt or full screen
    await askForMic(() => toast.error(t(lesson, 'micPermission')))
    try {
      if (from) {
        await tutor.start(lesson, from.session_id, from.position_ms ?? 0, resumePoint?.state ?? undefined)
      } else {
        const session = await api.createSession(guest.id, lesson.slug)
        await tutor.start(lesson, session.id)
      }
    } catch {
      toast.error(t(lesson, 'videoError'))
    }
  }

  if (!lesson || !tutor) return <p className="text-muted-foreground p-8 text-sm">Loading lesson…</p>

  const chapters = lesson.chapters ?? []
  const chapter = chapters[chapterIdx]
  const durationMs = lesson.video?.duration_ms ?? 0
  const canPause = mode === 'playing' || mode === 'paused'
  const seekable = canSeek(video)
  const card =
    mode === 'idle' ? (
      <StartCard lesson={lesson} resume={resumePoint?.resume ?? null} onStart={(from) => void start(from)} />
    ) : mode === 'done' ? (
      <VideoSummaryCard onExit={onExit} />
    ) : null

  return (
    <div className="mx-auto flex w-full max-w-7xl flex-col gap-4 pb-36 lg:pb-0">
      <header className="flex items-center gap-2 sm:gap-3">
        <Button variant="ghost" size="icon" onClick={onExit} aria-label="Back">
          <ArrowLeft className="size-4" />
        </Button>
        <div className="min-w-0 flex-1">
          <h1 className="truncate text-lg font-semibold">{lesson.title}</h1>
          <p className="text-muted-foreground truncate text-sm">
            {chapter ? fill(t(lesson, 'chapterOf'), { n: chapterIdx + 1, total: chapters.length, title: chapter.title }) : ' '}
          </p>
        </div>
        <Progress value={durationMs ? Math.round((100 * positionMs) / durationMs) : 0} className="w-20 shrink-0 sm:w-40" />
      </header>

      {/*
        Full screen keeps the same elements mounted (the engine owns the <video>) and only changes their layout:
        video, checkpoint answers and seek bar in one column, mic and pause in a rail (right in landscape, below in
        portrait); the header, doubts and chapters are hidden.
      */}
      <div
        className={cn(
          full
            ? 'fixed inset-0 z-50 flex flex-col gap-3 bg-black pt-[max(env(safe-area-inset-top),0.5rem)] pr-[max(env(safe-area-inset-right),0.5rem)] pb-[max(env(safe-area-inset-bottom),0.5rem)] pl-[max(env(safe-area-inset-left),0.5rem)] landscape:flex-row'
            : 'grid gap-4 lg:grid-cols-[1fr_300px]',
        )}
      >
        <div className={cn('flex flex-col', full ? 'min-h-0 min-w-0 flex-1 gap-2' : 'gap-3')}>
          {/* A phone held upright can't make a 16:9 video any wider; Android turns itself, iPhone needs the student */}
          {full && <p className="hidden shrink-0 text-center text-xs text-white/60 portrait:block">{t(lesson, 'rotateHint')}</p>}
          <VideoPlayer tutor={tutor} lesson={lesson} fullscreen={full} onToggleFullscreen={toggleFull}>
            {card}
          </VideoPlayer>
          <CheckpointBar tutor={tutor} />
          <ThreadPrompt tutor={tutor} />
          <p
            className={cn(
              full
                ? 'shrink-0 rounded-md bg-white/10 px-3 py-1.5 text-xs leading-snug text-white [@media(max-height:500px)]:hidden'
                : 'bg-muted min-h-12 rounded-lg px-4 py-3 text-sm leading-relaxed',
              full && !caption && 'hidden',
            )}
          >
            {/* Clamped inside the padding, which would otherwise show the start of a third line */}
            <span className={cn(full && 'line-clamp-2')}>{caption || ' '}</span>
          </p>
        </div>

        {/* Sticks beside the video on desktop and never grows past the viewport: the lists scroll inside their boxes */}
        <aside
          className={cn(
            full
              ? 'flex shrink-0 items-center justify-center landscape:w-32'
              : 'flex flex-col gap-6 rounded-xl border p-4 lg:sticky lg:top-4 lg:max-h-[calc(100svh-2rem)] lg:overflow-y-auto',
          )}
        >
          <ControlBar inline={full}>
            <MicButton tutor={tutor} state={micState(video)} />
            <Button
              variant="outline"
              size="icon"
              className="size-12 rounded-full"
              disabled={!canPause}
              onClick={() => tutor.togglePause()}
              aria-label={mode === 'paused' ? 'Resume' : 'Pause'}
            >
              {mode === 'paused' ? <Play className="size-5" /> : <Pause className="size-5" />}
            </Button>
          </ControlBar>
          <div className={cn('flex min-h-0 shrink-0 flex-col gap-2', full && 'hidden')}>
            <h2 className="text-sm font-medium">{t(lesson, 'yourDoubts')}</h2>
            <VideoDoubtList className="max-h-64 lg:max-h-[40svh]" />
          </div>
          <div className={cn('flex min-h-0 shrink-0 flex-col gap-2', full && 'hidden')}>
            <button
              type="button"
              aria-expanded={chaptersOpen}
              aria-controls="video-chapters"
              onClick={() => {
                const open = !chaptersOpen
                setChaptersOpen(open)
                writeChaptersOpen(open)
              }}
              className="hover:bg-muted -mx-2 flex items-center justify-between rounded-md px-2 py-1 text-left text-sm font-medium transition-colors"
            >
              <span>
                {t(lesson, 'chapters')}
                {chapters.length > 0 && <span className="text-muted-foreground ml-1 text-xs tabular-nums">({chapters.length})</span>}
              </span>
              <ChevronDown className={cn('text-muted-foreground size-4 transition-transform', chaptersOpen && 'rotate-180')} />
            </button>
            {chaptersOpen && (
              <div id="video-chapters" className="max-h-72 overflow-y-auto overscroll-contain lg:max-h-[35svh]">
                <ChapterList chapters={chapters} currentIdx={chapterIdx} disabled={!seekable} onSelect={(ms) => tutor.seek(ms)} />
              </div>
            )}
          </div>
        </aside>
      </div>
    </div>
  )
}
