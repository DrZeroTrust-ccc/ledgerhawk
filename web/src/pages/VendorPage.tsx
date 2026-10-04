import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, LANE_LABEL, money, REASON_LABEL, type ExclusionHit, type VendorDetail } from '../api'
import { useAnalystName } from '../App'
import { Button, Card, ErrorNote, FlagChip, Loading, QueueChip, useAsync } from '../ui'

const KIND_LABEL: Record<string, string> = {
  direct: 'Excluded under this UEI',
  alias: 'Named as an alias or affiliate in an exclusion record',
  name_match: 'Same name as an excluded firm (not yet supported)',
}

function ExclusionRecord({ h }: { h: ExclusionHit }) {
  const scopeColor = h.scope === 'Firm-wide' ? 'bg-crimson text-white' : h.scope === 'Facility-only' ? 'bg-slate-200 text-slate-700' : 'bg-amber-100 text-amber-900'
  return (
    <div className={`rounded-md border p-4 ${h.kind === 'name_match' ? 'border-dashed border-slate-300' : 'border-crimson/30'}`}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-semibold">{KIND_LABEL[h.kind]}</span>
        <span className={`rounded px-2 py-0.5 text-xs font-medium ${scopeColor}`}>{h.scope}</span>
      </div>
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
        <div className="space-y-6 lg:col-span-2">
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
            {scored.length === 0 && context.length === 0 && <p className="text-sm text-slate-500">No summary-data signals for this vendor.</p>}
            <ul className="space-y-3">
              {[...scored, ...context].map((s, i) => (
                <li key={i} className="flex gap-3">
                  <span className={`h-fit rounded px-1.5 py-0.5 font-mono text-xs font-semibold ${s.id === 'S6' ? 'bg-slate-100 text-slate-500' : 'bg-navy-50 text-navy'}`}>{s.id}</span>
                  <div className="text-sm">
                    <div className="font-medium">
                      {s.label}
                      {s.id === 'S5' && <span className="ml-2 text-xs font-normal text-slate-500">second signal only</span>}
                    </div>
                    <div className="text-slate-600">{s.detail}</div>
                  </div>
                </li>
              ))}
            </ul>
            {v.suppression && <p className="mt-4 rounded-md bg-slate-50 p-3 text-sm text-slate-600">Lawful pattern, growth signals discounted: {v.suppression}</p>}
          </Card>

          <Card title="Exclusions">
            {v.exclusion.length === 0 ? (
              <p className="text-sm text-slate-500">No active exclusion record is tied to this vendor by UEI, name or alias. Address and contact links are checked once SAM enrichment is added.</p>
            ) : (
              <div className="space-y-3">
                {v.exclusion.map((h, i) => (
                  <ExclusionRecord key={i} h={h} />
                ))}
              </div>
            )}
          </Card>
        </div>

        <div className="space-y-6">
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
