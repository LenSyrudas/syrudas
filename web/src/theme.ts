/**
 * Theming: two independent axes so a colorblind user can still pick light or
 * dark. Appearance drives `data-theme`; color-vision drives `data-cvd`. Both
 * are applied as attributes on <html> and read by CSS variable overrides.
 */

export type Appearance = 'system' | 'light' | 'dark'
export type ColorVision =
  | 'default'
  | 'protanopia'
  | 'deuteranopia'
  | 'tritanopia'
  | 'achromatopsia'

const APPEARANCE_KEY = 'syrudas.appearance'
const CVD_KEY = 'syrudas.colorVision'

const APPEARANCES: Appearance[] = ['system', 'light', 'dark']
const COLOR_VISIONS: ColorVision[] = [
  'default',
  'protanopia',
  'deuteranopia',
  'tritanopia',
  'achromatopsia',
]

const media = () =>
  typeof window !== 'undefined' && window.matchMedia
    ? window.matchMedia('(prefers-color-scheme: light)')
    : null

export function getAppearance(): Appearance {
  const v = localStorage.getItem(APPEARANCE_KEY) as Appearance | null
  return v && APPEARANCES.includes(v) ? v : 'system'
}

export function getColorVision(): ColorVision {
  const v = localStorage.getItem(CVD_KEY) as ColorVision | null
  return v && COLOR_VISIONS.includes(v) ? v : 'default'
}

function resolvedTheme(appearance: Appearance): 'light' | 'dark' {
  if (appearance === 'system') return media()?.matches ? 'light' : 'dark'
  return appearance
}

/** Fired after the theme changes so all controls (sidebar toggle, Settings
 *  selects) can re-sync from a single source of truth. */
export const THEME_EVENT = 'syrudas:themechange'

/** Apply the current stored preferences to the document root. */
export function applyTheme(): void {
  const root = document.documentElement
  root.setAttribute('data-theme', resolvedTheme(getAppearance()))
  const cvd = getColorVision()
  if (cvd === 'default') root.removeAttribute('data-cvd')
  else root.setAttribute('data-cvd', cvd)
  window.dispatchEvent(new Event(THEME_EVENT))
}

export function setAppearance(a: Appearance): void {
  localStorage.setItem(APPEARANCE_KEY, a)
  applyTheme()
}

export function setColorVision(c: ColorVision): void {
  localStorage.setItem(CVD_KEY, c)
  applyTheme()
}

/** Call once at startup: apply prefs and keep 'system' in sync with the OS. */
export function initTheme(): void {
  applyTheme()
  media()?.addEventListener('change', () => {
    if (getAppearance() === 'system') applyTheme()
  })
}
