import { Mic } from 'lucide-react'
import { useEffect, useState } from 'react'

import { Button } from '@/components/ui/button'
import { fill } from '@/components/video/format'
import type { VideoTutor } from '@/engine/video/videoTutor'
import { t } from '@/lib/i18n'
import { useVideo } from '@/store/video'

/** Whole seconds until `endsAt` (a Date.now() ms), ticking every half second; 0 when there is no deadline. */
function useCountdown(endsAt: number | null): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (endsAt === null) return
    const id = window.setInterval(() => setNow(Date.now()), 500)
    return () => window.clearInterval(id)
  }, [endsAt])
  return endsAt === null ? 0 : Math.max(0, Math.ceil((endsAt - now) / 1000))
}

/**
 * "Did that make sense?" after an answer: Got it ends the thread, Ask another keeps it open for a follow-up (only
 * while the thread allows one). While a follow-up is awaited the mic is the way to answer; on a thread that has
 * answered nothing yet (the speech wasn't understood) it asks to try again, with Continue to go back to the video.
 * Shows how long until the idle timer resumes the lecture on its own.
 */
export function ThreadPrompt({ tutor }: { tutor: VideoTutor }) {
  const mode = useVideo((s) => s.mode)
  const thread = useVideo((s) => s.thread)
  const promptEndsAt = useVideo((s) => s.promptEndsAt)
  const awaitingFollowup = useVideo((s) => s.awaitingFollowup)
  const lesson = useVideo((s) => s.lesson)
  const seconds = useCountdown(promptEndsAt)
  const show = thread !== null && (mode === 'thread_prompt' || awaitingFollowup)
  if (!show) return null
  // Nothing was answered in this thread yet: the student is asked to say the question again, not "Got it?"
  const retry = thread.turns === 0

  return (
    <div className="flex flex-wrap items-center gap-3 rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-950">
      <p className="flex min-w-0 flex-1 items-center gap-2 font-medium">
        {awaitingFollowup || retry ? (
          <>
            <Mic className="size-4 shrink-0" /> {t(lesson, retry ? 'askAgainHint' : 'followupHint')}
          </>
        ) : (
          t(lesson, 'gotIt')
        )}
      </p>
      {promptEndsAt !== null && <span className="text-xs text-amber-800 tabular-nums">{fill(t(lesson, 'resumingIn'), { s: seconds })}</span>}
      <div className="flex gap-2">
        <Button size="sm" onClick={() => void tutor.reply(true)}>
          {t(lesson, retry ? 'next' : 'gotItYes')}
        </Button>
        {!awaitingFollowup && !retry && thread.can_follow_up && (
          <Button size="sm" variant="outline" onClick={() => void tutor.reply(false)}>
            {t(lesson, 'askAnother')}
          </Button>
        )}
      </div>
    </div>
  )
}
