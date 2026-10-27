import { lazy, type ReactNode, Suspense, useEffect, useMemo, useRef } from 'react'

import { Board } from '@/components/Board'
import { Badge } from '@/components/ui/badge'
import { SeekBar } from '@/components/video/SeekBar'
import { doneCheckpoints } from '@/engine/video/checkpoints'
import type { VideoTutor } from '@/engine/video/videoTutor'
import type { Lesson } from '@/lib/types'
import { cn } from '@/lib/utils'
import { useVideo } from '@/store/video'

// CanvasKit is a few MB of WebAssembly, so only Skia lessons load it
const SkiaBoard = lazy(() => import('@/components/SkiaBoard'))

/**
 * The lecture video (owned by the engine once attached), the whiteboard overlay that answers doubts over the
 * dimmed, paused video, a slot for cards (start, checkpoint, summary) and the seek bar.
 */
export function VideoPlayer({ tutor, lesson, children }: { tutor: VideoTutor; lesson: Lesson; children?: ReactNode }) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const mode = useVideo((s) => s.mode)
  const overlay = useVideo((s) => s.overlay)
  const positionMs = useVideo((s) => s.positionMs)
  const answered = useVideo((s) => s.answered)

  useEffect(() => {
    if (videoRef.current) tutor.attach(videoRef.current)
  }, [tutor])

  const chapters = lesson.chapters ?? []
  const checkpoints = lesson.checkpoints ?? []
  const done = useMemo(() => doneCheckpoints(lesson.checkpoints ?? [], answered), [lesson, answered])

  return (
    <div className="flex flex-col gap-2">
      <div className="relative aspect-video overflow-hidden rounded-xl border bg-black shadow-sm">
        <video
          ref={videoRef}
          src={lesson.video?.url}
          playsInline
          preload="metadata"
          className={cn('block h-full w-full transition-[filter]', overlay && 'brightness-50')}
        />
        {overlay && (
          <div className="absolute inset-0 opacity-95">
            {lesson.renderer === 'skia' ? (
              <Suspense fallback={null}>
                <SkiaBoard />
              </Suspense>
            ) : (
              <Board />
            )}
            <Badge className="absolute top-3 right-3 bg-amber-500">Doubt</Badge>
          </div>
        )}
        {children && <div className="absolute inset-0 flex items-center justify-center bg-black/30 p-4">{children}</div>}
      </div>
      <SeekBar
        positionMs={positionMs}
        durationMs={lesson.video?.duration_ms ?? 0}
        chapters={chapters}
        checkpoints={checkpoints}
        done={done}
        disabled={mode !== 'playing' && mode !== 'paused'}
        onSeek={(ms) => tutor.seek(ms)}
      />
    </div>
  )
}
