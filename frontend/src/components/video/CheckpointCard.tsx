import { Check, Mic, X } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from '@/components/ui/card'
import { fill } from '@/components/video/format'
import type { VideoTutor } from '@/engine/video/videoTutor'
import { t } from '@/lib/i18n'
import { cn } from '@/lib/utils'
import { useVideo } from '@/store/video'

/**
 * One checkpoint question ("Question n of N"): options, wrong picks marked, the hint before the retry, the
 * explanation once answered, Skip / Continue. The page hides it while the teacher's replay plays (stage 'reteach').
 */
export function CheckpointCard({ tutor }: { tutor: VideoTutor }) {
  const cp = useVideo((s) => s.checkpoint)
  const lesson = useVideo((s) => s.lesson)
  const checkpoint = cp ? lesson?.checkpoints?.[cp.idx] : undefined
  const q = cp && checkpoint ? checkpoint.questions[cp.questionIdx] : undefined
  if (!cp || !checkpoint || !q) return null
  const { result, stage, wrong, busy } = cp
  const done = stage === 'done'
  const lastWrong = wrong.at(-1)
  const hint = stage === 'retry' && lastWrong !== undefined ? q.why_wrong[lastWrong]?.text : undefined

  return (
    <Card className="w-full max-w-xl shadow-xl">
      <CardHeader>
        <p className="text-muted-foreground text-xs font-medium tracking-wide uppercase">
          {fill(t(lesson, 'questionOf'), { n: cp.questionIdx + 1, total: checkpoint.questions.length })}
          {stage === 'retry' && ` · ${t(lesson, 'tryAgain')}`}
        </p>
        <CardTitle className="text-lg leading-snug">{q.question}</CardTitle>
      </CardHeader>
      <CardContent className="grid gap-2">
        {q.options.map((opt, i) => {
          // The right answer is only shown once there are no more tries
          const isCorrect = done && result && i === result.correct_index
          const isWrongPick = wrong.includes(i)
          return (
            <Button
              key={i}
              variant="outline"
              disabled={done || busy || isWrongPick || stage === 'intro'}
              onClick={() => void tutor.answerCheckpoint(i)}
              className={cn(
                'h-auto justify-start py-3 text-left whitespace-normal disabled:opacity-100',
                isCorrect && 'border-green-600 bg-green-50 text-green-900',
                isWrongPick && 'border-red-600 bg-red-50 text-red-900',
              )}
            >
              <span className="font-semibold">{'ABCD'[i]}.</span> {opt}
              {isCorrect && <Check className="ml-auto size-4" />}
              {isWrongPick && <X className="ml-auto size-4" />}
            </Button>
          )
        })}
        {hint && <p className="mt-1 rounded-md bg-amber-50 px-3 py-2 text-sm text-amber-900">{hint}</p>}
        {done && result && <p className="text-muted-foreground mt-1 text-sm">{result.explanation}</p>}
        {done && cp.followup && (
          <p className="mt-1 flex items-center gap-2 text-sm font-medium">
            <Mic className="size-4" /> {t(lesson, 'confusion')}
          </p>
        )}
      </CardContent>
      <CardFooter className="justify-end gap-2">
        {!done && (
          <Button variant="ghost" disabled={busy} onClick={() => tutor.skipCheckpointQuestion()}>
            {t(lesson, 'skip')}
          </Button>
        )}
        {done && (
          <Button disabled={busy} onClick={() => tutor.continueAfterCheckpoint()}>
            {t(lesson, 'next')}
          </Button>
        )}
      </CardFooter>
    </Card>
  )
}
