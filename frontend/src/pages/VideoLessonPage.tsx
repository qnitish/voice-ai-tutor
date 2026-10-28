import { ArrowLeft, ChevronDown, Pause, Play } from 'lucide-react'
import { useEffect, useState } from 'react'
import { toast } from 'sonner'

import { LessonTags } from '@/components/LessonTags'
import { MicButton } from '@/components/MicButton'
import { Button } from '@/components/ui/button'
import { Progress } from '@/components/ui/progress'
import { ChapterList } from '@/components/video/ChapterList'
import { CheckpointBar } from '@/components/video/CheckpointBar'
import { ThreadPrompt } from '@/components/video/ThreadPrompt'
import { fill } from '@/components/video/format'
import { StartCard } from '@/components/video/StartCard'
import { VideoDoubtList } from '@/components/video/VideoDoubtList'
import { VideoPlayer } from '@/components/video/VideoPlayer'
import { VideoSummaryCard } from '@/components/video/VideoSummaryCard'
import { VideoTutor } from '@/engine/video/videoTutor'
import { api } from '@/lib/api'
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
    <div className="mx-auto flex w-full max-w-7xl flex-col gap-4">
      <header className="flex flex-wrap items-center gap-3">
        <Button variant="ghost" size="icon" onClick={onExit} aria-label="Back">
          <ArrowLeft className="size-4" />
        </Button>
        <div className="min-w-0 flex-1">
          <h1 className="truncate text-lg font-semibold">{lesson.title}</h1>
          <p className="text-muted-foreground truncate text-sm">
            {chapter ? fill(t(lesson, 'chapterOf'), { n: chapterIdx + 1, total: chapters.length, title: chapter.title }) : ' '}
          </p>
        </div>
        <LessonTags tags={lesson.tags} />
        <Progress value={durationMs ? Math.round((100 * positionMs) / durationMs) : 0} className="w-40" />
      </header>

      <div className="grid gap-4 lg:grid-cols-[1fr_300px]">
        <div className="flex flex-col gap-3">
          <VideoPlayer tutor={tutor} lesson={lesson}>
            {card}
          </VideoPlayer>
          <CheckpointBar tutor={tutor} />
          <ThreadPrompt tutor={tutor} />
          <p className="bg-muted min-h-12 rounded-lg px-4 py-3 text-sm leading-relaxed">{caption || ' '}</p>
        </div>

        {/* Sticks beside the video on desktop and never grows past the viewport: the lists scroll inside their boxes */}
        <aside className="flex flex-col gap-6 rounded-xl border p-4 lg:sticky lg:top-4 lg:max-h-[calc(100svh-2rem)] lg:overflow-y-auto">
          <div className="flex shrink-0 items-center justify-center gap-6">
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
          </div>
          <div className="flex min-h-0 shrink-0 flex-col gap-2">
            <h2 className="text-sm font-medium">{t(lesson, 'yourDoubts')}</h2>
            <VideoDoubtList className="max-h-64 lg:max-h-[40svh]" />
          </div>
          <div className="flex min-h-0 shrink-0 flex-col gap-2">
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
