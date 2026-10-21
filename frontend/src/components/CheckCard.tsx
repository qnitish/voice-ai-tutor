import { Check, X } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from '@/components/ui/card'
import type { Tutor } from '@/engine/tutor'
import { cn } from '@/lib/utils'
import { usePlayer } from '@/store/player'

export function CheckCard({ tutor }: { tutor: Tutor }) {
  const check = usePlayer((s) => s.check)
  const lesson = usePlayer((s) => s.lesson)
  const q = check && lesson?.scenes[check.sceneIdx]?.check_question
  if (!check || !q) return null
  const result = check.result

  return (
    <Card className="w-full max-w-xl shadow-xl">
      <CardHeader>
        <p className="text-muted-foreground text-xs font-medium uppercase tracking-wide">Quick check</p>
        <CardTitle className="text-lg leading-snug">{q.question}</CardTitle>
      </CardHeader>
      <CardContent className="grid gap-2">
        {q.options.map((opt, i) => {
          const isCorrect = result && i === result.correct_index
          const isWrongPick = result && i === check.chosen && !result.correct
          return (
            <Button
              key={i}
              variant="outline"
              disabled={check.chosen !== null}
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
        {result && <p className="text-muted-foreground mt-1 text-sm">{result.explanation}</p>}
      </CardContent>
      <CardFooter className="justify-end gap-2">
        {!result && (
          <Button variant="ghost" onClick={() => tutor.continueAfterCheck()}>
            Skip
          </Button>
        )}
        {result && <Button onClick={() => tutor.continueAfterCheck()}>Aage chalein</Button>}
      </CardFooter>
    </Card>
  )
}
