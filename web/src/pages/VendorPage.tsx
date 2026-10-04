import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, LANE_LABEL, money, REASON_LABEL, type ExclusionHit, type SamCard, type VendorDetail } from '../api'
import LinkGraph from '../LinkGraph'
import { useAnalystName } from '../App'
import { Button, Card, ErrorNote, FlagChip, Loading, QueueChip, useAsync } from '../ui'

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
  const scopeColor = h.scope === 'Firm-wide' ? 'bg-crimson text-white' : h.scope === 'Facility-only' ? 'bg-slate-200 text-slate-700' : 'bg-amber-100 text-amber-900'
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
          {h.name || <span className="text-slate-500">Name not given</span>} {h.uei && <span className="font-mono text-xs text-slate-500">{h.uei}</span>}
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
    <span className={`rounded px-1.5 py-0.5 text-xs ${hub ? 'bg-slate-100 text-slate-500' : n > 1 ? 'bg-amber-50 text-amber-800' : 'bg-slate-50 text-slate-500'}`}>
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
        <span className={`mr-2 rounded px-1.5 py-0.5 text-xs font-medium ${c.active ? 'bg-emerald-50 text-emerald-800' : 'bg-amber-50 text-amber-800'}`}>{c.active ? 'Active' : 'Expired'}</span>
        registered {c.reg_date || '?'}, expires {c.exp_date || '?'}, updated {c.last_update || '?'}
      </dd>
      <dt className="text-slate-500">Business start date</dt>
      <dd>{c.start_date || '—'}</dd>
      <dt className="text-slate-500">Certifications</dt>
      <dd>{c.certs.length ? c.certs.join(', ') : <span className="text-slate-500">None</span>}</dd>
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
              {p.title && <span className="text-slate-600">, {p.title}</span>} <span className="text-slate-500">({[p.city, p.state].filter(Boolean).join(', ')})</span>
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

function GraphCard({ runId, uei }: { runId: string; uei: string }) {
  const { data, error } = useAsync(() => api.graph(runId, uei), [runId, uei])
  return (
    <Card title="Link graph">
      <ErrorNote error={error} />
      {!data && !error && <Loading />}
      {data && <LinkGraph graph={data} runId={runId} uei={uei} />}
    </Card>
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
        <div className="rounded-md bg-slate-50 p-3 text-sm">
          <div className="font-medium">{v.disposition.value}</div>
          <div className="text-slate-600">{v.disposition.note}</div>
          <div className="mt-1 text-xs text-slate-500">
            {v.disposition.analyst} · {new Date(v.disposition.at).toLocaleString()}
          </div>
        </div>
      )}
      <select required value={value} onChange={(e) => setValue(e.target.value)} className="w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm">
        <option value="" disabled>
          Choose a disposition
        </option>
        {meta.data?.dispositions.map((d) => (
          <option key={d}>{d}</option>
        ))}
      </select>
      <textarea required value={note} onChange={(e) => setNote(e.target.value)} rows={3} placeholder="Note (required): what you checked and why" className="w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm" />
      <Button type="submit" disabled={busy || !value || !note.trim() || !analyst.trim()} className="w-full">
        {v.disposition ? 'Update disposition' : 'Save disposition'}
      </Button>
      {!analyst.trim() && <p className="text-xs text-slate-500">Enter your name in the header first.</p>}
      <ErrorNote error={error} />
    </form>
  )
}

export default function VendorPage() {
  const { id = '', uei = '' } = useParams()
  const { data: v, error, reload } = useAsync(() => api.vendor(id, uei), [id, uei])
  if (error) return <ErrorNote error={error} />
  if (!v) return <Loading />
  const scored = v.signals.filter((s) => s.id !== 'S6')
  const context = v.signals.filter((s) => s.id === 'S6')
  return (
    <div className="space-y-6">
      <div>
        <Link to={`/runs/${id}/queue`} className="text-sm text-navy hover:underline">
          ← Queue
        </Link>
        <div className="mt-2 flex flex-wrap items-center gap-3">
          <h1 className="text-2xl font-semibold text-navy">{v.name}</h1>
          <QueueChip queue={v.queue || (v.bucket === 'watch' ? 'watch' : '')} />
          {v.exclusion_flags.map((f) => (
            <FlagChip key={f} flag={f} />
          ))}
        </div>
        <p className="mt-1 font-mono text-sm text-slate-500">
          UEI {v.uei} {v.struct && <span className="font-sans">· {v.struct}</span>} {v.naics && <span className="font-sans">· NAICS {v.naics}</span>} {v.psc && <span className="font-sans">· PSC {v.psc}</span>}
        </p>
      </div>

      <div className="grid gap-6 lg:grid-cols-3">
        <div className="min-w-0 space-y-6 lg:col-span-2">
          <Card title="Why it flagged">
            <p className="text-[15px] leading-relaxed">{v.why}</p>
            <div className="mt-4 grid grid-cols-3 gap-4 border-t border-slate-100 pt-4 text-sm">
              <div>
                <div className="text-xs text-slate-500">FY24</div>
                <div className="tabular font-medium">{money(v.fy24)}</div>
              </div>
              <div>
                <div className="text-xs text-slate-500">FY25</div>
                <div className="tabular font-medium">{money(v.fy25)}</div>
              </div>
              <div>
                <div className="text-xs text-slate-500">Dollars under review</div>
                <div className="tabular font-medium">{money(v.tot)}</div>
              </div>
            </div>
          </Card>

          <Card title="Screening signals">
            {scored.length === 0 && context.length === 0 && <p className="text-sm text-slate-500">No screening signals for this vendor.</p>}
            <ul className="space-y-3">
              {[...scored, ...context].map((s, i) => (
                <li key={i} className="flex gap-3">
                  <span className={`h-fit whitespace-nowrap rounded px-1.5 py-0.5 font-mono text-xs font-semibold ${s.id === 'S6' || s.id === 'R_split' ? 'bg-slate-100 text-slate-500' : 'bg-navy-50 text-navy'}`}>{s.id}</span>
                  <div className="text-sm">
                    <div className="font-medium">
                      {s.label}
                      {s.id === 'S5' && <span className="ml-2 text-xs font-normal text-slate-500">second signal only</span>}
                      {s.id === 'R_split' && <span className="ml-2 text-xs font-normal text-slate-500">context; scores only when certified</span>}
                    </div>
                    <div className="text-slate-600">{s.detail}</div>
                  </div>
                </li>
              ))}
            </ul>
            {v.suppression && <p className="mt-4 rounded-md bg-slate-50 p-3 text-sm text-slate-600">Lawful pattern, growth signals discounted: {v.suppression}</p>}
          </Card>

          <Card title="SAM profile">
            {v.sam ? (
              <SamProfile c={v.sam} />
            ) : (
              <p className="text-sm text-slate-500">No SAM registration in this run's extract. Unmatched vendors usually have lapsed registrations, or the run had no SAM extract.</p>
            )}
          </Card>

          {v.links.length > 0 && (
            <Card title="Linked vendors">
              <p className="mb-3 text-sm text-slate-600">Different companies that share a contact and a suite or building with this vendor, after hub suppression. Signals, not proof of common control.</p>
              <ul className="divide-y divide-slate-100 text-sm">
                {v.links.map((l) => (
                  <li key={l.uei} className="py-2">
                    <Link to={`/runs/${id}/vendors/${encodeURIComponent(l.uei)}`} className="font-medium text-navy hover:underline">
                      {l.name}
                    </Link>{' '}
                    <span className="font-mono text-xs text-slate-500">{l.uei}</span> <span className="tabular text-slate-600">{money(l.tot)}</span>
                    {l.certified && <span className="ml-2 rounded bg-navy-50 px-1.5 py-0.5 text-xs text-navy">certified</span>}
                    <div className="text-xs text-slate-500">via {l.via}</div>
                  </li>
                ))}
              </ul>
            </Card>
          )}

          {v.sam && <GraphCard runId={id} uei={v.uei} />}

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
        </div>

        <div className="min-w-0 space-y-6">
          <Card title="Disposition">
            <DispositionForm runId={id} v={v} onSaved={reload} />
          </Card>
          <Card title="Where it sits in this run">
            <dl className="space-y-2 text-sm">
              <div>
                <dt className="text-xs text-slate-500">Lane</dt>
                <dd>{LANE_LABEL[v.lane] ?? v.lane}</dd>
              </div>
              {v.reason_code && (
                <div>
                  <dt className="text-xs text-slate-500">Cut reason</dt>
                  <dd>
                    {REASON_LABEL[v.reason_code] ?? v.reason_code} <span className="font-mono text-xs text-slate-400">{v.reason_code}</span>
                    <div className="text-xs text-slate-600">{v.reason}</div>
                    {v.restored_from && <div className="text-xs text-slate-600">Originally cut as {v.restored_from}</div>}
                  </dd>
                </div>
              )}
            </dl>
          </Card>
          <Card title="History">
            {v.history.length === 0 && <p className="text-sm text-slate-500">No analyst actions yet.</p>}
            <ol className="space-y-3">
              {v.history.map((h, i) => (
                <li key={i} className="text-sm">
                  <div className="text-xs text-slate-500">
                    {new Date(h.at).toLocaleString()} · {h.analyst}
                  </div>
                  <div>
                    <span className="font-medium">{h.action === 'disposition' ? 'Disposition' : h.action === 'restored' ? 'Restored to queue' : h.action}</span>: {h.detail}
                  </div>
                </li>
              ))}
            </ol>
          </Card>
        </div>
      </div>
    </div>
  )
}
