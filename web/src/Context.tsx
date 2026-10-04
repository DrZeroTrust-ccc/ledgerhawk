// Outside context (news, DOJ, federal courts, SEC, OFAC) for one vendor, subject or person.
// Each hit says how well it matches what we know about the subject, and an analyst can confirm it or rule it out.
import { useState } from 'react'
import { useAnalystName } from './App'
import { api } from './api'
import type { ContextItem, ContextTally, OutsideContext } from './api'

const TAG_STYLE: Record<string, string> = {
  criminal: 'bg-crimson-50 text-crimson',
  'civil enforcement': 'bg-crimson-50 text-crimson',
  procurement: 'bg-amber-50 text-amber-800',
  litigation: 'bg-violet-50 text-violet-800',
  sanctions: 'bg-crimson-50 text-crimson',
}

const MATCH: Record<string, { label: string; style: string }> = {
  strong: { label: 'Strong match', style: 'bg-emerald-50 text-emerald-800 ring-emerald-200' },
  possible: { label: 'Possible match', style: 'bg-amber-50 text-amber-800 ring-amber-200' },
  weak: { label: 'Name only', style: 'bg-slate-50 text-slate-500 ring-slate-200' },
}

const VERDICT: Record<string, { label: string; style: string }> = {
  same: { label: 'Confirmed same entity', style: 'bg-navy text-white ring-navy' },
  not: { label: 'Ruled out', style: 'bg-slate-200 text-slate-600 ring-slate-300' },
  unsure: { label: 'Unsure', style: 'bg-amber-100 text-amber-900 ring-amber-300' },
}

const RANK: Record<string, number> = { strong: 0, possible: 1, weak: 2 }

const verdictOf = (i: ContextItem) => i.verdict?.verdict ?? ''

/** Confirmed first, then strong, possible and name-only; enforcement language first in each, then newest. */
export function contextItems(c: OutsideContext): ContextItem[] {
  const band = (i: ContextItem) => (verdictOf(i) === 'not' ? 9 : verdictOf(i) === 'same' ? -1 : RANK[i.confidence] ?? 2)
  return Object.values(c.sources)
    .flatMap((s) => s.items)
    .sort(
      (a, b) =>
        band(a) - band(b) || Number(!a.tags.length) - Number(!b.tags.length) || (b.date || '').localeCompare(a.date || ''),
    )
}

function tally(c: OutsideContext): ContextTally {
  const t: ContextTally = { confirmed: 0, strong: 0, possible: 0, weak: 0, dismissed: 0, unsure: 0 }
  for (const i of Object.values(c.sources).flatMap((s) => s.items)) {
    const v = verdictOf(i)
    if (v === 'not') t.dismissed++
    else if (v === 'same') t.confirmed++
    else t[i.confidence]++
    if (v === 'unsure') t.unsure++
  }
  return t
}

function Item({ i, decide }: { i: ContextItem; decide: (i: ContextItem, verdict: string, note: string) => Promise<void> }) {
  const [analyst] = useAnalystName()
  const [why, setWhy] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const v = verdictOf(i)
  const m = MATCH[i.confidence] ?? MATCH.weak
  const go = async (verdict: string, note = '') => {
    setBusy(true)
    try {
      await decide(i, verdict, note)
      setWhy(null)
    } finally {
      setBusy(false)
    }
  }
  const btn = 'rounded px-1.5 py-0.5 text-[11px] ring-1 ring-slate-200 hover:bg-slate-50 disabled:opacity-50'
  return (
    <li className={`py-2 text-sm ${v === 'not' ? 'opacity-60' : ''}`}>
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
        <span
          className={`rounded px-1.5 py-0.5 text-[11px] font-medium ring-1 ${v ? VERDICT[v].style : m.style}`}
          title={i.why?.join('; ')}
        >
          {v ? VERDICT[v].label : m.label}
        </span>
        <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[11px] text-slate-600">{i.source}</span>
        {i.url ? (
          <a href={i.url} target="_blank" rel="noreferrer" className={`text-navy hover:underline ${v === 'not' ? 'line-through' : ''}`}>
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
      <div className="text-xs text-slate-500">
        {[i.where, i.date].filter(Boolean).join(' · ')}
        {i.why?.length > 0 && <span className="text-slate-400">{i.where || i.date ? ' · ' : ''}Why: {i.why.join('; ')}</span>}
      </div>
      {i.verdict && (
        <div className="text-xs text-slate-600">
          {VERDICT[v].label} by {i.verdict.by}, {i.verdict.at.slice(0, 10)}
          {i.verdict.note && <>: “{i.verdict.note}”</>}
        </div>
      )}
      <div className="mt-1 flex flex-wrap items-center gap-1">
        {v !== 'same' && (
          <button className={btn} disabled={busy || !analyst.trim()} onClick={() => go('same')} title="This item is about our subject">
            Same entity
          </button>
        )}
        {v !== 'not' && why === null && (
          <button className={btn} disabled={busy || !analyst.trim()} onClick={() => setWhy('')} title="This item is about someone else">
            Not our subject
          </button>
        )}
        {v !== 'unsure' && (
          <button className={btn} disabled={busy || !analyst.trim()} onClick={() => go('unsure')}>
            Unsure
          </button>
        )}
        {v && (
          <button className={btn} disabled={busy || !analyst.trim()} onClick={() => go('')}>
            Clear
          </button>
        )}
        {!analyst.trim() && <span className="text-[11px] text-slate-400">Enter your name in the header to record a decision.</span>}
      </div>
      {why !== null && (
        <form
          className="mt-1 flex flex-wrap items-center gap-1"
          onSubmit={(e) => {
            e.preventDefault()
            if (why.trim()) go('not', why)
          }}
        >
          <input
            autoFocus
            value={why}
            onChange={(e) => setWhy(e.target.value)}
            placeholder="Why not? e.g. different state, different industry"
            className="min-w-0 flex-1 rounded border border-slate-300 px-2 py-1 text-xs"
          />
          <button type="submit" className={btn} disabled={busy || !why.trim()}>
            Rule out
          </button>
          <button type="button" className={btn} onClick={() => setWhy(null)}>
            Cancel
          </button>
        </form>
      )}
    </li>
  )
}

export function ContextPanel({ c: initial, title = 'Outside context' }: { c: OutsideContext; title?: string }) {
  const [analyst] = useAnalystName()
  const [c, setC] = useState(initial)
  const [showWeak, setShowWeak] = useState(false)
  const [showOut, setShowOut] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const items = contextItems(c)
  const t = tally(c)
  const main = items.filter((i) => verdictOf(i) !== 'not' && (i.confidence !== 'weak' || verdictOf(i)))
  const weak = items.filter((i) => i.confidence === 'weak' && !verdictOf(i))
  const out = items.filter((i) => verdictOf(i) === 'not')
  const errors = Object.entries(c.sources).filter(([, s]) => s.error)

  // Record the call, then take the verdicts back from the server by item id. This panel keeps its own snapshot (a
  // screen's lookup can be older than the entity's latest), so only the verdicts are merged in.
  const decide = async (i: ContextItem, verdict: string, note: string) => {
    setError(null)
    const f = new FormData()
    f.append('analyst', analyst)
    f.append('item', i.id)
    f.append('verdict', verdict)
    f.append('note', note)
    f.append('name', c.name)
    f.append('uei', c.uei)
    if (c.person) f.append('person', 'true')
    try {
      const res = await api.contextVerdict(f)
      const got = new Map(Object.values(res.sources).flatMap((s) => s.items.map((x) => [x.id, x.verdict] as const)))
      setC({
        ...c,
        sources: Object.fromEntries(
          Object.entries(c.sources).map(([k, s]) => [
            k,
            { ...s, items: s.items.map((x) => (x.id === i.id ? { ...x, verdict: got.get(x.id) ?? null } : x)) },
          ]),
        ),
      })
    } catch (err) {
      setError((err as Error).message)
    }
  }

  const counts = [
    t.confirmed && `${t.confirmed} confirmed`,
    `${t.strong} strong`,
    `${t.possible} possible`,
    `${t.weak} name only`,
    t.dismissed && `${t.dismissed} ruled out`,
  ].filter(Boolean)

  return (
    <section>
      <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">
        {title} · {c.fetched_at.slice(0, 10)}
      </h3>
      <p className="text-xs text-slate-600">
        {c.count} items for “{c.query}”: {counts.join(', ')}.
        {c.generic && <span className="text-amber-700"> This is a common business name, so expect unrelated hits.</span>}
      </p>
      <p className="text-xs text-slate-500">
        Strong means the item also names something we know about this {c.person ? 'person' : 'company'} (UEI, CAGE, city, an officer or a
        related firm). Reports list only confirmed, strong and possible items, each labeled unverified until someone confirms it.
      </p>
      {error && <p className="mt-1 text-xs text-crimson">{error}</p>}
      {main.length > 0 ? (
        <ul className="mt-1 divide-y divide-slate-100">
          {main.map((i) => (
            <Item key={i.id} i={i} decide={decide} />
          ))}
        </ul>
      ) : (
        <p className="mt-1 text-xs text-slate-500">No item matched more than the name.</p>
      )}
      {weak.length > 0 && (
        <div className="mt-1">
          <button className="text-xs text-navy hover:underline" onClick={() => setShowWeak(!showWeak)}>
            {showWeak ? 'Hide' : 'Show'} {weak.length} name-only {weak.length === 1 ? 'hit' : 'hits'} (likely other {c.person ? 'people' : 'companies'})
          </button>
          {showWeak && (
            <ul className="divide-y divide-slate-100">
              {weak.map((i) => (
                <Item key={i.id} i={i} decide={decide} />
              ))}
            </ul>
          )}
        </div>
      )}
      {out.length > 0 && (
        <div className="mt-1">
          <button className="text-xs text-navy hover:underline" onClick={() => setShowOut(!showOut)}>
            {showOut ? 'Hide' : 'Show'} {out.length} ruled out
          </button>
          {showOut && (
            <ul className="divide-y divide-slate-100">
              {out.map((i) => (
                <Item key={i.id} i={i} decide={decide} />
              ))}
            </ul>
          )}
        </div>
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
