import { Maximize, Minimize } from 'lucide-react'
import { lazy, type PointerEvent, type ReactNode, Suspense, useEffect, useMemo, useRef } from 'react'

import { Board } from '@/components/Board'
import { BoardCard } from '@/components/ControlBar'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { SeekBar } from '@/components/video/SeekBar'
import { useAutoHide } from '@/components/video/useAutoHide'
import { doneCheckpoints } from '@/engine/video/checkpoints'
import type { VideoTutor } from '@/engine/video/videoTutor'
import type { Lesson } from '@/lib/types'
import { t } from '@/lib/i18n'
import { cn } from '@/lib/utils'
import { canSeek, useVideo } from '@/store/video'

// CanvasKit is a few MB of WebAssembly, so only Skia lessons load it
const SkiaBoard = lazy(() => import('@/components/SkiaBoard'))

/**
 * The lecture video (owned by the engine once attached), the whiteboard overlay that answers doubts over the
 * dimmed, paused video, a slot for cards (start, checkpoint, summary), and the seek bar and full-screen button over
 * the video, which come up on a tap and fade away (they stay while paused; a card replaces them).
 * In full screen the video is fitted into the space it is given and cards cover that space, not just the video.
 */
export function VideoPlayer({
  tutor,
  lesson,
  children,
  fullscreen = false,
  onToggleFullscreen,
}: {
  tutor: VideoTutor
  lesson: Lesson
  children?: ReactNode
  fullscreen?: boolean
  onToggleFullscreen?: () => void
}) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const seekable = useVideo(canSeek)
  const overlay = useVideo((s) => s.overlay)
  const positionMs = useVideo((s) => s.positionMs)
  const answered = useVideo((s) => s.answered)
  // A checkpoint question is on the board (a doubt asked at it shows as a doubt)
  const atCheckpoint = useVideo((s) => s.mode === 'checkpoint')
  const mode = useVideo((s) => s.mode)
  const { shown, wake, hide, toggle } = useAutoHide()

  useEffect(() => {
    if (videoRef.current) tutor.attach(videoRef.current)
  }, [tutor])

  // Up for a moment when the lecture starts or resumes; a board coming up gets to be seen in full
  useEffect(() => {
    // oxlint-disable-next-line react/set-state-in-effect -- follows the engine's mode, which lives outside React
    if (overlay) hide()
    else wake()
  }, [mode, overlay, hide, wake])

  /** A tap flips the controls; a mouse has no "away" tap, so a click only brings them up. */
  function onTap(e: PointerEvent) {
    if (e.pointerType === 'mouse') wake()
    else toggle()
  }

  const chapters = lesson.chapters ?? []
  const checkpoints = lesson.checkpoints ?? []
  const done = useMemo(() => doneCheckpoints(lesson.checkpoints ?? [], answered), [lesson, answered])
  const controlsUp = shown || mode === 'paused'
  const fullscreenLabel = fullscreen ? 'Exit full screen' : 'Full screen'

  return (
    <div className={cn('flex flex-col', fullscreen && 'min-h-0 flex-1')}>
      {/* Full screen: a size container, so the 16:9 box can be as large as fits both its width and height */}
      <div className={cn('relative', fullscreen && 'flex min-h-0 flex-1 items-center justify-center [container-type:size]')}>
        <div
          className={cn(
            'relative aspect-video overflow-hidden bg-black',
            fullscreen ? 'w-[min(100cqw,calc(100cqh*16/9))]' : 'rounded-xl border shadow-sm',
          )}
          onPointerUp={onTap}
          onPointerMove={(e) => e.pointerType === 'mouse' && wake()}
          onPointerLeave={(e) => e.pointerType === 'mouse' && hide()}
        >
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
              <Badge className={cn('absolute top-3 right-3', atCheckpoint ? 'bg-blue-600' : 'bg-amber-500')}>
                {atCheckpoint ? t(lesson, 'checkpointBadge') : 'Doubt'}
              </Badge>
            </div>
          )}
          {!children && (
            <div
              className={cn(
                'absolute inset-x-0 bottom-0 flex items-center gap-1 bg-linear-to-t from-black/80 to-transparent px-2 pt-8 pb-1 transition-opacity duration-200',
                !controlsUp && 'pointer-events-none opacity-0',
              )}
              onPointerDown={wake}
              onPointerUp={(e) => e.stopPropagation()}
              onFocus={wake}
            >
              <div className="min-w-0 flex-1">
                <SeekBar
                  positionMs={positionMs}
                  durationMs={lesson.video?.duration_ms ?? 0}
                  chapters={chapters}
                  checkpoints={checkpoints}
                  done={done}
                  disabled={!seekable}
                  onSeek={(ms) => tutor.seek(ms)}
                />
              </div>
              {onToggleFullscreen && (
                <Button
                  variant="ghost"
                  size="icon"
                  className="shrink-0 text-white hover:bg-white/15 hover:text-white"
                  onClick={onToggleFullscreen}
                  aria-label={fullscreenLabel}
                >
                  {fullscreen ? <Minimize className="size-4" /> : <Maximize className="size-4" />}
                </Button>
              )}
            </div>
          )}
        </div>
        {children &&
          (fullscreen ? (
            <div className="absolute inset-0 flex items-center justify-center overflow-y-auto bg-black/40 p-4">{children}</div>
          ) : (
            <BoardCard dim="md:bg-black/30">{children}</BoardCard>
          ))}
        {/* A card has no controls under it, so full screen keeps its own way out */}
        {children && fullscreen && onToggleFullscreen && (
          <Button
            variant="ghost"
            size="icon"
            className="absolute top-2 right-2 text-white hover:bg-white/15 hover:text-white"
            onClick={onToggleFullscreen}
            aria-label={fullscreenLabel}
          >
            <Minimize className="size-4" />
          </Button>
        )}
      </div>
    </div>
  )
}
