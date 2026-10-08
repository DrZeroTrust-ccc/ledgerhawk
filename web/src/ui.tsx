import { useEffect, useRef, useState, type ReactNode } from 'react'
import { FLAG_LABEL, QUEUE_LABEL, type Signal } from './api'

export function Card({ title, action, children, className = '' }: { title?: ReactNode; action?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`rounded-lg border border-slate-200 bg-white ${className}`}>
      {title && (
        <header className="flex items-center justify-between gap-4 border-b border-slate-100 px-5 py-3">
          <h2 className="text-sm font-semibold text-navy">{title}</h2>
          {action}
        </header>
      )}
      <div className="p-5">{children}</div>
    </section>
  )
}

export function Stat({ label, value, sub }: { label: string; value: ReactNode; sub?: ReactNode }) {
  return (
    <div>
      <div className="text-xs font-medium text-slate-500">{label}</div>
      <div className="tabular mt-1 text-2xl font-semibold text-ink">{value}</div>
      {sub && <div className="mt-0.5 text-xs text-slate-500">{sub}</div>}
    </div>
  )
}

export function SignalChip({ s }: { s: Signal }) {
  const context = s.id === 'S6' || s.id === 'R_split'
  return (
    <span className="group relative inline-flex">
      <span
        className={`cursor-help rounded px-1.5 py-0.5 font-mono text-[11px] font-semibold ${
          context ? 'bg-slate-100 text-slate-500' : 'bg-navy-50 text-navy ring-1 ring-navy-100'
        }`}
        tabIndex={0}
        aria-label={`${s.id} ${s.label}: ${s.detail}`}
      >
        {s.id}
      </span>
      <span className="pointer-events-none absolute bottom-full left-0 z-20 mb-1.5 hidden w-80 rounded-md bg-ink px-3 py-2 text-xs leading-snug text-white shadow-lg group-hover:block group-focus-within:block">
        <span className="font-semibold">
          {s.id} · {s.label}
        </span>
        <br />
        {s.detail}
      </span>
    </span>
  )
}

export function FlagChip({ flag }: { flag: string }) {
  const strong = flag === 'EXCLUDED' || flag === 'ALIAS_MATCH' || flag === 'R_EXPOC' || flag === 'R_EXADDR'
  const weak = flag === 'NAME_MATCH_CANDIDATE'
  return (
    <span
      className={`rounded px-1.5 py-0.5 text-[11px] font-medium ${
        strong ? 'bg-crimson-50 text-crimson ring-1 ring-crimson/30' : weak ? 'bg-slate-100 text-slate-500' : 'bg-amber-50 text-amber-800 ring-1 ring-amber-200'
      }`}
    >
      {FLAG_LABEL[flag] ?? flag}
    </span>
  )
}

export function QueueChip({ queue }: { queue: string }) {
  if (!queue) return null
  const color =
    queue === 'priority'
      ? 'bg-crimson text-white'
      : queue === 'exclusion'
        ? 'bg-crimson-50 text-crimson ring-1 ring-crimson/30'
        : queue === 'strong'
          ? 'bg-navy text-white'
          : queue === 'relationship'
            ? 'bg-navy-50 text-navy ring-1 ring-navy'
            : queue === 'integrity'
              ? 'bg-amber-50 text-amber-800 ring-1 ring-amber-300'
              : 'bg-slate-100 text-slate-600'
  return <span className={`whitespace-nowrap rounded px-2 py-0.5 text-xs font-medium ${color}`}>{QUEUE_LABEL[queue] ?? queue}</span>
}

export const TIER_SHORT: Record<string, string> = {
  '1': 'Tier 1 · Elevated',
  '2': 'Tier 2 · Moderate',
  '3': 'Tier 3 · Exclusion',
  '4': 'Tier 4 · Data anomaly',
  '5': 'Tier 5 · Not reviewed',
  explained: 'Explained',
}

const COLOR_STYLE: Record<string, string> = {
  red: 'bg-crimson text-white',
  yellow: 'bg-amber-200 text-amber-950',
  green: 'bg-emerald-100 text-emerald-900',
}

/** Red, yellow or green: the same rules as the export for analysis (exports/analysis.py). */
export function ColorChip({ color, why }: { color?: string; why?: string[] }) {
  if (!color) return null
  return (
    <span title={why?.join('\n')} className={`inline-block rounded px-1.5 py-0.5 text-xs font-semibold capitalize ${COLOR_STYLE[color] ?? ''}`}>
      {color}
    </span>
  )
}

export function TierChip({ tier, changed }: { tier: string; changed?: boolean }) {
  if (!tier) return null
  const color =
    tier === '1'
      ? 'bg-crimson text-white'
      : tier === '2'
        ? 'bg-amber-100 text-amber-900'
        : tier === '3'
          ? 'bg-crimson-50 text-crimson ring-1 ring-crimson/30'
          : tier === '4'
            ? 'bg-violet-100 text-violet-800'
            : tier === 'explained'
              ? 'bg-slate-100 text-slate-500'
              : 'bg-navy-50 text-navy ring-1 ring-navy-100'
  return (
    <span className={`whitespace-nowrap rounded px-2 py-0.5 text-xs font-medium ${color}`} title={changed ? 'Set by an analyst' : 'Pipeline default'}>
      {TIER_SHORT[tier] ?? tier}
      {changed && ' ✎'}
    </span>
  )
}

export function DataClassBadge({ dataClass }: { dataClass: string }) {
  if (dataClass !== 'synthetic') return null
  return <span className="rounded bg-violet-100 px-2 py-0.5 text-xs font-semibold uppercase tracking-wide text-violet-800">Synthetic data</span>
}

export function Button({
  children,
  variant = 'primary',
  ...rest
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: 'primary' | 'secondary' | 'ghost' }) {
  const styles = {
    primary: 'bg-navy text-white hover:bg-ink disabled:bg-slate-300',
    secondary: 'bg-white text-navy ring-1 ring-slate-300 hover:bg-slate-50 disabled:text-slate-400',
    ghost: 'text-navy hover:bg-navy-50',
  }[variant]
  return (
    <button {...rest} className={`rounded-md px-3 py-1.5 text-sm font-medium transition-colors disabled:cursor-not-allowed ${styles} ${rest.className ?? ''}`}>
      {children}
    </button>
  )
}

// One menu for a page's exports, instead of a row of equal-weight download buttons.
export function DownloadMenu({ items, label = 'Download' }: { items: { label: string; href: string; hint?: string }[]; label?: string }) {
  const ref = useRef<HTMLDetailsElement>(null)
  useEffect(() => {
    const close = (e: MouseEvent | KeyboardEvent) => {
      const d = ref.current
      if (!d?.open) return
      if (e instanceof KeyboardEvent ? e.key === 'Escape' : !d.contains(e.target as Node)) d.open = false
    }
    document.addEventListener('mousedown', close)
    document.addEventListener('keydown', close)
    return () => {
      document.removeEventListener('mousedown', close)
      document.removeEventListener('keydown', close)
    }
  }, [])
  return (
    <details ref={ref} className="relative">
      <summary className="flex cursor-pointer list-none items-center gap-1.5 rounded-md bg-white px-3 py-1.5 text-sm font-medium text-navy ring-1 ring-slate-300 hover:bg-slate-50 [&::-webkit-details-marker]:hidden">
        {label}
        <span aria-hidden className="text-xs">▾</span>
      </summary>
      <ul className="absolute right-0 z-20 mt-1 w-72 overflow-hidden rounded-md bg-white py-1 text-sm shadow-lg ring-1 ring-slate-200">
        {items.map((i) => (
          <li key={i.href}>
            <a href={i.href} className="block px-3 py-2 text-ink hover:bg-slate-50" onClick={() => ref.current?.removeAttribute('open')}>
              <span className="font-medium">{i.label}</span>
              {i.hint && <span className="block text-xs text-slate-500">{i.hint}</span>}
            </a>
          </li>
        ))}
      </ul>
    </details>
  )
}

export function ErrorNote({ error }: { error: string | null }) {
  if (!error) return null
  return <div className="rounded-md bg-crimson-50 px-3 py-2 text-sm text-crimson">{error}</div>
}

export function Loading() {
  return <div className="py-10 text-center text-sm text-slate-500">Loading…</div>
}

export function useAsync<T>(fn: () => Promise<T>, deps: unknown[]): { data: T | null; error: string | null; reload: () => void } {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [n, setN] = useState(0)
  useEffect(() => {
    let live = true
    setError(null)
    fn()
      .then((d) => live && setData(d))
      .catch((e: Error) => live && setError(e.message))
    return () => {
      live = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, n])
  return { data, error, reload: () => setN((x) => x + 1) }
}

const ANALYST_KEY = 'ledgerhawk.analyst'

export function useAnalyst(): [string, (v: string) => void] {
  const [name, setName] = useState(() => {
    try {
      return localStorage.getItem(ANALYST_KEY) ?? ''
    } catch {
      return ''
    }
  })
  const set = (v: string) => {
    setName(v)
    try {
      localStorage.setItem(ANALYST_KEY, v)
    } catch {
      /* storage unavailable */
    }
  }
  return [name, set]
}

/** The lead's one-line reason: the Hawk's (AI) when written, otherwise the rule-based headline. */
export function LeadLine({ hawk, headline, className = '' }: { hawk?: string; headline?: string; className?: string }) {
  if (hawk)
    return (
      <div className={className} title={headline ? `Rule summary: ${headline}` : undefined}>
        <span className="mr-1 rounded bg-violet-100 px-1 py-px text-[10px] font-semibold uppercase tracking-wide text-violet-800">Hawk (AI)</span>
        {hawk}
      </div>
    )
  return headline ? <div className={className}>{headline}</div> : null
}
