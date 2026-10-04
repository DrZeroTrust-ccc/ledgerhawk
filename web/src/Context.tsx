// Outside context (news, DOJ, federal courts, SEC, OFAC) for one vendor, subject or person.
import { useState } from 'react'
import type { ContextItem, OutsideContext } from './api'

const TAG_STYLE: Record<string, string> = {
  criminal: 'bg-crimson-50 text-crimson',
  'civil enforcement': 'bg-crimson-50 text-crimson',
  procurement: 'bg-amber-50 text-amber-800',
  litigation: 'bg-violet-50 text-violet-800',
  sanctions: 'bg-crimson-50 text-crimson',
}

export function contextItems(c: OutsideContext): ContextItem[] {
  const items = Object.values(c.sources).flatMap((s) => s.items)
  return items.sort((a, b) => Number(!a.tags.length) - Number(!b.tags.length) || (b.date || '').localeCompare(a.date || ''))
}

function Item({ i }: { i: ContextItem }) {
  return (
    <li className="py-1.5 text-sm">
      <div className="flex flex-wrap items-baseline gap-x-2">
        <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[11px] text-slate-600">{i.source}</span>
        {i.url ? (
          <a href={i.url} target="_blank" rel="noreferrer" className="text-navy hover:underline">
            {i.title || i.url}
          </a>
        ) : (
          <span>{i.title}</span>
        )}
        {i.tags.map((t) => (
          <span key={t} className={`rounded px-1.5 py-0.5 text-[11px] font-medium ${TAG_STYLE[t] ?? 'bg-slate-100'}`}>
            {t}
          </span>
        ))}
      </div>
      <div className="text-xs text-slate-500">{[i.where, i.date].filter(Boolean).join(' · ')}</div>
    </li>
  )
}

export function ContextPanel({ c, title = 'Outside context' }: { c: OutsideContext; title?: string }) {
  const [all, setAll] = useState(false)
  const items = contextItems(c)
  const shown = all ? items : items.slice(0, 6)
  const errors = Object.entries(c.sources).filter(([, s]) => s.error)
  return (
    <section>
      <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">
        {title} · {c.fetched_at.slice(0, 10)}
      </h3>
      <p className="text-xs text-slate-600">
        {c.count} items for “{c.query}”
        {c.adverse > 0 && <span className="font-medium text-crimson"> · {c.adverse} with enforcement or litigation language</span>}.
        Matched by name only; confirm each one is this {c.person ? 'person' : 'company'}.
      </p>
      {shown.length > 0 && (
        <ul className="mt-1 divide-y divide-slate-100">
          {shown.map((i, n) => (
            <Item key={n} i={i} />
          ))}
        </ul>
      )}
      {items.length > 6 && (
        <button className="text-xs text-navy hover:underline" onClick={() => setAll(!all)}>
          {all ? 'Show fewer' : `Show all ${items.length}`}
        </button>
      )}
      {errors.length > 0 && <p className="mt-1 text-xs text-amber-700">Not checked: {errors.map(([, s]) => s.error).join('; ')}.</p>}
      <p className="mt-2 text-xs text-slate-500">
        Check by hand:{' '}
        {c.manual.map((m, n) => (
          <span key={m.label}>
            {n > 0 && ' · '}
            <a href={m.url} target="_blank" rel="noreferrer" className="text-navy hover:underline">
              {m.label}
            </a>
          </span>
        ))}
      </p>
    </section>
  )
}
