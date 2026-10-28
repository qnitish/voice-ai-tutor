import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from '@/components/ui/card'
import { t } from '@/lib/i18n'
import { useVideo } from '@/store/video'

/** End of the lecture: the session summary from store/video (SummaryCard reads the scene player's store). */
export function VideoSummaryCard({ onExit }: { onExit: () => void }) {
  const summary = useVideo((s) => s.summary)
  const lesson = useVideo((s) => s.lesson)

  return (
    <Card className="w-full max-w-xl shadow-xl">
      <CardHeader>
        <CardTitle>{summary ? t(lesson, 'summaryTitle') : t(lesson, 'videoEnded')}</CardTitle>
      </CardHeader>
      {summary && (
        <CardContent className="flex flex-col gap-4 text-sm">
          <p className="leading-relaxed">{summary.lines.join(' ')}</p>
          {summary.revise.length > 0 && (
            <div>
              <p className="mb-1 font-medium">{t(lesson, 'revise')}</p>
              <ul className="list-disc pl-5">
                {summary.revise.map((r) => (
                  <li key={r}>{r}</li>
                ))}
              </ul>
            </div>
          )}
          {summary.weak_concepts.length > 0 && (
            <div className="flex flex-wrap gap-2">
              {summary.weak_concepts.map((c) => (
                <Badge key={c} variant="secondary">
                  {c}
                </Badge>
              ))}
            </div>
          )}
        </CardContent>
      )}
      <CardFooter className="justify-end">
        <Button onClick={onExit}>{t(lesson, 'backToLessons')}</Button>
      </CardFooter>
    </Card>
  )
}
