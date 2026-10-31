import { Play } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Card, CardContent, CardFooter } from '@/components/ui/card'
import { fill, mmss } from '@/components/video/format'
import { t } from '@/lib/i18n'
import type { Lesson, Resume } from '@/lib/types'

/** "Start lecture", or "Continue from m:ss" / "Start over" when the student has a saved position. */
export function StartCard({ lesson, resume, onStart }: { lesson: Lesson; resume: Resume | null; onStart: (from: Resume | null) => void }) {
  const questions = (lesson.checkpoints ?? []).reduce((n, cp) => n + cp.questions.length, 0)
  const continueAt = resume?.position_ms ?? null

  return (
    <Card className="w-full max-w-md shadow-xl">
      <CardContent>
        <p className="text-muted-foreground text-sm">{fill(t(lesson, 'startHint'), { n: lesson.chapters?.length ?? 0, q: questions })}</p>
      </CardContent>
      <CardFooter className="flex flex-wrap gap-3">
        {resume && continueAt !== null ? (
          <>
            <Button size="lg" onClick={() => onStart(resume)}>
              <Play className="size-4" /> {fill(t(lesson, 'continueFrom'), { time: mmss(continueAt) })}
            </Button>
            <Button size="lg" variant="outline" onClick={() => onStart(null)}>
              {t(lesson, 'startOver')}
            </Button>
          </>
        ) : (
          <Button size="lg" onClick={() => onStart(null)}>
            <Play className="size-4" /> {t(lesson, 'startVideo')}
          </Button>
        )}
      </CardFooter>
    </Card>
  )
}
