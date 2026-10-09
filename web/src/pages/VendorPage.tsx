import { useState } from 'react'
import { Breadcrumbs, queueHref, runLabel, usePlace, useRuns } from '../nav'
import { ContextPanel } from '../Context'
import { AwardBlock, LedgerCard, leadCtx, Notes, Review, WrittenSummary } from '../Case'
import { useAnalystName } from '../App'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { api, LANE_LABEL, money, REASON_LABEL, type ExclusionHit, type SamCard, type VendorDetail } from '../api'
import LinkMap from '../LinkMap'
import { FarCard } from '../Far'
import { MoneyByYear, ScreenEvidence, WhyHere } from '../VendorRecord'
import { Button, Card, ColorChip, DownloadMenu, ErrorNote, FlagChip, Loading, QueueChip, TierChip, TIER_SHORT, useAsync } from '../ui'

const KIND_LABEL: Record<string, string> = {
  direct: 'Excluded under this UEI',
  alias: 'Named as an alias or affiliate in an exclusion record',
  name_match: 'Same name as an excluded firm',
  address: 'Shares a suite with an excluded party',
  person: 'Shares a contact with an excluded party',
}

// One card per exclusion record, listing every way it ties to this vendor.
function groupHits(hits: ExclusionHit[]): { h: ExclusionHit; ties: ExclusionHit[] }[] {
  const out = new Map<string, { h: ExclusionHit; ties: ExclusionHit[] }>()
  for (const h of hits) {
    const k = `${h.name}|${h.agency}|${h.uei}|${h.active_date}`
    if (!out.has(k)) out.set(k, { h, ties: [] })
    out.get(k)!.ties.push(h)
  }
  return [...out.values()]
}

function ExclusionRecord({ h, ties }: { h: ExclusionHit; ties: ExclusionHit[] }) {
  const scopeColor =
    h.scope === 'Firm-wide' ? 'bg-crimson text-white' : h.scope === 'Facility-only' ? 'bg-slate-200 text-slate-700' : 'bg-amber-100 text-amber-900'
  return (
    <div className={`rounded-md border p-4 ${h.kind === 'name_match' ? 'border-dashed border-slate-300' : 'border-crimson/30'}`}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-semibold">{ties.map((t) => KIND_LABEL[t.kind]).join(' · ')}</span>
        <span className={`rounded px-2 py-0.5 text-xs font-medium ${scopeColor}`}>{h.scope}</span>
        {h.kind === 'name_match' && <span className="text-xs text-slate-500">Support: {h.support || 'unsupported'}</span>}
      </div>
      {ties
        .filter((t) => t.evidence)
        .map((t, i) => (
          <p key={i} className="mt-1 text-sm text-slate-600">
            {t.evidence}
          </p>
        ))}
      <dl className="mt-3 grid gap-x-6 gap-y-1 text-sm sm:grid-cols-2">
        <dt className="text-slate-500">Excluded party</dt>
        <dd>
          {h.name || <span className="text-slate-500">Name not given</span>}{' '}
          {h.uei && <span className="font-mono text-xs text-slate-500">{h.uei}</span>}
        </dd>
        <dt className="text-slate-500">Excluding agency</dt>
        <dd>{h.agency || '—'}</dd>
        <dt className="text-slate-500">Type</dt>
        <dd>
          {h.type || '—'}
          {h.ct_code && <span className="ml-1 text-xs text-slate-500">CT {h.ct_code}</span>}
        </dd>
        <dt className="text-slate-500">Dates</dt>
        <dd>
          {h.active_date || '?'} → {h.termination_date}
        </dd>
        <dt className="text-slate-500">Location</dt>
        <dd>{[h.city, h.state].filter(Boolean).join(', ') || '—'}</dd>
      </dl>
      {h.comments && (
        <blockquote className="mt-3 border-l-2 border-slate-300 pl-3 text-xs text-slate-600">
          <span className="font-medium text-slate-500">Additional comments (verbatim): </span>
          {h.comments}
        </blockquote>
      )}
    </div>
  )
}

const HISTORY_LABEL: Record<string, string> = {
  disposition: 'Disposition',
  restored: 'Restored to queue',
  tier: 'Tier',
  routing: 'Routing',
  assigned: 'Assignment',
  disposition_confirmed: 'Kept earlier decision',
  context_lookup: 'Outside context search',
  context_verdict: 'Outside context call',
  case_note: 'Note',
  case_note_removed: 'Note removed',
  case_awards: 'Award lookup',
  case_submit: 'Submitted for review',
  case_approve: 'Approved',
  case_return: 'Returned',
  case_reopen: 'Reopened',
  far: 'FAR review',
}

const ROLE: Record<string, string> = {
  gov_business: 'Government business POC',
  alt_gov_business: 'Alternate government business POC',
  past_performance: 'Past performance POC',
  alt_past_performance: 'Alternate past performance POC',
  electronic_business: 'Electronic business POC',
  alt_electronic_business: 'Alternate electronic business POC',
}

function Count({ n, what }: { n: number; what: string }) {
  const hub = n > 5
  return (
    <span
      className={`rounded px-1.5 py-0.5 text-xs ${hub ? 'bg-slate-100 text-slate-500' : n > 1 ? 'bg-amber-50 text-amber-800' : 'bg-slate-50 text-slate-500'}`}
    >
      {n <= 1 ? `only this entity ${what}` : `${n} SAM entities ${what}${hub ? ', hub suppressed' : ''}`}
    </span>
  )
}

function SamProfile({ c }: { c: SamCard }) {
  const map = `https://www.openstreetmap.org/search?query=${encodeURIComponent(c.address)}`
  return (
    <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-[10rem_1fr]">
      <dt className="text-slate-500">Registration</dt>
      <dd>
        <span
          className={`mr-2 rounded px-1.5 py-0.5 text-xs font-medium ${c.active ? 'bg-emerald-50 text-emerald-800' : 'bg-amber-50 text-amber-800'}`}
        >
          {c.active ? 'Active' : 'Expired'}
        </span>
        registered {c.reg_date || '?'}, expires {c.exp_date || '?'}, updated {c.last_update || '?'}
      </dd>
      <dt className="text-slate-500">Business start date</dt>
      <dd>{c.start_date || '—'}</dd>
      <dt className="text-slate-500">Certifications</dt>
      <dd>{c.certs.length ? c.certs.join(', ') : <span className="text-slate-500">None</span>}</dd>
      {c.owner && c.owner.length > 0 && (
        <>
          <dt className="text-slate-500">Ownership</dt>
          <dd title={c.business_types?.length ? `SAM business types: ${c.business_types.join(', ')}` : undefined}>{c.owner.join(', ')}</dd>
        </>
      )}
      {c.dba && (
        <>
          <dt className="text-slate-500">Doing business as</dt>
          <dd>{c.dba}</dd>
        </>
      )}
      <dt className="text-slate-500">Physical address</dt>
      <dd className="space-y-1">
        <div>
          {c.address}{' '}
          <a href={map} target="_blank" rel="noreferrer" className="text-xs text-navy underline">
            map
          </a>
        </div>
        <div className="flex flex-wrap gap-2">
          <Count n={c.suite_count} what="at this suite" />
          <Count n={c.bldg_count} what="in this building" />
          {c.residential && <span className="rounded bg-amber-50 px-1.5 py-0.5 text-xs text-amber-800">Apartment, unit or PO box</span>}
          {c.virtual && <span className="rounded bg-amber-50 px-1.5 py-0.5 text-xs text-amber-800">Virtual office or mailbox</span>}
        </div>
      </dd>
      <dt className="text-slate-500">Points of contact</dt>
      <dd>
        {c.pocs.length === 0 && <span className="text-slate-500">None listed</span>}
        <ul className="space-y-1.5">
          {c.pocs.map((p) => (
            <li key={p.role}>
              <span className="font-medium">{p.name}</span>
              {p.title && <span className="text-slate-600">, {p.title}</span>}{' '}
              <span className="text-slate-500">({[p.city, p.state].filter(Boolean).join(', ')})</span>
              <div className="flex flex-wrap items-center gap-2 text-xs text-slate-500">
                {ROLE[p.role] ?? p.role} <Count n={p.universe} what="list this person" />
              </div>
            </li>
          ))}
        </ul>
      </dd>
    </dl>
  )
}

function TierRouting({ runId, v, onSaved }: { runId: string; v: VendorDetail; onSaved: () => void }) {
  const [analyst] = useAnalystName()
  const meta = useAsync(() => api.meta(), [])
  const [tier, setTier] = useState(v.tier)
  const [reason, setReason] = useState('')
  const [owner, setOwner] = useState(v.owner)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true)
    setError(null)
    try {
      await fn()
      setReason('')
      onSaved()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }
  const owners = meta.data?.owners ?? []
  return (
    <div className="space-y-4 text-sm">
      <div>
        <div className="flex flex-wrap items-center gap-2">
          {v.tier ? <TierChip tier={v.tier} changed={!!v.tier_change} /> : <span className="text-slate-500">No tier</span>}
          {v.tier_change && v.tier_default !== v.tier && (
            <span className="text-xs text-slate-500">pipeline default: {v.tier_default ? TIER_SHORT[v.tier_default] : 'none'}</span>
          )}
        </div>
        {v.tier_change && (
          <p className="mt-1.5 text-xs text-slate-600">
            {v.tier_change.reason}
            <span className="block text-slate-500">
              {v.tier_change.analyst} · {new Date(v.tier_change.at).toLocaleString()} · was{' '}
              {v.tier_change.prior ? TIER_SHORT[v.tier_change.prior] : 'no tier'}
            </span>
          </p>
        )}
      </div>
      <form
        className="space-y-2"
        onSubmit={(e) => {
          e.preventDefault()
          run(() => api.setTier(runId, v.uei, { tier, reason, analyst }))
        }}
      >
        <select value={tier} onChange={(e) => setTier(e.target.value)} className="w-full rounded-md border border-slate-300 px-2 py-1.5">
          <option value="">No tier</option>
          {meta.data &&
            Object.entries(meta.data.tiers).map(([k, label]) => (
              <option key={k} value={k}>
                {label}: {meta.data!.tier_meaning[k]}
              </option>
            ))}
        </select>
        {tier !== v.tier && (
          <>
            <textarea
              required
              rows={2}
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="Reason (required), e.g. Promoted: award records show one IDIQ"
              className="w-full rounded-md border border-slate-300 px-2 py-1.5"
            />
            <Button type="submit" className="w-full" disabled={busy || !reason.trim() || !analyst.trim()}>
              Change tier
            </Button>
          </>
        )}
      </form>
      <form
        className="space-y-2 border-t border-slate-100 pt-3"
        onSubmit={(e) => {
          e.preventDefault()
          run(() => api.setRouting(runId, v.uei, { owner, analyst }))
        }}
      >
        <label className="block text-xs text-slate-500">Routes to</label>
        <input
          list="owners"
          value={owner}
          onChange={(e) => setOwner(e.target.value)}
          className="w-full rounded-md border border-slate-300 px-2 py-1.5"
        />
        <datalist id="owners">
          {owners.map((o) => (
            <option key={o} value={o} />
          ))}
        </datalist>
        {!v.owner_set && v.owner_suggested && <p className="text-xs text-slate-500">Suggested from the signals; edit to override.</p>}
        {v.owner_set && (
          <p className="text-xs text-slate-500">
            Set by {v.owner_set.analyst}; suggested: {v.owner_suggested || 'none'}
          </p>
        )}
        {owner !== v.owner && (
          <Button type="submit" variant="secondary" className="w-full" disabled={busy || !owner.trim() || !analyst.trim()}>
            Save owner
          </Button>
        )}
      </form>
      {v.assignee && <p className="border-t border-slate-100 pt-3 text-xs text-slate-600">Assigned to {v.assignee}. Reassign from the queue.</p>}
      {!analyst.trim() && <p className="text-xs text-slate-500">Enter your name in the header first.</p>}
      <ErrorNote error={error} />
    </div>
  )
}

function DispositionForm({ runId, v, onSaved }: { runId: string; v: VendorDetail; onSaved: () => void }) {
  const [analyst] = useAnalystName()
  const meta = useAsync(() => api.meta(), [])
  const [value, setValue] = useState(v.disposition?.value ?? '')
  const [note, setNote] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  return (
    <form
      className="space-y-3"
      onSubmit={async (e) => {
        e.preventDefault()
        setBusy(true)
        setError(null)
        try {
          await api.setDisposition(runId, v.uei, { value, note, analyst })
          setNote('')
          onSaved()
        } catch (err) {
          setError((err as Error).message)
        } finally {
          setBusy(false)
        }
      }}
    >
      {v.disposition && (
        <div
          className={`rounded-md p-3 text-sm ${v.disposition.carried_from ? 'border border-dashed border-amber-300 bg-amber-50/60' : 'bg-slate-50'}`}
        >
          {v.disposition.carried_from && (
            <div className="mb-1 text-xs font-medium text-amber-800">
              Carried from import {v.disposition.carried_from.label} of {v.disposition.carried_from.created_at.slice(0, 10)}. Not yet decided in this
              import.
            </div>
          )}
          <div className="font-medium">{v.disposition.value}</div>
          <div className="text-slate-600">{v.disposition.note}</div>
          <div className="mt-1 text-xs text-slate-500">
            {v.disposition.analyst} · {new Date(v.disposition.at).toLocaleString()}
          </div>
          {v.disposition.carried_from && (
            <Button
              type="button"
              variant="secondary"
              className="mt-2"
              disabled={busy || !analyst.trim()}
              onClick={async () => {
                setBusy(true)
                setError(null)
                try {
                  await api.confirmCarried(runId, { ueis: [v.uei], analyst })
                  onSaved()
                } catch (err) {
                  setError((err as Error).message)
                } finally {
                  setBusy(false)
                }
              }}
            >
              Keep this decision in this import
            </Button>
          )}
        </div>
      )}
      <select
        required
        value={value}
        onChange={(e) => setValue(e.target.value)}
        className="w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm"
      >
        <option value="" disabled>
          Choose a disposition
        </option>
        {meta.data?.dispositions.map((d) => (
          <option key={d}>{d}</option>
        ))}
      </select>
      <textarea
        required
        value={note}
        onChange={(e) => setNote(e.target.value)}
        rows={3}
        placeholder="Note (required): what you checked and why"
        className="w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm"
      />
      <Button type="submit" disabled={busy || !value || !note.trim() || !analyst.trim()} className="w-full">
        {v.disposition ? 'Update disposition' : 'Save disposition'}
      </Button>
      {!analyst.trim() && <p className="text-xs text-slate-500">Enter your name in the header first.</p>}
      <ErrorNote error={error} />
    </form>
  )
}

function OutsideContextCard({ runId, v }: { runId: string; v: VendorDetail }) {
  const [analyst] = useAnalystName()
  const got = useAsync(() => api.context({ uei: v.uei }), [v.uei])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const c = got.data?.context
  const look = async () => {
    setBusy(true)
    setError(null)
    const f = new FormData()
    f.append('analyst', analyst)
    f.append('name', v.sam?.legal_name || v.name)
    f.append('uei', v.uei)
    f.append('run_id', runId)
    f.append('state', v.sam?.state ?? '')
    // What we already know about the vendor, so its hits can be told from same-name strangers.
    f.append('city', v.sam?.city ?? '')
    f.append('cage', v.sam?.cage ?? '')
    f.append('other_names', [v.sam?.dba, v.name].filter(Boolean).join('\n'))
    f.append('people', (v.sam?.pocs ?? []).map((p) => p.name).join('\n'))
    try {
      await api.lookupContext(f)
      got.reload()
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <Card
      title="Outside context"
      action={
        <Button
          variant="secondary"
          disabled={busy || !analyst.trim()}
          onClick={look}
          title={analyst.trim() ? undefined : 'Enter your name in the header first'}
        >
          {busy ? 'Searching…' : c ? 'Refresh' : 'Search news, courts, DOJ, SEC, OFAC'}
        </Button>
      }
    >
      <ErrorNote error={error || got.error} />
      {c ? (
        <ContextPanel key={c.fetched_at} c={c} title="Results" runId={runId} />
      ) : (
        !busy && (
          <p className="text-sm text-slate-500">
            Not searched yet. Looks for news coverage, DOJ press releases, federal court dockets and opinions, SEC filings and the OFAC sanctions list
            that name this vendor.
          </p>
        )
      )}
    </Card>
  )
}

const TABS = [
  ['money', 'Money'],
  ['screens', 'Subject screens'],
  ['map', 'Link map'],
  ['people', 'People and links'],
  ['outside', 'Outside context'],
  ['notes', 'Notes and files'],
  ['history', 'History'],
] as const
type Tab = (typeof TABS)[number][0]

function CaseAwards({ runId, v, reload }: { runId: string; v: VendorDetail; reload: () => void }) {
  const [analyst] = useAnalystName()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const aw = v.case.awards
  return (
    <Card
      title="Federal awards (USAspending)"
      action={
        <Button
          variant="secondary"
          disabled={busy || !analyst.trim()}
          title={analyst.trim() ? 'Contracts and IDVs reported to USAspending.gov for this UEI' : 'Enter your name in the header first'}
          onClick={async () => {
            setBusy(true)
            setError(null)
            const f = new FormData()
            f.append('analyst', analyst)
            try {
              await api.caseAwards(runId, v.uei, f)
              reload()
            } catch (err) {
              setError((err as Error).message)
            } finally {
              setBusy(false)
            }
          }}
        >
          {busy ? 'Looking up…' : aw ? 'Refresh' : 'Look up awards'}
        </Button>
      }
    >
      <ErrorNote error={error} />
      {aw ? (
        <AwardBlock entities={aw.entities} awards={aw} />
      ) : (
        <p className="text-sm text-slate-500">Not looked up yet. Awards that started after an exclusion go straight into the evidence ledger.</p>
      )}
    </Card>
  )
}

function SignalsCard({ v }: { v: VendorDetail }) {
  const scored = v.signals.filter((s) => s.id !== 'S6')
  const context = v.signals.filter((s) => s.id === 'S6')
  return (
    <Card title="Screening signals">
      {scored.length === 0 && context.length === 0 && <p className="text-sm text-slate-500">No screening signals for this vendor.</p>}
      <ul className="space-y-3">
        {[...scored, ...context].map((s, i) => (
          <li key={i} className="text-sm">
            <div className="font-medium">
              {s.label}
              {s.id === 'S5' && <span className="ml-2 text-xs font-normal text-slate-500">second signal only</span>}
              {s.id === 'R_split' && <span className="ml-2 text-xs font-normal text-slate-500">context; scores only when certified</span>}
              <span className="ml-2 font-mono text-[11px] font-normal text-slate-400">{s.id}</span>
            </div>
            <div className="text-slate-600">{s.detail}</div>
          </li>
        ))}
      </ul>
      {v.suppression && (
        <p className="mt-4 rounded-md bg-slate-50 p-3 text-sm text-slate-600">Lawful pattern, growth signals discounted: {v.suppression}</p>
      )}
    </Card>
  )
}

export default function VendorPage() {
  const { id = '', uei = '' } = useParams()
  const { data: v, error, reload } = useAsync(() => api.vendor(id, uei), [id, uei])
  const { runs } = useRuns()
  const [sp] = useSearchParams()
  const [tab, setTab] = useState<Tab>(() => TABS.find(([k]) => k === sp.get('tab'))?.[0] ?? 'money')
  usePlace(v ? `${v.name} (case)` : null)
  if (error) return <ErrorNote error={error} />
  if (!v) return <Loading />
  const ctx = leadCtx(id, v.uei, v.case.review, reload)
  const status = v.disposition ? (v.disposition.carried_from ? `${v.disposition.value} (carried)` : v.disposition.value) : 'Open'
  const notes = v.case.review.notes.length + v.case.earlier_notes.length
  return (
    <div className="space-y-6">
      <div>
        <Breadcrumbs
          items={[
            { label: 'Imports', to: '/' },
            { label: runLabel(runs, id), to: `/runs/${id}` },
            { label: 'Queue', to: queueHref(id) },
            { label: v.name },
          ]}
        />
        <div className="mt-2 flex flex-wrap items-center gap-3">
          <h1 className="text-2xl font-semibold text-navy">{v.name}</h1>
          <QueueChip queue={v.queue || (v.bucket === 'watch' ? 'watch' : '')} />
          <ColorChip color={v.color} why={v.color_why} />
          <TierChip tier={v.tier} changed={!!v.tier_change} />
          {v.exclusion_flags.map((f) => (
            <FlagChip key={f} flag={f} />
          ))}
          <span className="ml-auto">
            <DownloadMenu
              items={[
                {
                  label: 'Case file (Word)',
                  href: `/api/runs/${id}/vendors/${encodeURIComponent(v.uei)}/case.docx`,
                  hint: 'Editable, with notes and sign-off',
                },
                { label: 'Case file (PDF)', href: `/api/runs/${id}/vendors/${encodeURIComponent(v.uei)}/case.pdf`, hint: 'For sharing as is' },
              ]}
            />
          </span>
        </div>
        <p className="mt-1 font-mono text-sm text-slate-500">
          UEI {v.uei} {v.struct && <span className="font-sans">· {v.struct}</span>} {v.naics && <span className="font-sans">· NAICS {v.naics}</span>}{' '}
          {v.psc && <span className="font-sans">· PSC {v.psc}</span>}
        </p>
        <dl className="mt-3 grid grid-cols-2 gap-3 rounded-lg border border-slate-200 bg-white p-3 text-sm sm:grid-cols-5">
          <div>
            <dt className="text-xs text-slate-500">Dollars under review</dt>
            <dd className="tabular font-semibold">{money(v.tot)}</dd>
          </div>
          <div>
            <dt className="text-xs text-slate-500">FY24 → FY25</dt>
            <dd className="tabular">
              {money(v.fy24)} → {money(v.fy25)}
            </dd>
          </div>
          <div>
            <dt className="text-xs text-slate-500">Decision</dt>
            <dd className={v.disposition?.carried_from ? 'text-amber-800' : ''}>{status}</dd>
          </div>
          <div>
            <dt className="text-xs text-slate-500">Owner</dt>
            <dd className="truncate" title={v.owner}>
              {v.assignee || v.owner || 'Unassigned'}
            </dd>
          </div>
          <div>
            <dt className="text-xs text-slate-500">Sign-off</dt>
            <dd>{v.case.review.state_label}</dd>
          </div>
        </dl>
        <div className="mt-3">
          <WhyHere headline={v.hawk || v.headline || ''} flags={v.exclusion_flags} screens={v.screens} />
        </div>
      </div>

      <div className="grid gap-6 lg:grid-cols-3">
        <div className="min-w-0 space-y-6 lg:col-span-2">
          <MoneyByYear uei={v.uei} screens={v.screens} />
          <Card title="Summary">
            <WrittenSummary
              runId={id}
              uei={v.uei}
              summary={v.case.summary}
              earlier={v.case.earlier_summary}
              enabled={v.summary_enabled}
              locked={v.case.review.state === 'approved'}
              ledgerIds={v.ledger.rows.map((r) => r.id)}
              reload={reload}
            />
            <div className="mt-4 border-t border-slate-100 pt-3">
              <div className="mb-1 text-xs font-medium text-slate-500">Why the screen flagged it</div>
              {v.headline && <p className="mb-1 font-medium text-ink">{v.headline}</p>}
              <p className="text-sm leading-relaxed text-slate-700">{v.why}</p>
            </div>
          </Card>

          <FarCard runId={id} uei={v.uei} far={v.far} onSaved={reload} />

          <LedgerCard
            ledger={v.ledger}
            action={
              <button onClick={() => setTab('notes')} className="text-sm font-medium text-navy hover:underline">
                Add a finding
              </button>
            }
          />

          <div>
            <div className="flex flex-wrap gap-1 border-b border-slate-200" role="tablist">
              {TABS.map(([k, label]) => (
                <button
                  key={k}
                  role="tab"
                  aria-selected={tab === k}
                  onClick={() => setTab(k)}
                  className={`-mb-px border-b-2 px-3 py-2 text-sm font-medium ${tab === k ? 'border-crimson text-navy' : 'border-transparent text-slate-500 hover:text-navy'}`}
                >
                  {label}
                  {k === 'notes' && notes > 0 && <span className="ml-1 text-xs text-slate-400">{notes}</span>}
                  {k === 'people' && v.links.length > 0 && <span className="ml-1 text-xs text-slate-400">{v.links.length}</span>}
                  {k === 'screens' && v.screens.length > 0 && <span className="ml-1 text-xs text-slate-400">{v.screens.length}</span>}
                </button>
              ))}
            </div>
            <div className="mt-4 space-y-6">
              {tab === 'money' && (
                <>
                  <SignalsCard v={v} />
                  <CaseAwards runId={id} v={v} reload={reload} />
                </>
              )}
              {tab === 'people' && (
                <>
                  <Card title="SAM profile">
                    {v.sam ? (
                      <SamProfile c={v.sam} />
                    ) : (
                      <p className="text-sm text-slate-500">
                        No SAM registration in this import's extract. Unmatched vendors usually have lapsed registrations, or the import had no SAM
                        extract.
                      </p>
                    )}
                  </Card>
                  <Card title="Exclusions">
                    {v.exclusion.length === 0 ? (
                      <p className="text-sm text-slate-500">
                        No active exclusion record is tied to this vendor by UEI, name or alias{v.sam ? ', or by a shared suite or contact' : ''}.
                        {!v.sam && ' Address and contact ties need the SAM entity extract on this run.'}
                      </p>
                    ) : (
                      <div className="space-y-3">
                        {groupHits(v.exclusion).map((g, i) => (
                          <ExclusionRecord key={i} h={g.h} ties={g.ties} />
                        ))}
                      </div>
                    )}
                  </Card>
                  {v.links.length > 0 && (
                    <Card title="Linked vendors">
                      <p className="mb-3 text-sm text-slate-600">
                        Different companies that share a contact and a suite or building with this vendor, after hub suppression. Signals, not proof
                        of common control.
                      </p>
                      <ul className="divide-y divide-slate-100 text-sm">
                        {v.links.map((l) => (
                          <li key={l.uei} className="py-2">
                            <Link to={`/runs/${id}/vendors/${encodeURIComponent(l.uei)}`} className="font-medium text-navy hover:underline">
                              {l.name}
                            </Link>{' '}
                            <span className="font-mono text-xs text-slate-500">{l.uei}</span>{' '}
                            <span className="tabular text-slate-600">{money(l.tot)}</span>
                            {l.certified && <span className="ml-2 rounded bg-navy-50 px-1.5 py-0.5 text-xs text-navy">certified</span>}
                            <div className="text-xs text-slate-500">via {l.via}</div>
                          </li>
                        ))}
                      </ul>
                    </Card>
                  )}
                </>
              )}
              {tab === 'screens' && <ScreenEvidence screens={v.screens} />}
              {tab === 'map' && (
                <Card title="Link map">
                  <LinkMap runId={id} uei={v.uei} onNoted={reload} />
                </Card>
              )}
              {tab === 'outside' && <OutsideContextCard runId={id} v={v} />}
              {tab === 'notes' && (
                <Card title="Notes and files">
                  <Notes target="case" ctx={ctx} title="Notes in this import" earlier={v.case.earlier_notes} />
                </Card>
              )}
              {tab === 'history' && (
                <Card title="History">
                  {v.history.length === 0 && <p className="text-sm text-slate-500">No analyst actions yet.</p>}
                  <ol className="space-y-3">
                    {v.history.map((h, i) => (
                      <li key={i} className="text-sm">
                        <div className="text-xs text-slate-500">
                          {new Date(h.at).toLocaleString()} · {h.analyst}
                          {h.other_run && (
                            <>
                              {' · '}
                              <Link
                                to={`/runs/${h.other_run.id}/vendors/${v.uei}`}
                                className="rounded bg-slate-100 px-1 text-slate-600 hover:underline"
                              >
                                import {h.other_run.label}, {h.other_run.created_at.slice(0, 10)}
                              </Link>
                            </>
                          )}
                        </div>
                        <div>
                          <span className="font-medium">{HISTORY_LABEL[h.action] ?? h.action.replace(/_/g, ' ')}</span>: {h.detail}
                        </div>
                      </li>
                    ))}
                  </ol>
                </Card>
              )}
            </div>
          </div>
        </div>

        <div className="min-w-0 space-y-6">
          <Card title="Decision">
            <DispositionForm runId={id} v={v} onSaved={reload} />
          </Card>
          <Review ctx={ctx} />
          <Card title="Tier and routing">
            <TierRouting key={`${v.tier}|${v.owner}`} runId={id} v={v} onSaved={reload} />
          </Card>
          <Card title="Where it sits in this import">
            <dl className="space-y-2 text-sm">
              <div>
                <dt className="text-xs text-slate-500">Lane</dt>
                <dd>{LANE_LABEL[v.lane] ?? v.lane}</dd>
              </div>
              {v.reason_code && (
                <div>
                  <dt className="text-xs text-slate-500">Cut reason</dt>
                  <dd>
                    {REASON_LABEL[v.reason_code] ?? v.reason_code}
                    <div className="text-xs text-slate-600">{v.reason}</div>
                    {v.restored_from && <div className="text-xs text-slate-600">Originally cut as {v.restored_from}</div>}
                  </dd>
                </div>
              )}
            </dl>
          </Card>
        </div>
      </div>
    </div>
  )
}
