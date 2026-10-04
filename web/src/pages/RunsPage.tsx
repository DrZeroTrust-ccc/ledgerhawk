import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api, money, num } from '../api'
import { useAnalystName } from '../App'
import { Button, Card, DataClassBadge, ErrorNote, Loading, useAsync } from '../ui'

function UploadForm() {
  const [analyst] = useAnalystName()
  const nav = useNavigate()
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
    try {
      const { id } = await api.createRun(f)
      nav(`/runs/${id}`)
    } catch (err) {
      setError((err as Error).message)
      setBusy(false)
    }
  }

  const field = 'block w-full text-sm file:mr-3 file:rounded-md file:border-0 file:bg-navy-50 file:px-3 file:py-1.5 file:text-sm file:font-medium file:text-navy'
  return (
    <form onSubmit={submit} className="space-y-4">
      <div className="grid gap-4 md:grid-cols-2">
        <label className="space-y-1.5">
          <span className="text-sm font-medium">Agency vendor file</span>
          <input type="file" accept=".xlsx,.xls,.csv" onChange={(e) => setVendors(e.target.files?.[0] ?? null)} className={field} />
          <span className="block text-xs text-slate-500">One row per UEI with FY24 and FY25 net obligations (.xlsx or .csv).</span>
        </label>
        <label className="space-y-1.5">
          <span className="text-sm font-medium">SAM exclusions extract (recommended)</span>
          <input type="file" accept=".csv" onChange={(e) => setExclusions(e.target.files?.[0] ?? null)} className={field} />
          <span className="block text-xs text-slate-500">The daily public CSV. Without it, the exclusion lane is empty.</span>
        </label>
      </div>
      <div className="flex flex-wrap items-end gap-4">
        {exclusions && (
          <label className="space-y-1.5">
            <span className="block text-sm font-medium">Exclusions extract date</span>
            <input type="date" required value={exDate} onChange={(e) => setExDate(e.target.value)} className="rounded-md border border-slate-300 px-2 py-1 text-sm" />
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
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold text-navy">Runs</h1>
        <p className="mt-1 text-sm text-slate-600">Each run screens one vendor file against one exclusions extract. Runs never change after they finish, so their numbers stay reproducible.</p>
      </div>
      <Card title="Start a new run">
        <UploadForm />
      </Card>
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
                      <td className="tabular py-2.5 text-right">{num(q.priority + q.strong + q.exclusion)}</td>
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
