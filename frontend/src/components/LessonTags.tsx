import { Badge } from '@/components/ui/badge'

/** Language, voice, build number and build note of a lesson, so builds of the same lecture can be told apart. */
export function LessonTags({ tags }: { tags?: string[] }) {
  if (!tags?.length) return null
  return (
    <div className="flex flex-wrap gap-1.5">
      {tags.map((t) => (
        <Badge key={t} variant="secondary">
          {t}
        </Badge>
      ))}
    </div>
  )
}
