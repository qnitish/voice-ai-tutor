/**
 * The whiteboard painted with Skia (CanvasKit, WebAssembly) instead of SVG, to compare the two. It paints the same
 * frame list as Board (engine/frame), so only the painting differs. CanvasKit has no access to the page's fonts:
 * the board fonts are bundled as TTF in public/fonts and registered with a font provider (Kalam, then Noto Sans Math
 * and Noto Sans as fallbacks for maths symbols).
 */
import CanvasKitInit, { type Canvas, type CanvasKit, type Paint, type Paragraph, type Path, type PathEffect, type Surface, type TypefaceFontProvider } from 'canvaskit-wasm'
import wasmUrl from 'canvaskit-wasm/bin/canvaskit.wasm?url'

import { BOARD_BG, BOARD_COLORS, clamp01, type FrameItem, frameOf, GRID, type PathMark } from '@/engine/frame'
import { BOARD_H, BOARD_W, type Mark } from '@/engine/geometry'
import type { Color } from '@/lib/types'
import { usePlayer } from '@/store/player'

const FONT_FILES: [family: string, url: string][] = [
  ['Kalam', '/fonts/Kalam-Regular.ttf'],
  ['Kalam', '/fonts/Kalam-Bold.ttf'],
  ['Noto Sans Math', '/fonts/NotoSansMath-Regular.ttf'],
  ['Noto Sans', '/fonts/NotoSans.ttf'],
]
const FAMILIES = ['Kalam', 'Noto Sans Math', 'Noto Sans']
const CACHE_MAX = 600

interface Skia {
  ck: CanvasKit
  fonts: TypefaceFontProvider
}

let loading: Promise<Skia> | null = null

/** CanvasKit and the board fonts, loaded once per page. */
export function loadSkia(): Promise<Skia> {
  loading ??= (async () => {
    const [ck, ...buffers] = await Promise.all([
      CanvasKitInit({ locateFile: () => wasmUrl }),
      ...FONT_FILES.map(async ([, url]) => {
        const res = await fetch(url)
        if (!res.ok) throw new Error(`font ${url}: ${res.status}`)
        return res.arrayBuffer()
      }),
    ])
    const fonts = ck.TypefaceFontProvider.Make()
    buffers.forEach((b, i) => fonts.registerFont(b, FONT_FILES[i][0]))
    return { ck, fonts }
  })()
  loading.catch(() => (loading = null))
  return loading
}

interface Text {
  para: Paragraph
  width: number
  baseline: number
}

/** Draws the player's board on a canvas whenever the store changes; dispose() frees everything. */
export class SkiaPainter {
  private ck: CanvasKit
  private surface: Surface | null = null
  private stroke: Paint
  private fill: Paint
  private layer: Paint
  private dash: PathEffect
  private paths = new Map<string, Path>()
  private texts = new Map<string, Text>()
  private colors = new Map<string, Float32Array>()
  private frame = 0
  private unsubscribe: () => void
  private resize: ResizeObserver
  private skia: Skia
  private canvas: HTMLCanvasElement

  constructor(skia: Skia, canvas: HTMLCanvasElement) {
    this.skia = skia
    this.canvas = canvas
    const ck = (this.ck = skia.ck)
    this.stroke = new ck.Paint()
    this.stroke.setStyle(ck.PaintStyle.Stroke)
    this.stroke.setStrokeCap(ck.StrokeCap.Round)
    this.stroke.setStrokeJoin(ck.StrokeJoin.Round)
    this.stroke.setAntiAlias(true)
    this.fill = new ck.Paint()
    this.fill.setStyle(ck.PaintStyle.Fill)
    this.fill.setAntiAlias(true)
    this.layer = new ck.Paint()
    this.dash = ck.PathEffect.MakeDash([14, 12], 0)
    this.resize = new ResizeObserver(() => this.makeSurface())
    this.resize.observe(canvas)
    canvas.addEventListener('webglcontextlost', this.onLost)
    canvas.addEventListener('webglcontextrestored', this.onRestored)
    this.makeSurface()
    this.unsubscribe = usePlayer.subscribe(() => this.invalidate())
  }

  dispose() {
    cancelAnimationFrame(this.frame)
    this.unsubscribe()
    this.resize.disconnect()
    this.canvas.removeEventListener('webglcontextlost', this.onLost)
    this.canvas.removeEventListener('webglcontextrestored', this.onRestored)
    for (const p of this.paths.values()) p.delete()
    for (const t of this.texts.values()) t.para.delete()
    this.paths.clear()
    this.texts.clear()
    this.stroke.delete()
    this.fill.delete()
    this.layer.delete()
    this.dash.delete()
    this.surface?.delete()
    this.surface = null
  }

  // A phone browser can drop the GPU context in the background; the board is redrawn from the store when it's back
  private onLost = (e: Event) => {
    e.preventDefault()
    this.surface = null
  }

  private onRestored = () => this.makeSurface()

  private makeSurface() {
    const dpr = window.devicePixelRatio || 1
    const w = Math.max(1, Math.round(this.canvas.clientWidth * dpr))
    const h = Math.max(1, Math.round(this.canvas.clientHeight * dpr))
    if (this.surface && this.canvas.width === w && this.canvas.height === h) return
    this.surface?.delete()
    this.canvas.width = w
    this.canvas.height = h
    this.surface = this.ck.MakeWebGLCanvasSurface(this.canvas) ?? this.ck.MakeSWCanvasSurface(this.canvas)
    this.invalidate()
  }

  private invalidate() {
    if (this.frame) return
    this.frame = requestAnimationFrame(() => {
      this.frame = 0
      this.draw()
    })
  }

  private draw() {
    const surface = this.surface
    if (!surface) return
    const { drawn, active, clipTime, layer, boardScroll } = usePlayer.getState()
    const c = surface.getCanvas()
    const { width, height } = this.canvas
    // Fit the board like the SVG's viewBox (xMidYMid meet)
    const scale = Math.min(width / BOARD_W, height / BOARD_H)
    c.clear(this.color('#ffffff'))
    c.save()
    c.translate((width - BOARD_W * scale) / 2, (height - BOARD_H * scale) / 2)
    c.scale(scale, scale)
    this.background(c, layer, boardScroll)
    // Follow-up answers stack below the first: the board scrolls up, and only its own area is painted
    c.clipRect(this.ck.XYWHRect(0, 0, BOARD_W, BOARD_H), this.ck.ClipOp.Intersect, true)
    c.translate(0, -boardScroll)
    for (const item of frameOf(drawn, active, clipTime)) this.item(c, item)
    c.restore()
    surface.flush()
  }

  private background(c: Canvas, layer: 'lesson' | 'doubt', scroll: number) {
    const ck = this.ck
    this.fill.setColor(this.color(BOARD_BG[layer]))
    c.drawRect(ck.XYWHRect(0, 0, BOARD_W, BOARD_H), this.fill)
    this.stroke.setColor(this.color(GRID.color, GRID.opacity))
    this.stroke.setStrokeWidth(1)
    for (let x = 0; x <= BOARD_W; x += GRID.step) c.drawLine(x, 0, x, BOARD_H, this.stroke)
    for (let y = -(scroll % GRID.step); y <= BOARD_H; y += GRID.step) if (y >= 0) c.drawLine(0, y, BOARD_W, y, this.stroke)
  }

  private item(c: Canvas, item: FrameItem) {
    const ck = this.ck
    if (item.k === 'box') {
      const pad = 10
      const { box } = item
      this.fill.setColor(this.color(BOARD_COLORS[item.color] ?? BOARD_COLORS.orange, 0.25))
      const rect = ck.XYWHRect(box.x - pad, box.y - pad, (box.w + pad * 2) * item.p, box.h + pad * 2)
      c.drawRRect(ck.RRectXY(rect, 8, 8), this.fill)
      return
    }
    const moved = item.dx !== 0 || item.dy !== 0
    if (moved) {
      c.save()
      c.translate(item.dx, item.dy)
    }
    if (item.k === 'glow') this.glow(c, item.mark, item.color, item.p)
    else if (item.mark.k === 'path') this.path(c, item.mark, item.p)
    else this.text(c, item.mark, item.p)
    if (moved) c.restore()
  }

  /** Strokes draw themselves (path trimmed to p), dashed ones fade in, fills fade in late. */
  private path(c: Canvas, m: PathMark, p: number) {
    const path = this.pathOf(m.d)
    if (!path) return
    const alpha = m.opacity ?? 1
    if (m.fill) {
      const a = (m.fillOpacity ?? 1) * clamp01((p - 0.6) / 0.4) * alpha
      if (a > 0) {
        this.fill.setColor(this.color(BOARD_COLORS[m.fill], a))
        c.drawPath(path, this.fill)
      }
    }
    if (p <= 0) return
    this.stroke.setStrokeWidth(m.width)
    if (m.dashed) {
      this.stroke.setColor(this.color(BOARD_COLORS[m.color], p * alpha))
      this.stroke.setPathEffect(this.dash)
      c.drawPath(path, this.stroke)
      this.stroke.setPathEffect(null)
      return
    }
    this.stroke.setColor(this.color(BOARD_COLORS[m.color], alpha))
    this.trimmed(c, path, p)
  }

  /** Highlighter traced over a shape's stroke. */
  private glow(c: Canvas, m: PathMark, color: Color, p: number) {
    const path = this.pathOf(m.d)
    if (!path || p <= 0) return
    this.stroke.setStrokeWidth(m.width + 12)
    this.stroke.setColor(this.color(BOARD_COLORS[color] ?? BOARD_COLORS.orange, 0.35))
    this.trimmed(c, path, p)
  }

  private trimmed(c: Canvas, path: Path, p: number) {
    if (p >= 1) {
      c.drawPath(path, this.stroke)
      return
    }
    const part = path.copy()
    if (part.trim(0, p, false)) c.drawPath(part, this.stroke)
    part.delete()
  }

  /** Text is written left to right (clipped to p of its width); labels fade in. */
  private text(c: Canvas, m: Extract<Mark, { k: 'text' }>, p: number) {
    const t = this.textOf(m)
    const x = m.anchor === 'middle' ? m.x - t.width / 2 : m.x
    const top = m.y - t.baseline
    if (m.label) {
      const a = clamp01((p - 0.5) / 0.5)
      if (a <= 0) return
      if (a >= 1) return c.drawParagraph(t.para, x, top)
      this.layer.setAlphaf(a)
      c.saveLayer(this.layer)
      c.drawParagraph(t.para, x, top)
      c.restore()
      return
    }
    if (p >= 1) return c.drawParagraph(t.para, x, top)
    if (p <= 0) return
    c.save()
    c.clipRect(this.ck.XYWHRect(x - 4, m.y - m.px * 1.1, (t.width + 8) * p, m.px * 1.7), this.ck.ClipOp.Intersect, true)
    c.drawParagraph(t.para, x, top)
    c.restore()
  }

  private pathOf(d: string): Path | null {
    let path = this.paths.get(d)
    if (!path) {
      const made = this.ck.Path.MakeFromSVGString(d)
      if (!made) return null
      path = made
      this.remember(this.paths, d, path, (old) => old.delete())
    }
    return path
  }

  private textOf(m: Extract<Mark, { k: 'text' }>): Text {
    const key = `${m.px}|${m.weight}|${m.color}|${m.text}`
    let t = this.texts.get(key)
    if (!t) {
      const ck = this.ck
      const style = new ck.ParagraphStyle({
        textStyle: {
          color: this.color(BOARD_COLORS[m.color]),
          fontFamilies: FAMILIES,
          fontSize: m.px,
          fontStyle: { weight: m.weight >= 700 ? ck.FontWeight.Bold : ck.FontWeight.Normal },
        },
      })
      const builder = ck.ParagraphBuilder.MakeFromFontProvider(style, this.skia.fonts)
      builder.addText(m.text)
      const para = builder.build()
      builder.delete()
      para.layout(100000)
      t = { para, width: para.getMaxIntrinsicWidth(), baseline: para.getAlphabeticBaseline() }
      this.remember(this.texts, key, t, (old) => old.para.delete())
    }
    return t
  }

  private remember<T>(cache: Map<string, T>, key: string, value: T, free: (old: T) => void) {
    if (cache.size >= CACHE_MAX) {
      const [oldKey, old] = cache.entries().next().value as [string, T]
      cache.delete(oldKey)
      free(old)
    }
    cache.set(key, value)
  }

  private color(hex: string, alpha = 1): Float32Array {
    let base = this.colors.get(hex)
    if (!base) {
      base = this.ck.parseColorString(hex)
      this.colors.set(hex, base)
    }
    return alpha === 1 ? base : this.ck.Color4f(base[0], base[1], base[2], alpha)
  }
}
