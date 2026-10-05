// Outside context (news, DOJ, federal courts, SEC, sanctions and exclusion lists, registries) for one vendor, subject or person.
// Each hit says how well it matches what we know about the subject, and an analyst can confirm it or rule it out.
import { useState } from 'react'
import { useAnalystName } from './App'
import { api } from './api'
import type { CheckFinding, ContextItem, ContextTally, OutsideContext } from './api'

const TAG_STYLE: Record<string, string> = {
  criminal: 'bg-crimson-50 text-crimson',
  'civil enforcement': 'bg-crimson-50 text-crimson',
  procurement: 'bg-amber-50 text-amber-800',
  registry: 'bg-amber-50 text-amber-800',
  pep: 'bg-violet-50 text-violet-800',
  watchlist: 'bg-amber-50 text-amber-800',
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

/** Confirmed first, then by match score; enforcement language first among equal scores, then newest. */
export function contextItems(c: OutsideContext): ContextItem[] {
  const band = (i: ContextItem) => (verdictOf(i) === 'not' ? 9 : verdictOf(i) === 'same' ? -1 : RANK[i.confidence] ?? 2)
  return Object.values(c.sources)
    .flatMap((s) => s.items)
    .sort(
      (a, b) =>
        band(a) - band(b) ||
        (b.score ?? 0) - (a.score ?? 0) ||
        Number(!a.tags.length) - Number(!b.tags.length) ||
        (b.date || '').localeCompare(a.date || ''),
    )
}

const hostOf = (i: ContextItem) => {
  try {
    return new URL(i.url).hostname.replace(/^www\./, '')
  } catch {
    return ''
  }
}

/** The score as a small meter: how much of what we know about the subject this item shows. */
function Score({ i }: { i: ContextItem }) {
  const s = i.score ?? 0
  const color = s >= 70 ? 'bg-emerald-600' : s >= 40 ? 'bg-amber-500' : 'bg-slate-400'
  return (
    <span className="inline-flex items-center gap-1" title={`Match score ${s}/100: ${i.why?.join('; ')}`}>
      <span className="relative inline-block h-1.5 w-10 overflow-hidden rounded bg-slate-200">
        <span className={`absolute inset-y-0 left-0 ${color}`} style={{ width: `${s}%` }} />
      </span>
      <span className="text-[11px] tabular-nums text-slate-600">{s}</span>
    </span>
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

function Item({
  i,
  decide,
  mute,
}: {
  i: ContextItem
  decide: (i: ContextItem, verdict: string, note: string) => Promise<void>
  mute: (host: string, on: boolean) => Promise<void>
}) {
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
          {v ? (i.verdict?.muted ? 'Site muted' : VERDICT[v].label) : m.label}
        </span>
        <Score i={i} />
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
      {i.query && <div className="text-[11px] text-slate-400">Found by searching {i.query}</div>}
      {i.verdict && (
        <div className="text-xs text-slate-600">
          {i.verdict.muted ? i.verdict.note : VERDICT[v].label} by {i.verdict.by}, {i.verdict.at.slice(0, 10)}
          {!i.verdict.muted && i.verdict.note && <>: “{i.verdict.note}”</>}
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
        {v && !i.verdict?.muted && (
          <button className={btn} disabled={busy || !analyst.trim()} onClick={() => go('')}>
            Clear
          </button>
        )}
        {hostOf(i) && (
          <button
            className={btn}
            disabled={busy || !analyst.trim()}
            title={i.verdict?.muted ? 'Show results from this site again' : 'Rule out every result from this site, in all lookups'}
            onClick={async () => {
              setBusy(true)
              try {
                await mute(hostOf(i), !i.verdict?.muted)
              } finally {
                setBusy(false)
              }
            }}
          >
            {i.verdict?.muted ? `Unmute ${hostOf(i)}` : `Mute ${hostOf(i)}`}
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

const LEAN_STYLE: Record<CheckFinding['lean'], string> = {
  strengthens: 'text-crimson',
  weakens: 'text-emerald-700',
  context: 'text-slate-500',
}

/** What the vendor's own SAM website and address show, checked directly (no name matching, so nothing to confirm). */
function EntityChecks({ c }: { c: OutsideContext }) {
  const w = c.checks?.website
  const a = c.checks?.address
  if (!w && !a) return null
  const rows: [string, CheckFinding[], string[]][] = []
  if (a) rows.push(['Address (USPS via Smarty)', a.findings, a.errors])
  if (w) rows.push([w.domain ? `Website ${w.domain}` : 'Website', w.findings, w.errors])
  return (
    <div className="mb-2 rounded-md bg-slate-50 p-2 text-xs">
      <div className="mb-1 font-medium text-slate-600">From the vendor's own SAM registration</div>
      <ul className="space-y-0.5">
        {rows.map(([label, findings, errors]) => (
          <li key={label}>
            <span className="font-medium">{label}:</span>{' '}
            {findings.length === 0 && errors.length === 0 && <span className="text-slate-500">nothing unusual</span>}
            {findings.map((f, n) => (
              <span key={n} className={LEAN_STYLE[f.lean]}>
                {n > 0 && '; '}
                {f.text}
              </span>
            ))}
            {errors.length > 0 && <span className="text-amber-700"> {errors.join('; ')}</span>}
            {w && label.startsWith('Website') && w.registered && !findings.some((f) => f.text.includes(w.registered)) && (
              <span className="text-slate-500">
                {' '}
                (registered {w.registered}
                {w.first_capture && `, archived ${w.first_capture} to ${w.last_capture}`})
              </span>
            )}
          </li>
        ))}
      </ul>
    </div>
  )
}

export function ContextPanel({ c: initial, title = 'Outside context', runId }: { c: OutsideContext; title?: string; runId?: string }) {
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
  const merge = (ids: Set<string>, got: Map<string, ContextItem['verdict']>) =>
    setC((c) => ({
      ...c,
      sources: Object.fromEntries(
        Object.entries(c.sources).map(([k, s]) => [
          k,
          { ...s, items: s.items.map((x) => (ids.has(x.id) ? { ...x, verdict: got.get(x.id) ?? null } : x)) },
        ]),
      ),
    }))

  const record = async (ids: string[], verdict: string, note: string) => {
    setError(null)
    const f = new FormData()
    f.append('analyst', analyst)
    f.append('item', ids.join(','))
    f.append('verdict', verdict)
    f.append('note', note)
    f.append('name', c.name)
    f.append('uei', c.uei)
    if (runId) f.append('run_id', runId)
    if (c.person) f.append('person', 'true')
    try {
      const res = await api.contextVerdict(f)
      merge(new Set(ids), new Map(Object.values(res.sources).flatMap((s) => s.items.map((x) => [x.id, x.verdict] as const))))
    } catch (err) {
      setError((err as Error).message)
    }
  }
  const decide = (i: ContextItem, verdict: string, note: string) => record([i.id], verdict, note)

  // Muting is site-wide: mark this panel's items from the site the way the server will on the next read.
  const mute = async (host: string, on: boolean) => {
    setError(null)
    let note = ''
    if (on) {
      const n = window.prompt(`Mute ${host}? Its results will count as ruled out in every lookup until someone unmutes it. Reason (optional):`, 'Directory or junk site')
      if (n === null) return
      note = n
    }
    const f = new FormData()
    f.append('analyst', analyst)
    f.append('host', host)
    f.append('note', note)
    f.append('mute', String(on))
    try {
      const res = await api.muteSite(f)
      const m = res.sites[host]
      setC((c) => ({
        ...c,
        sources: Object.fromEntries(
          Object.entries(c.sources).map(([k, s]) => [
            k,
            {
              ...s,
              items: s.items.map((x) => {
                if (hostOf(x) !== host) return x
                if (on && !x.verdict)
                  return { ...x, verdict: { verdict: 'not' as const, note: `Site ${host} muted${m.note ? `: ${m.note}` : ''}`, by: m.by, at: m.at, muted: true } }
                if (!on && x.verdict?.muted) return { ...x, verdict: null }
                return x
              }),
            },
          ]),
        ),
      }))
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
        Each result has a match score from 0 to 100: points for the name, the UEI or CAGE code, the city or state, an officer or related firm,
        and the kind of site. 70 or more is a strong match, 40 to 69 possible, under 40 probably another {c.person ? 'person' : 'company'}.
        Hover a score to see how it adds up. Reports list only confirmed, strong and possible results, each labeled unverified until
        someone confirms it.
      </p>
      {error && <p className="mt-1 text-xs text-crimson">{error}</p>}
      <EntityChecks c={c} />
      {main.length > 0 ? (
        <ul className="mt-1 divide-y divide-slate-100">
          {main.map((i) => (
            <Item key={i.id} i={i} decide={decide} mute={mute} />
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
          <button
            className="ml-3 text-xs text-navy hover:underline disabled:text-slate-400"
            disabled={!analyst.trim()}
            title="Rule out every name-only result in this lookup at once"
            onClick={() => record(weak.map((i) => i.id), 'not', 'Name-only match, ruled out in bulk')}
          >
            Rule out all {weak.length}
          </button>
          {showWeak && (
            <ul className="divide-y divide-slate-100">
              {weak.map((i) => (
                <Item key={i.id} i={i} decide={decide} mute={mute} />
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
                <Item key={i.id} i={i} decide={decide} mute={mute} />
              ))}
            </ul>
          )}
        </div>
      )}
      {errors.length > 0 && <p className="mt-1 text-xs text-amber-700">Not checked: {errors.map(([, s]) => s.error).join('; ')}.</p>}
      {c.keys && !c.person && (!c.keys.opensanctions || !c.keys.smarty) && (
        <p className="mt-1 text-xs text-slate-500">
          Off until an administrator adds a key on the server:{' '}
          {[
            !c.keys.opensanctions && 'OpenSanctions (OPENSANCTIONS_API_KEY)',
            !c.keys.smarty && 'USPS address check (SMARTY_AUTH_ID and SMARTY_AUTH_TOKEN)',
          ]
            .filter(Boolean)
            .join(', ')}
          .
        </p>
      )}
      {c.web_search === false && (
        <p className="mt-1 text-xs text-slate-500">Web search (Brave) is off. An administrator turns it on by setting BRAVE_API_KEY on the server.</p>
      )}
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
