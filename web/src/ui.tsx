import { useEffect, useState, type ReactNode } from 'react'
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
  const context = s.id === 'S6'
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
        <span className="font-semibold">{s.id} · {s.label}</span>
        <br />
        {s.detail}
      </span>
    </span>
  )
}

export function FlagChip({ flag }: { flag: string }) {
  const strong = flag === 'EXCLUDED' || flag === 'ALIAS_MATCH'
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
    queue === 'priority' ? 'bg-crimson text-white' : queue === 'exclusion' ? 'bg-crimson-50 text-crimson ring-1 ring-crimson/30' : queue === 'strong' ? 'bg-navy text-white' : 'bg-slate-100 text-slate-600'
  return <span className={`whitespace-nowrap rounded px-2 py-0.5 text-xs font-medium ${color}`}>{QUEUE_LABEL[queue] ?? queue}</span>
}

export function DataClassBadge({ dataClass }: { dataClass: string }) {
  if (dataClass !== 'synthetic') return null
  return <span className="rounded bg-violet-100 px-2 py-0.5 text-xs font-semibold uppercase tracking-wide text-violet-800">Synthetic data</span>
}

export function Button({ children, variant = 'primary', ...rest }: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: 'primary' | 'secondary' | 'ghost' }) {
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
