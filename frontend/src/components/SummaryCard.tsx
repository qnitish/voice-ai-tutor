import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from '@/components/ui/card'
import { usePlayer } from '@/store/player'

export function SummaryCard({ onExit }: { onExit: () => void }) {
  const summary = usePlayer((s) => s.summary)
  if (!summary) return null

  return (
    <Card className="w-full max-w-xl shadow-xl">
      <CardHeader>
        <CardTitle>Aaj ka summary</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-4 text-sm">
        <p className="leading-relaxed">{summary.lines.join(' ')}</p>
        {summary.revise.length > 0 && (
          <div>
            <p className="mb-1 font-medium">Revise karo</p>
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
      <CardFooter className="justify-end">
        <Button onClick={onExit}>Back to lessons</Button>
      </CardFooter>
    </Card>
  )
}
