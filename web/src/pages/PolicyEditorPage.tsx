import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, money, num, type PolicyEstimate } from '../api'
import { useAnalystName } from '../App'
import { Breadcrumbs, usePlace } from '../nav'
import { formatSetting, SETTINGS, settingLabel } from '../policyLabels'
import { parts, SENTENCES } from '../policySentences'
import { Button, ErrorNote, Loading, useAsync } from '../ui'

type Rules = Record<string, unknown>

// What a list setting is called inside a sentence: "the major-contractor list (303 names)".
const LIST_NOUN: Record<string, [string, string]> = {
  majors: ['major-contractor list', 'names'],
  noncommercial_structs: ['these', 'entity types'],
  jv_patterns: ['', 'name patterns'],
  tribal_patterns: ['', 'name patterns'],
  qio_patterns: ['', 'name patterns'],
  dialysis_patterns: ['', 'name patterns'],
  foreign_suffixes: ['', 'suffixes'],
  foreign_words: ['', 'words'],
  s4_psc_prefixes: ['', 'PSC prefixes'],
  s5_naics2: ['these', 'NAICS sectors'],
  s5_psc_prefixes: ['these', 'PSC prefixes'],
  stem_stop_words: ['these', 'common words'],
}

function chipText(key: string, v: unknown): string {
  if (Array.isArray(v)) {
    const [pre, noun] = LIST_NOUN[key] ?? ['', 'entries']
    return pre && pre !== 'these' ? `${pre} (${v.length} ${noun})` : `${pre ? pre + ' ' : ''}${v.length} ${noun}`
  }
  return formatSetting(key, v)
}

/** "$5M", "5,000,000", "250k", "2.5m" → dollars; null when it isn't a number. */
function parseMoney(s: string): number | null {
  const m = s.replace(/[$,\s]/g, '').match(/^(\d*\.?\d+)([kmb])?$/i)
  if (!m) return null
  const mult = { k: 1e3, m: 1e6, b: 1e9 }[(m[2] ?? '').toLowerCase() as 'k' | 'm' | 'b'] ?? 1
  return Math.round(parseFloat(m[1]) * mult)
}

const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b)

function SettingEditor({
  k,
  value,
  live,
  pid,
  rules,
  onChange,
}: {
  k: string
  value: unknown
  live: unknown
  pid: string
  rules: Rules
  onChange: (v: unknown) => void
}) {
  const kind = SETTINGS[k]?.kind
  const [text, setText] = useState(() =>
    Array.isArray(value) ? (value as string[]).join('\n') : kind === 'ratio' ? String(Math.round((value as number) * 100)) : String(value ?? ''),
  )
  const [bad, setBad] = useState(false)
  const full = SENTENCES.some((g) => g.full && g.sentences.some((s) => s.text.includes(`{${k}}`)))
  const chartable = !full && (kind === 'money' || k.endsWith('s3_ratio'))
  const cur = value as number
  // Around the live value, so the bars stay put while you try values; the one you pick turns gold.
  const mid = live as number
  const candidates = chartable
    ? [
        ...new Set([mid / 4, mid / 2, mid, mid * 2, mid * 4].map((x) => (kind === 'money' ? Math.round(x / 1000) * 1000 : Math.round(x * 10) / 10))),
      ].filter((x) => x > 0)
    : []
  const others = JSON.stringify({ ...rules, [k]: null })
  const sens = useAsync(() => (chartable ? api.policySensitivity(pid, { rules, key: k, values: candidates }) : Promise.resolve(null)), [k, others])
  const apply = (raw: string) => {
    setText(raw)
    let v: unknown = null
    if (kind === 'list')
      v = raw
        .split('\n')
        .map((x) => x.trim())
        .filter(Boolean)
    else if (kind === 'money') v = parseMoney(raw)
    else if (kind === 'ratio') v = raw.trim() === '' || isNaN(+raw) ? null : Math.min(100, Math.max(0, +raw)) / 100
    else if (kind === 'date') v = /^\d{4}-\d{2}-\d{2}$/.test(raw) ? raw : null
    else v = raw.trim() === '' || isNaN(+raw) || +raw < 0 ? null : +raw
    setBad(v === null)
    if (v !== null) onChange(v)
  }
  const max = Math.max(1, ...(sens.data?.points ?? []).map((p) => p.leads))
  return (
    <div className="mt-3 space-y-3 rounded-lg border border-slate-200 bg-slate-50 p-4">
      <label className="block space-y-1 text-sm">
        <span className="font-medium text-ink">{settingLabel(k)}</span>
        {kind === 'list' ? (
          <textarea
            value={text}
            onChange={(e) => apply(e.target.value)}
            rows={8}
            className="block w-full rounded-md border border-slate-300 px-2 py-1.5 font-mono text-xs"
          />
        ) : (
          <span className="flex items-center gap-2">
            <input
              value={text}
              onChange={(e) => apply(e.target.value)}
              type={kind === 'date' ? 'date' : 'text'}
              inputMode={kind === 'money' || kind === 'date' ? undefined : 'decimal'}
              className={`w-40 rounded-md border px-2 py-1.5 ${bad ? 'border-crimson' : 'border-slate-300'}`}
            />
            <span className="text-slate-500">{kind === 'ratio' ? '%' : kind === 'days' ? 'days' : kind === 'money' ? 'e.g. 5M or 250k' : ''}</span>
          </span>
        )}
      </label>
      {kind === 'list' && <p className="text-xs text-slate-500">One per line. Name patterns match whole words in the cleaned, upper-case name.</p>}
      {bad && <p className="text-xs text-crimson">That isn’t a valid value yet; the draft keeps the last good one.</p>}
      {!same(value, live) && (
        <p className="text-xs text-slate-600">
          Live: {formatSetting(k, live)}.{' '}
          <button type="button" className="text-navy underline" onClick={() => onChange(live)}>
            Put it back
          </button>
        </p>
      )}
      {chartable && sens.data && (
        <div>
          <div className="text-xs font-medium text-slate-600">Leads in the latest import at each value</div>
          <div className="mt-2 flex h-28 items-end gap-3 border-b border-slate-300 px-1">
            {sens.data.points.map((p) => (
              <div key={p.value} className="flex flex-1 flex-col items-center gap-1">
                <span className="tabular text-xs text-slate-600">{num(p.leads)}</span>
                <div
                  className={`w-full max-w-14 rounded-t ${p.value === cur ? 'bg-amber-400' : 'bg-navy/60'}`}
                  style={{ height: Math.max(3, (p.leads / max) * 80) }}
                />
              </div>
            ))}
          </div>
          <div className="mt-1 flex gap-3 px-1">
            {sens.data.points.map((p) => (
              <button
                key={p.value}
                type="button"
                onClick={() => {
                  onChange(p.value)
                  setText(kind === 'money' ? formatSetting(k, p.value) : String(p.value))
                }}
                className={`min-h-9 flex-1 rounded-md border text-xs ${p.value === cur ? 'border-navy bg-navy font-semibold text-white' : 'border-slate-300 bg-white hover:bg-slate-50'}`}
              >
                {formatSetting(k, p.value)}
              </button>
            ))}
          </div>
        </div>
      )}
      {chartable && !sens.data && !sens.error && <p className="text-xs text-slate-500">Working out how many leads each value gives…</p>}
    </div>
  )
}

function Impact({ est, loading }: { est: PolicyEstimate | null; loading: boolean }) {
  if (!est) return <p className="text-sm text-slate-500">{loading ? 'Working out the impact…' : 'Change a highlighted word to see the impact.'}</p>
  const w = est.workload
  const hours = (n: number) => Math.round(n * w.hours_per_lead)
  const dl = est.draft.leads - est.live.leads
  const dd = est.draft.dollars - est.live.dollars
  const sign = (x: number, f: (v: number) => string) => (x > 0 ? `+${f(x)}` : x < 0 ? `−${f(-x)}` : 'no change')
  const tone = (x: number) => (x > 0 ? 'text-crimson' : x < 0 ? 'text-emerald-700' : 'text-slate-500')
  const ins = est.moves.filter((m) => m.kind === 'in')
  const outs = est.moves.filter((m) => m.kind === 'out')
  const tile = 'rounded-md bg-slate-50 p-3'
  return (
    <div className={`space-y-4 ${loading ? 'opacity-60' : ''}`}>
      <p className="text-xs text-slate-500">
        Quick estimate on {est.import.label} ({est.import.created_at.slice(0, 10)}), against the live version.
      </p>
      <div className="grid grid-cols-2 gap-2">
        <div className={tile}>
          <div className="text-xs text-slate-500">Leads in the queue</div>
          <div className="tabular text-xl font-semibold">{num(est.draft.leads)}</div>
          <div className={`text-xs font-medium ${tone(dl)}`}>{sign(dl, (v) => num(v))}</div>
        </div>
        <div className={tile}>
          <div className="text-xs text-slate-500">Dollars under review</div>
          <div className="tabular text-xl font-semibold">{money(est.draft.dollars)}</div>
          <div className={`text-xs font-medium ${tone(dd)}`}>{sign(dd, (v) => money(v))}</div>
        </div>
        <div className={tile}>
          <div className="text-xs text-slate-500">Review hours</div>
          <div className="tabular text-xl font-semibold">{num(hours(est.draft.leads))}</div>
          <div className={`text-xs font-medium ${tone(dl)}`}>{sign(hours(dl), (v) => `${num(v)} h`)}</div>
        </div>
        <div className={tile}>
          <div className="text-xs text-slate-500">Per analyst ({w.analysts})</div>
          <div className="tabular text-xl font-semibold">{num(Math.round(est.draft.leads / w.analysts))} leads</div>
          <div className="text-xs text-slate-500">
            about {((est.draft.leads * w.hours_per_lead) / w.analysts / 40).toFixed(1)} weeks each (was{' '}
            {((est.live.leads * w.hours_per_lead) / w.analysts / 40).toFixed(1)})
          </div>
        </div>
      </div>
      <p className="-mt-2 text-xs text-slate-500">
        Assumes {w.hours_per_lead} review hours per lead and {w.analysts} {w.analysts === 1 ? 'analyst' : 'analysts'} at 40 hours a week
        {w.set ? '' : ' (not set for this pack yet)'}.
      </p>
      {est.conflicts.length > 0 && (
        <div className="rounded-md border border-crimson/30 bg-crimson-50 p-3 text-sm">
          <strong className="text-crimson">Affects work already done.</strong> {est.conflicts.length}{' '}
          {est.conflicts.length === 1 ? 'lead someone decided' : 'leads someone decided'} would no longer be flagged in new imports:{' '}
          {est.conflicts
            .slice(0, 4)
            .map((c) => `${c.name} (${c.decision}, ${c.decided_by})`)
            .join('; ')}
          {est.conflicts.length > 4 ? '…' : ''}. Their decisions stay on record.
        </div>
      )}
      {est.must_catch.length > 0 && (
        <div>
          <h3 className="text-xs font-semibold tracking-wide text-slate-500 uppercase">Must-catch vendors</h3>
          <ul className="mt-1 space-y-1 text-sm">
            {est.must_catch.map((m) => (
              <li key={m.uei} className="flex gap-2">
                <span
                  className={
                    m.status === 'dropped' ? 'font-bold text-crimson' : m.status === 'kept' ? 'font-bold text-emerald-700' : 'text-slate-400'
                  }
                >
                  {m.status === 'dropped' ? '✕' : m.status === 'kept' ? '✓' : '–'}
                </span>
                <span>
                  <strong>{m.name || m.uei}</strong>{' '}
                  <span className="text-slate-500">
                    {m.status === 'dropped' ? 'would be dropped' : m.status === 'kept' ? 'still flagged' : 'not in this import'}
                  </span>
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {(ins.length > 0 || outs.length > 0) && (
        <div className="space-y-2 text-sm">
          <h3 className="text-xs font-semibold tracking-wide text-slate-500 uppercase">
            {est.moves_total} {est.moves_total === 1 ? 'vendor moves' : 'vendors move'}
          </h3>
          {[
            ['Coming in', ins],
            ['Dropping out', outs],
          ].map(([label, list]) =>
            (list as typeof ins).length ? (
              <details key={label as string}>
                <summary className="cursor-pointer text-slate-700">
                  {label as string} ({(list as typeof ins).length}
                  {est.moves_total > est.moves.length ? '+' : ''})
                </summary>
                <ul className="mt-1 space-y-1.5 pl-4">
                  {(list as typeof ins).slice(0, 12).map((m) => (
                    <li key={m.uei}>
                      <span className="font-medium">{m.name}</span> <span className="tabular text-xs text-slate-500">{money(m.tot)}</span>
                      <div className="text-xs text-slate-600">{m.because}</div>
                    </li>
                  ))}
                </ul>
              </details>
            ) : null,
          )}
        </div>
      )}
      {est.unestimated.length > 0 && (
        <p className="rounded-md bg-amber-50 p-3 text-xs text-amber-900">
          Not in this estimate: {est.unestimated.map(settingLabel).join('; ')}. These depend on the SAM and exclusion matching, which only the full
          preview re-runs.
        </p>
      )}
    </div>
  )
}

export default function PolicyEditorPage() {
  const { id = '' } = useParams()
  const nav = useNavigate()
  const [analyst] = useAnalystName()
  const { data: p, error } = useAsync(() => api.policy(id), [id])
  usePlace(p ? `${p.name} (editing)` : null)
  const [rules, setRules] = useState<Rules | null>(null)
  const [open, setOpen] = useState('')
  const [reason, setReason] = useState('')
  const [est, setEst] = useState<PolicyEstimate | null>(null)
  const [estLoading, setEstLoading] = useState(false)
  const [estErr, setEstErr] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    if (p && !rules) {
      setRules(p.draft?.rules ?? p.live_rules)
      setReason(p.draft?.reason ?? '')
    }
  }, [p, rules])

  // Re-estimate a moment after the last change.
  const key = JSON.stringify(rules)
  useEffect(() => {
    if (!rules) return
    setEstLoading(true)
    const t = setTimeout(() => {
      api.policyEstimate(id, { rules }).then(
        (e) => {
          setEst(e)
          setEstErr(null)
          setEstLoading(false)
        },
        (e) => {
          setEstErr(String(e.message ?? e))
          setEstLoading(false)
        },
      )
    }, 600)
    return () => clearTimeout(t)
  }, [id, key])

  const changed = useMemo(() => (p && rules ? Object.keys(rules).filter((k) => !same(rules[k], p.live_rules[k])) : []), [p, rules])
  if (error) return <ErrorNote error={error} />
  if (!p || !rules) return <Loading />
  if (p.locked)
    return (
      <p className="text-sm text-slate-600">
        {p.name} is read-only. <Link to="/policies">Copy it to a new pack</Link> to change its rules.
      </p>
    )

  const save = async () => {
    setSaving(true)
    setErr(null)
    try {
      const v = await api.savePolicyDraft(id, { rules, reason, analyst })
      setMsg(
        `Draft v${v.n} saved with ${v.changes.length} ${v.changes.length === 1 ? 'change' : 'changes'}. Nothing changes for anyone until it's approved and deployed.`,
      )
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setSaving(false)
    }
  }
  const discard = async () => {
    if (!window.confirm('Discard this draft? The live version stays as it is.')) return
    const f = new FormData()
    f.append('analyst', analyst)
    try {
      await api.discardPolicyDraft(id, f)
      nav(`/policies/${id}`)
    } catch (e) {
      setErr((e as Error).message)
    }
  }
  const blocked = est?.must_catch.some((m) => m.status === 'dropped')

  return (
    <div className="space-y-6">
      <Breadcrumbs items={[{ label: 'Policies', to: '/policies' }, { label: p.name, to: `/policies/${id}` }, { label: 'Edit rules' }]} />
      <div>
        <h1 className="text-2xl font-semibold text-navy">Edit {p.name}</h1>
        <p className="mt-1 max-w-3xl text-sm text-slate-600">
          Every rule this pack follows, as a sentence. Click a highlighted value to change it; changed values turn gold. You’re editing a draft of v
          {p.draft?.n ?? (p.live ?? 0) + 1}: imports keep using v{p.live} until a draft is approved and deployed.
        </p>
      </div>
      <div className="flex flex-wrap items-start gap-6">
        <div className="min-w-0 flex-[999_1_560px] space-y-6">
          {SENTENCES.map((g) => (
            <section key={g.title} className="rounded-lg border border-slate-200 bg-white px-5 py-4">
              <h2 className="text-xs font-semibold tracking-wide text-slate-500 uppercase">
                {g.title}
                {g.full && <span className="ml-2 font-normal tracking-normal normal-case">· impact shows in the full preview</span>}
              </h2>
              <div className="divide-y divide-slate-100">
                {g.sentences.map((s) => {
                  const keys = parts(s.text)
                    .filter((x): x is { key: string } => typeof x !== 'string')
                    .map((x) => x.key)
                  const editing = keys.find((k) => k === open)
                  return (
                    <div key={s.text} className="py-3">
                      <p className="text-[15px] leading-8 text-ink">
                        {s.locked && <span className="mr-1 rounded bg-slate-100 px-1.5 py-0.5 text-xs text-slate-600">Locked</span>}
                        {parts(s.text).map((x, i) =>
                          typeof x === 'string' ? (
                            <span key={i}>{x}</span>
                          ) : (
                            <button
                              key={i}
                              type="button"
                              onClick={() => setOpen(open === x.key ? '' : x.key)}
                              aria-expanded={open === x.key}
                              className={`mx-0.5 rounded-md border-2 px-1.5 py-0.5 font-semibold ${
                                changed.includes(x.key)
                                  ? 'border-amber-400 bg-amber-50 text-amber-900'
                                  : 'border-dashed border-navy/40 bg-navy-50 text-navy'
                              } ${open === x.key ? 'ring-2 ring-navy/30' : ''}`}
                            >
                              {chipText(x.key, rules[x.key])}
                            </button>
                          ),
                        )}
                      </p>
                      {s.note && (
                        <p className="text-xs text-slate-500">
                          {parts(s.note).map((x, i) => (typeof x === 'string' ? x : <strong key={i}>{chipText(x.key, rules[x.key])}</strong>))}
                        </p>
                      )}
                      {editing && (
                        <SettingEditor
                          key={editing}
                          k={editing}
                          value={rules[editing]}
                          live={p.live_rules[editing]}
                          pid={id}
                          rules={rules}
                          onChange={(v) => setRules({ ...rules, [editing]: v })}
                        />
                      )}
                    </div>
                  )
                })}
              </div>
            </section>
          ))}
        </div>
        <aside className="min-w-0 flex-[1_1_340px] space-y-4 rounded-lg border border-slate-200 bg-white p-5 lg:sticky lg:top-4">
          <h2 className="text-lg font-semibold text-navy">If this draft were live</h2>
          <ErrorNote error={estErr} />
          <Impact est={est} loading={estLoading} />
          <div className="space-y-2 border-t border-slate-100 pt-4">
            <h3 className="text-xs font-semibold tracking-wide text-slate-500 uppercase">
              {changed.length} {changed.length === 1 ? 'change' : 'changes'} from v{p.live}
            </h3>
            <ul className="list-disc space-y-1 pl-5 text-sm">
              {changed.map((k) => (
                <li key={k}>
                  {settingLabel(k)}:{' '}
                  {Array.isArray(rules[k])
                    ? `${(rules[k] as unknown[]).length} entries (was ${(p.live_rules[k] as unknown[]).length})`
                    : `${formatSetting(k, p.live_rules[k])} → ${formatSetting(k, rules[k])}`}
                </li>
              ))}
            </ul>
            <label className="block space-y-1 text-sm">
              <span className="font-medium">Why (goes with the draft to the approver)</span>
              <textarea
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                rows={2}
                className="block w-full rounded-md border border-slate-300 px-2 py-1.5"
              />
            </label>
            {blocked && (
              <p className="text-sm text-crimson">
                A must-catch vendor would be dropped. You can save the draft, but it can’t be approved like this.
              </p>
            )}
            <div className="flex flex-wrap gap-2">
              <Button onClick={save} disabled={saving || changed.length === 0}>
                Save draft
              </Button>
              {p.draft && (
                <Button variant="secondary" onClick={discard}>
                  Discard draft
                </Button>
              )}
              <Link to={`/policies/${id}`} className="rounded-md px-3 py-1.5 text-sm font-medium text-navy hover:bg-navy-50">
                Back to the pack
              </Link>
            </div>
            {msg && (
              <p role="status" className="text-sm text-emerald-800">
                {msg}
              </p>
            )}
            <ErrorNote error={err} />
            <p className="text-xs text-slate-500">
              When the draft is saved,{' '}
              <Link to={`/policies/${id}/review`} className="text-navy underline">
                run the full preview and send it for approval
              </Link>
              .
            </p>
          </div>
        </aside>
      </div>
    </div>
  )
}
