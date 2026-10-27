import { ArrowLeft, Pause, Play } from 'lucide-react'
import { useEffect, useState } from 'react'
import { toast } from 'sonner'

import { LessonTags } from '@/components/LessonTags'
import { MicButton } from '@/components/MicButton'
import { Button } from '@/components/ui/button'
import { Progress } from '@/components/ui/progress'
import { ChapterList } from '@/components/video/ChapterList'
import { CheckpointCard } from '@/components/video/CheckpointCard'
import { fill } from '@/components/video/format'
import { StartCard } from '@/components/video/StartCard'
import { ThreadPrompt } from '@/components/video/ThreadPrompt'
import { VideoDoubtList } from '@/components/video/VideoDoubtList'
import { VideoPlayer } from '@/components/video/VideoPlayer'
import { VideoSummaryCard } from '@/components/video/VideoSummaryCard'
import { VideoTutor } from '@/engine/video/videoTutor'
import { api } from '@/lib/api'
import { t } from '@/lib/i18n'
import type { Lesson, Resume, VideoSessionState } from '@/lib/types'
import { useGuestStore } from '@/store/guest'
import { initialPlayerState, setPlayer } from '@/store/player'
import { initialVideoState, micState, setVideo, useVideo } from '@/store/video'

/** A saved position to continue from, with the session's earlier answers and doubts (fetched before Start). */
interface ResumePoint {
  resume: Resume
  state: VideoSessionState | null
}

export function VideoLessonPage({ slug, onExit }: { slug: string; onExit: () => void }) {
  const guest = useGuestStore((s) => s.guest)
  const [lesson, setLesson] = useState<Lesson | null>(null)
  const [tutor, setTutor] = useState<VideoTutor | null>(null)
  const [resumePoint, setResumePoint] = useState<ResumePoint | null>(null)
  // The whole store: the seek position changes often anyway, and the mic state derives from several fields
  const video = useVideo()
  const { mode, positionMs, chapterIdx, caption, error, checkpoint } = video

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
  const canSeek = mode === 'playing' || mode === 'paused'
  const card =
    mode === 'idle' ? (
      <StartCard lesson={lesson} resume={resumePoint?.resume ?? null} onStart={(from) => void start(from)} />
    ) : mode === 'done' ? (
      <VideoSummaryCard onExit={onExit} />
    ) : checkpoint && mode === 'checkpoint' && checkpoint.stage !== 'reteach' ? (
      <CheckpointCard tutor={tutor} />
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
          <ThreadPrompt tutor={tutor} />
          <p className="bg-muted min-h-12 rounded-lg px-4 py-3 text-sm leading-relaxed">{caption || ' '}</p>
        </div>

        <aside className="flex flex-col gap-6 rounded-xl border p-4">
          <div className="flex items-center justify-center gap-6">
            <MicButton tutor={tutor} state={micState(video)} />
            <Button
              variant="outline"
              size="icon"
              className="size-12 rounded-full"
              disabled={!canSeek}
              onClick={() => tutor.togglePause()}
              aria-label={mode === 'paused' ? 'Resume' : 'Pause'}
            >
              {mode === 'paused' ? <Play className="size-5" /> : <Pause className="size-5" />}
            </Button>
          </div>
          <div className="flex min-h-0 flex-col gap-2">
            <h2 className="text-sm font-medium">{t(lesson, 'chapters')}</h2>
            <ChapterList chapters={chapters} currentIdx={chapterIdx} disabled={!canSeek} onSelect={(ms) => tutor.seek(ms)} />
          </div>
          <div className="flex min-h-0 flex-col gap-2">
            <h2 className="text-sm font-medium">{t(lesson, 'yourDoubts')}</h2>
            <VideoDoubtList />
          </div>
        </aside>
      </div>
    </div>
  )
}
