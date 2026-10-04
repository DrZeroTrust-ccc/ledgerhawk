import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api, money, num, queueTotal, type Source } from '../api'
import { useAnalystName } from '../App'
import { Button, Card, DataClassBadge, ErrorNote, Loading, useAsync } from '../ui'

const sizeLabel = (b: number) => (b >= 1e9 ? `${(b / 1e9).toFixed(1)} GB` : b >= 1e6 ? `${(b / 1e6).toFixed(1)} MB` : `${Math.ceil(b / 1e3)} KB`)

function SourceForm({ onAdded }: { onAdded: () => void }) {
  const [analyst] = useAnalystName()
  const [kind, setKind] = useState<'sam' | 'exclusions'>('sam')
  const [file, setFile] = useState<File | null>(null)
  const [asOf, setAsOf] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  return (
    <form
      className="flex flex-wrap items-end gap-3 border-t border-slate-100 pt-4"
      onSubmit={async (e) => {
        e.preventDefault()
        if (!file) return
        setBusy(true)
        setError(null)
        const f = new FormData()
        f.append('kind', kind)
        f.append('as_of', asOf)
        f.append('analyst', analyst)
        f.append('file', file)
        try {
          await api.addSource(f)
          setFile(null)
          onAdded()
        } catch (err) {
          setError((err as Error).message)
        } finally {
          setBusy(false)
        }
      }}
    >
      <label className="space-y-1">
        <span className="block text-xs font-medium text-slate-600">Source</span>
        <select value={kind} onChange={(e) => setKind(e.target.value as 'sam' | 'exclusions')} className="rounded-md border border-slate-300 px-2 py-1 text-sm">
          <option value="sam">SAM entity extract (V2 .dat)</option>
          <option value="exclusions">SAM exclusions extract (.csv)</option>
        </select>
      </label>
      <label className="space-y-1">
        <span className="block text-xs font-medium text-slate-600">File</span>
        <input type="file" accept={kind === 'sam' ? '.dat,.txt' : '.csv'} onChange={(e) => setFile(e.target.files?.[0] ?? null)} className="text-sm" />
      </label>
      <label className="space-y-1">
        <span className="block text-xs font-medium text-slate-600">Extract date</span>
        <input type="date" required value={asOf} onChange={(e) => setAsOf(e.target.value)} className="rounded-md border border-slate-300 px-2 py-1 text-sm" />
      </label>
      <Button type="submit" variant="secondary" disabled={!file || !asOf || busy || !analyst.trim()}>
        {busy ? 'Uploading…' : 'Add source'}
      </Button>
      <ErrorNote error={error} />
    </form>
  )
}

function DataSources({ sources, reload }: { sources: Source[] | null; reload: () => void }) {
  return (
    <Card title="Data sources">
      {!sources && <Loading />}
      {sources && sources.length === 0 && (
        <p className="mb-4 text-sm text-slate-500">No SAM or exclusions extracts loaded yet. Add them once here and every run can use them.</p>
      )}
      {sources && sources.length > 0 && (
        <div className="mb-4 overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-left text-xs text-slate-500">
              <tr>
                <th className="pb-2 font-medium">Source</th>
                <th className="pb-2 font-medium">As of</th>
                <th className="pb-2 font-medium">File</th>
                <th className="pb-2 font-medium">Added</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {sources.map((s) => (
                <tr key={s.id}>
                  <td className="py-2">{s.label}</td>
                  <td className="py-2">
                    {s.as_of}{' '}
                    <span className={`ml-1 rounded px-1.5 py-0.5 text-xs ${s.stale ? 'bg-amber-50 text-amber-800' : 'bg-emerald-50 text-emerald-800'}`}>
                      {s.age_days} days old
                      {s.stale ? `, stale after ${s.stale_after_days}` : ''}
                    </span>
                  </td>
                  <td className="py-2 text-slate-600">
                    {s.file} · {sizeLabel(s.bytes)}
                  </td>
                  <td className="py-2 text-slate-600">{s.uploaded_by}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <SourceForm onAdded={reload} />
    </Card>
  )
}

function UploadForm({ sources }: { sources: Source[] }) {
  const [analyst] = useAnalystName()
  const nav = useNavigate()
  const samSources = sources.filter((s) => s.kind === 'sam')
  const exSources = sources.filter((s) => s.kind === 'exclusions')
  const [samSource, setSamSource] = useState(samSources[0]?.id ?? '')
  const [exSource, setExSource] = useState(exSources[0]?.id ?? '')
  const [vendors, setVendors] = useState<File | null>(null)
  const [exclusions, setExclusions] = useState<File | null>(null)
  const [exDate, setExDate] = useState('')
  const [synthetic, setSynthetic] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!vendors) return
    setBusy(true)
    setError(null)
    const f = new FormData()
    f.append('vendors', vendors)
    if (exclusions) f.append('exclusions', exclusions)
    f.append('exclusions_date', exDate)
    f.append('synthetic', String(synthetic))
    f.append('analyst', analyst)
    f.append('sam_source', samSource)
    if (!exclusions) f.append('exclusions_source', exSource)
    try {
      const { id } = await api.createRun(f)
      nav(`/runs/${id}`)
    } catch (err) {
      setError((err as Error).message)
      setBusy(false)
    }
  }

  const field =
    'block w-full text-sm file:mr-3 file:rounded-md file:border-0 file:bg-navy-50 file:px-3 file:py-1.5 file:text-sm file:font-medium file:text-navy'
  return (
    <form onSubmit={submit} className="space-y-4">
      <div className="grid gap-4 md:grid-cols-3">
        <label className="space-y-1.5">
          <span className="text-sm font-medium">Agency vendor file</span>
          <input type="file" accept=".xlsx,.xls,.csv" onChange={(e) => setVendors(e.target.files?.[0] ?? null)} className={field} />
          <span className="block text-xs text-slate-500">One row per UEI with FY24 and FY25 net obligations (.xlsx or .csv).</span>
        </label>
        <label className="space-y-1.5">
          <span className="text-sm font-medium">SAM exclusions extract (recommended)</span>
          {exSources.length > 0 && !exclusions ? (
            <select
              value={exSource}
              onChange={(e) => setExSource(e.target.value)}
              className="block w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm"
            >
              <option value="">None</option>
              {exSources.map((s) => (
                <option key={s.id} value={s.id}>
                  As of {s.as_of} ({s.file}){s.stale ? ', stale' : ''}
                </option>
              ))}
            </select>
          ) : (
            <input type="file" accept=".csv" onChange={(e) => setExclusions(e.target.files?.[0] ?? null)} className={field} />
          )}
          <span className="block text-xs text-slate-500">The daily public CSV. Without it, the exclusion lane is empty.</span>
        </label>
        <label className="space-y-1.5">
          <span className="text-sm font-medium">SAM entity extract (recommended)</span>
          <select
            value={samSource}
            onChange={(e) => setSamSource(e.target.value)}
            className="block w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm"
            disabled={samSources.length === 0}
          >
            <option value="">{samSources.length ? 'None' : 'Add one under Data sources first'}</option>
            {samSources.map((s) => (
              <option key={s.id} value={s.id}>
                As of {s.as_of} ({s.file}){s.stale ? ', stale' : ''}
              </option>
            ))}
          </select>
          <span className="block text-xs text-slate-500">Adds SAM cards, the relationship screen, and address and contact ties to excluded parties.</span>
        </label>
      </div>
      <div className="flex flex-wrap items-end gap-4">
        {exclusions && (
          <label className="space-y-1.5">
            <span className="block text-sm font-medium">Exclusions extract date</span>
            <input
              type="date"
              required
              value={exDate}
              onChange={(e) => setExDate(e.target.value)}
              className="rounded-md border border-slate-300 px-2 py-1 text-sm"
            />
          </label>
        )}
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={synthetic} onChange={(e) => setSynthetic(e.target.checked)} />
          This is synthetic or demo data
        </label>
        <Button type="submit" disabled={!vendors || busy || !analyst.trim()} className="ml-auto">
          {busy ? 'Running…' : 'Run screen'}
        </Button>
      </div>
      {!analyst.trim() && <p className="text-xs text-slate-500">Enter your name in the header so the run is attributed to you.</p>}
      <ErrorNote error={error} />
    </form>
  )
}

export default function RunsPage() {
  const { data: runs, error } = useAsync(() => api.runs(), [])
  const sources = useAsync(() => api.sources(), [])
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold text-navy">Runs</h1>
        <p className="mt-1 text-sm text-slate-600">
          Each run screens one vendor file against the SAM and exclusions extracts you pick. Runs never change after they finish, so their numbers stay
          reproducible.
        </p>
      </div>
      <Card title="Start a new run">
        <UploadForm sources={sources.data?.sources ?? []} />
      </Card>
      <DataSources sources={sources.data?.sources ?? null} reload={sources.reload} />
      <Card title="Previous runs">
        <ErrorNote error={error} />
        {!runs && !error && <Loading />}
        {runs && runs.length === 0 && <p className="text-sm text-slate-500">No runs yet. Upload a vendor file above.</p>}
        {runs && runs.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-xs text-slate-500">
                <tr>
                  <th className="pb-2 font-medium">File</th>
                  <th className="pb-2 font-medium">Started</th>
                  <th className="pb-2 text-right font-medium">Vendors</th>
                  <th className="pb-2 text-right font-medium">Dollars</th>
                  <th className="pb-2 text-right font-medium">In queue</th>
                  <th />
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {runs.map((r) => {
                  const q = r.queue_counts
                  return (
                    <tr key={r.id}>
                      <td className="py-2.5">
                        <Link to={`/runs/${r.id}`} className="font-medium text-navy hover:underline">
                          {r.label}
                        </Link>{' '}
                        <DataClassBadge dataClass={r.data_class} />
                        {r.parent_id && <div className="text-xs text-slate-500">Rerun with {r.restore.length} restored vendor(s)</div>}
                      </td>
                      <td className="py-2.5 text-slate-600">
                        {new Date(r.created_at).toLocaleString()} · {r.created_by}
                      </td>
                      <td className="tabular py-2.5 text-right">{num(r.funnel[0].vendors)}</td>
                      <td className="tabular py-2.5 text-right">{money(r.funnel[0].dollars)}</td>
                      <td className="tabular py-2.5 text-right">{num(queueTotal(q))}</td>
                      <td className="py-2.5 text-right">
                        <Link to={`/runs/${r.id}/queue`} className="text-sm font-medium text-navy hover:underline">
                          Open queue →
                        </Link>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  )
}
