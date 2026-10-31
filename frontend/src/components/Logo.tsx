import { Mic } from 'lucide-react'

export function Logo() {
  return (
    <span className="flex items-center gap-2 font-semibold tracking-tight">
      <span className="flex size-8 items-center justify-center rounded-lg bg-amber-400 text-stone-950">
        <Mic className="size-4" />
      </span>
      Voice Tutor
    </span>
  )
}
