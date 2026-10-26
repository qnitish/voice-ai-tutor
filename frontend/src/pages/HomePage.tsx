import { BookOpen, Play } from 'lucide-react'
import { type FormEvent, useEffect, useState } from 'react'
import { toast } from 'sonner'

import { LessonTags } from '@/components/LessonTags'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '@/components/ui/card'
import { Progress } from '@/components/ui/progress'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { api } from '@/lib/api'
import { type Language, languageOf } from '@/lib/i18n'
import type { LessonProgress, LessonSummary } from '@/lib/types'
import { useGuestStore } from '@/store/guest'

const LANGUAGES: { value: Language; label: string }[] = [
  { value: 'hinglish', label: 'Hinglish' },
  { value: 'english', label: 'English' },
]

/** The student's lesson language; saved on the guest so it is remembered. */
function LanguageToggle({ value, onChange }: { value: Language; onChange: (l: Language) => void }) {
  return (
    <div className="bg-muted inline-flex rounded-lg p-1" role="radiogroup" aria-label="Lesson language">
      {LANGUAGES.map((l) => (
        <Button
          key={l.value}
          size="sm"
          variant={value === l.value ? 'default' : 'ghost'}
          role="radio"
          aria-checked={value === l.value}
          onClick={() => onChange(l.value)}
        >
          {l.label}
        </Button>
      ))}
    </div>
  )
}

function GuestForm() {
  const setGuest = useGuestStore((s) => s.setGuest)
  const [name, setName] = useState('')
  const [classLevel, setClassLevel] = useState('12')
  const [language, setLanguage] = useState('hinglish')
  const [saving, setSaving] = useState(false)

  async function submit(e: FormEvent) {
    e.preventDefault()
    setSaving(true)
    try {
      setGuest(await api.createGuest({ name: name.trim(), class_level: Number(classLevel), language }))
    } catch {
      toast.error('Could not save. Is the backend running?')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Card className="mx-auto w-full max-w-md">
      <form onSubmit={submit}>
        <CardHeader>
          <CardTitle>Namaste! 👋</CardTitle>
          <CardDescription>Tell us a little about you to start learning. No sign-up needed.</CardDescription>
        </CardHeader>
        <CardContent className="mt-4 grid gap-4">
          <div className="grid gap-2">
            <Label htmlFor="name">Your name</Label>
            <Input id="name" required maxLength={60} value={name} onChange={(e) => setName(e.target.value)} />
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div className="grid gap-2">
              <Label>Class</Label>
              <Select value={classLevel} onValueChange={setClassLevel}>
                <SelectTrigger className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {[6, 7, 8, 9, 10, 11, 12].map((c) => (
                    <SelectItem key={c} value={String(c)}>
                      Class {c}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="grid gap-2">
              <Label>Language</Label>
              <Select value={language} onValueChange={setLanguage}>
                <SelectTrigger className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {LANGUAGES.map((l) => (
                    <SelectItem key={l.value} value={l.value}>
                      {l.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
        </CardContent>
        <CardFooter className="mt-6">
          <Button type="submit" className="w-full" disabled={saving || !name.trim()}>
            Continue
          </Button>
        </CardFooter>
      </form>
    </Card>
  )
}

export function HomePage({ onStart }: { onStart: (slug: string) => void }) {
  const { guest, setGuest } = useGuestStore()
  const [lessons, setLessons] = useState<LessonSummary[] | null>(null)
  const [progress, setProgress] = useState<Record<string, LessonProgress>>({})

  useEffect(() => {
    api.listLessons().then(setLessons, () => setLessons([]))
  }, [])

  useEffect(() => {
    if (!guest) return
    api.progress(guest.id).then(
      (rows) => setProgress(Object.fromEntries(rows.map((p) => [p.slug, p]))),
      () => setProgress({}),
    )
  }, [guest?.id])

  // Drop a stale guest id (e.g. after the database was reset)
  useEffect(() => {
    if (guest) api.getGuest(guest.id).then(setGuest, () => setGuest(null))
  }, [guest?.id, setGuest])

  if (!guest) return <GuestForm />

  const language = languageOf(guest.language)
  const shown = lessons?.filter((l) => languageOf(l.language) === language)

  async function chooseLanguage(next: Language) {
    if (!guest || next === language) return
    setGuest({ ...guest, language: next })
    try {
      setGuest(await api.setLanguage(guest.id, next))
    } catch {
      toast.error('Could not save your language.')
    }
  }

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-6">
      <div className="flex items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold">Hi {guest.name}!</h1>
          <p className="text-muted-foreground text-sm">Pick a lesson. You can interrupt the tutor anytime with the mic.</p>
        </div>
        <div className="flex items-center gap-2">
          <LanguageToggle value={language} onChange={(l) => void chooseLanguage(l)} />
          <Button variant="ghost" size="sm" onClick={() => setGuest(null)}>
            Not {guest.name}?
          </Button>
        </div>
      </div>
      {lessons === null && <p className="text-muted-foreground text-sm">Loading lessons…</p>}
      {lessons !== null && lessons.length > 0 && shown?.length === 0 && (
        <p className="text-muted-foreground text-sm">No lessons in {language === 'english' ? 'English' : 'Hinglish'} yet.</p>
      )}
      {lessons?.length === 0 && (
        <p className="text-muted-foreground text-sm">
          No lessons yet. Run <code>python -m app.prep</code> in the backend to create one.
        </p>
      )}
      <div className="grid gap-4 sm:grid-cols-2">
        {shown?.map((l) => {
          const p = progress[l.slug]
          return (
            <Card key={l.slug}>
              <CardHeader>
                <BookOpen className="text-muted-foreground mb-2 size-5" />
                <CardTitle className="leading-snug">{l.title}</CardTitle>
                <LessonTags tags={l.tags} />
                <CardDescription>
                  {l.scene_count} scenes
                  {p && p.checks_total > 0 && ` · ${p.checks_correct}/${p.checks_total} checks correct`}
                </CardDescription>
              </CardHeader>
              {p && (
                <CardContent className="mt-4 flex items-center gap-3">
                  <Progress value={p.percent} className="flex-1" />
                  <span className="text-muted-foreground text-xs">{p.completed ? 'Done' : `${p.percent}%`}</span>
                </CardContent>
              )}
              <CardFooter className="mt-4">
                <Button onClick={() => onStart(l.slug)}>
                  <Play className="size-4" /> {p?.resume ? 'Continue lesson' : 'Start lesson'}
                </Button>
              </CardFooter>
            </Card>
          )
        })}
      </div>
    </div>
  )
}
