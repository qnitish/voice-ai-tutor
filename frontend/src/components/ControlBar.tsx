import type { ReactNode } from 'react'

import { cn } from '@/lib/utils'

/**
 * Mic and pause: a bar fixed to the bottom of the screen below `lg` (in thumb reach on a phone), and inline in the
 * side panel on desktop. Pages add `pb-36 lg:pb-0` so the bar doesn't cover the end of the page.
 */
export function ControlBar({ children, inline = false }: { children: ReactNode; /** in the full-screen rail */ inline?: boolean }) {
  return (
    <div
      className={cn(
        'flex shrink-0 items-center justify-center gap-6',
        inline
          ? 'landscape:w-full landscape:flex-col'
          : [
              'bg-background/95 fixed inset-x-0 bottom-0 z-20 border-t px-4 pt-3 pb-[max(env(safe-area-inset-bottom),0.75rem)] backdrop-blur',
              'lg:static lg:z-auto lg:border-0 lg:bg-transparent lg:p-0 lg:backdrop-blur-none',
            ],
      )}
    >
      {children}
    </div>
  )
}

/**
 * A card for the board (start, check, summary): under the board on small screens, where a 16:9 board is too short
 * to hold it, and over the board from `md` up. Render it inside a `relative` box that holds only the board.
 */
export function BoardCard({ children, dim = 'md:bg-black/20' }: { children: ReactNode; /** md:-prefixed backdrop class */ dim?: string }) {
  return (
    <div className={cn('mt-3 flex justify-center md:absolute md:inset-0 md:mt-0 md:items-center md:overflow-y-auto md:rounded-xl md:p-4', dim)}>
      {children}
    </div>
  )
}
