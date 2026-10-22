import { Check, Mic, X } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from '@/components/ui/card'
import type { Tutor } from '@/engine/tutor'
import { cn } from '@/lib/utils'
import { usePlayer } from '@/store/player'

export function CheckCard({ tutor }: { tutor: Tutor }) {
  const check = usePlayer((s) => s.check)
  const lesson = usePlayer((s) => s.lesson)
  const cq = check && lesson?.scenes[check.sceneIdx]?.check_question
  const q = check?.twin ? cq?.twin : cq
  if (!check || !cq || !q) return null
  const { result, stage, wrong } = check
  const done = stage === 'done'
  const lastWrong = wrong.at(-1)
  const hint = stage === 'retry' && lastWrong !== undefined ? cq.why_wrong?.[lastWrong]?.text : undefined

  return (
    <Card className="w-full max-w-xl shadow-xl">
      <CardHeader>
        <p className="text-muted-foreground text-xs font-medium uppercase tracking-wide">
          {check.twin ? 'Ek aur sawal' : stage === 'retry' ? 'Ek baar phir try karo' : 'Quick check'}
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
              disabled={done || check.busy || isWrongPick}
              onClick={() => void tutor.answerCheck(i)}
              className={cn(
                'h-auto justify-start whitespace-normal py-3 text-left disabled:opacity-100',
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
        {done && check.followup && (
          <p className="mt-1 flex items-center gap-2 text-sm font-medium">
            <Mic className="size-4" /> Kahin confusion hai? Mic dabake batao.
          </p>
        )}
      </CardContent>
      <CardFooter className="justify-end gap-2">
        {!done && (
          <Button variant="ghost" disabled={check.busy} onClick={() => tutor.continueAfterCheck()}>
            Skip
          </Button>
        )}
        {done && (
          <Button onClick={() => tutor.continueAfterCheck()}>Aage chalein</Button>
        )}
      </CardFooter>
    </Card>
  )
}
