import { ArrowLeft, Clock3, LogOut, Mic, Play } from 'lucide-react'
import { type FormEvent, useEffect, useState } from 'react'
import { toast } from 'sonner'

import { Logo } from '@/components/Logo'
import { Button } from '@/components/ui/button'
import { Progress } from '@/components/ui/progress'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { api } from '@/lib/api'
import type { LessonKind, LessonProgress, LessonSummary } from '@/lib/types'
import { useGuestStore } from '@/store/guest'

/** Lessons are shown whatever their language, so a new guest's language is only a default the API requires. */
const GUEST_LANGUAGE = 'english'

function GuestForm({ onBack }: { onBack: () => void }) {
  const setGuest = useGuestStore((s) => s.setGuest)
  const [name, setName] = useState('')
  const [classLevel, setClassLevel] = useState('12')
  const [saving, setSaving] = useState(false)

  async function submit(e: FormEvent) {
    e.preventDefault()
    setSaving(true)
    try {
      setGuest(await api.createGuest({ name: name.trim(), class_level: Number(classLevel), language: GUEST_LANGUAGE }))
    } catch {
      toast.error('Could not save. Please try again.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="mx-auto flex min-h-[calc(100svh-4rem)] w-full max-w-md flex-col">
      <div className="flex items-center justify-between">
        <Logo />
        <Button variant="ghost" size="sm" onClick={onBack}>
          <ArrowLeft className="size-4" /> Back
        </Button>
      </div>
      <form onSubmit={submit} className="my-auto flex flex-col gap-6 py-10">
        <div>
          <h1 className="text-3xl font-semibold tracking-tight text-balance">Let's get you started</h1>
          <p className="text-muted-foreground mt-2">Just your name and class. No sign-up or password.</p>
        </div>
        <div className="grid gap-2">
          <Label htmlFor="name">Your name</Label>
          <Input id="name" className="h-11" required autoFocus maxLength={60} value={name} onChange={(e) => setName(e.target.value)} />
        </div>
        <div className="grid gap-2">
          <Label>Class</Label>
          <Select value={classLevel} onValueChange={setClassLevel}>
            <SelectTrigger className="h-11! w-full">
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
        <Button type="submit" size="lg" className="h-11 w-full" disabled={saving || !name.trim()}>
          Continue
        </Button>
      </form>
    </div>
  )
}

function LectureCard({ lesson, progress, onStart }: { lesson: LessonSummary; progress?: LessonProgress; onStart: () => void }) {
  return (
    <article className="overflow-hidden rounded-2xl border shadow-sm md:grid md:grid-cols-[1.25fr_1fr]">
      <button type="button" onClick={onStart} aria-label={`Open ${lesson.title}`} className="group relative block aspect-video overflow-hidden bg-black md:aspect-auto md:h-full">
        <img src="/landing/lecture.jpg" alt="" className="h-full w-full object-cover transition-transform duration-500 group-hover:scale-[1.02]" />
        <span className="absolute inset-0 flex items-center justify-center bg-black/10">
          <span className="flex size-14 items-center justify-center rounded-full bg-white/95 text-black shadow-lg">
            <Play className="ml-0.5 size-6 fill-current" />
          </span>
        </span>
      </button>
      <div className="flex flex-col gap-4 p-5 sm:p-6">
        <div>
          <h2 className="text-xl font-semibold tracking-tight">{lesson.title}</h2>
          <p className="text-muted-foreground mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
            <span className="flex items-center gap-1.5">
              <Clock3 className="size-3.5" /> {lesson.scene_count} chapters
            </span>
            <span className="flex items-center gap-1.5">
              <Mic className="size-3.5" /> Ask doubts by voice
            </span>
          </p>
        </div>
        {progress && (
          <div className="flex items-center gap-3">
            <Progress value={progress.percent} className="flex-1" />
            <span className="text-muted-foreground text-xs tabular-nums">{progress.completed ? 'Done' : `${progress.percent}%`}</span>
          </div>
        )}
        <Button size="lg" className="mt-auto h-11 w-full" onClick={onStart}>
          <Play className="size-4" /> {progress?.resume ? 'Continue lecture' : 'Start lecture'}
        </Button>
      </div>
    </article>
  )
}

export function HomePage({ onStart, onBack }: { onStart: (slug: string, kind: LessonKind) => void; onBack: () => void }) {
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

  if (!guest) return <GuestForm onBack={onBack} />

  // Only the video lectures are offered for now
  const shown = lessons?.filter((l) => l.kind === 'video')

  return (
    <div className="mx-auto flex w-full max-w-4xl flex-col gap-8">
      <header className="flex items-center justify-between gap-3">
        <Logo />
        <Button
          size="sm"
          className="bg-red-600 text-white hover:bg-red-700"
          onClick={() => {
            setGuest(null)
            onBack()
          }}
        >
          <LogOut className="size-4" /> Log out
        </Button>
      </header>
      <div className="min-w-0">
        <h1 className="truncate text-3xl font-semibold tracking-tight">Hi {guest.name}!</h1>
        <p className="text-muted-foreground mt-1">Pick up a lecture. Tap the mic whenever you get stuck.</p>
      </div>
      {lessons === null && <p className="text-muted-foreground text-sm">Loading lectures…</p>}
      {shown?.length === 0 && <p className="text-muted-foreground text-sm">No lectures yet. Please check back soon.</p>}
      <div className="grid gap-5">
        {shown?.map((l) => (
          <LectureCard key={l.slug} lesson={l} progress={progress[l.slug]} onStart={() => onStart(l.slug, 'video')} />
        ))}
      </div>
    </div>
  )
}
