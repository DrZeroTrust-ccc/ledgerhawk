import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api, type ProgramMeta } from '../api'
import { useAnalystName } from '../App'
import { Button, Card, ErrorNote, Loading, useAsync } from '../ui'

const field = 'w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm'

const FILES: { key: string; label: string; hint: string }[] = [
  { key: 'sponsors', label: 'Sponsors', hint: 'sponsor_id, name, program, officers, address, city, state, zip, approved, status, ended' },
  { key: 'sites', label: 'Meal sites', hint: 'site_id, sponsor_id, name, address, capacity, opened, meals_YYYY_MM per month' },
  { key: 'retailers', label: 'SNAP retailers', hint: 'store_id, name, owner, address, authorized, ended, disqualified' },
  { key: 'wic_vendors', label: 'WIC vendors', hint: 'vendor_id, name, owner, address, authorized' },
  { key: 'exclusions', label: 'Exclusion lists (optional)', hint: 'name, people, address, list, agency, type, active, terminates' },
  { key: 'court_records', label: 'Court records (optional)', hint: 'case_no, court, filed, caption, nature, parties, status, summary' },
  { key: 'osint', label: 'Open-source findings (optional)', hint: 'subject, address, zip, source, found, finding, link' },
]

export function ProgramClassBadge({ dataClass, label }: { dataClass: string; label?: string }) {
  const style =
    dataClass === 'simulated' ? 'bg-violet-100 text-violet-800' : dataClass === 'public' ? 'bg-sky-100 text-sky-800' : 'bg-slate-100 text-slate-700'
  return <span className={`rounded px-2 py-0.5 text-xs font-semibold uppercase tracking-wide ${style}`}>{label ?? dataClass}</span>
}

function NewProgram({ onDone }: { onDone: (id: string) => void }) {
  const [analyst] = useAnalystName()
  const [label, setLabel] = useState('')
  const [dataClass, setDataClass] = useState('client')
  const [files, setFiles] = useState<Record<string, File | null>>({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    const f = new FormData()
    f.append('label', label)
    f.append('data_class', dataClass)
    f.append('analyst', analyst)
    for (const [k, v] of Object.entries(files)) if (v) f.append(k, v)
    try {
      onDone((await api.createProgram(f)).id)
    } catch (err) {
      setError((err as Error).message)
      setBusy(false)
    }
  }

  return (
    <form onSubmit={submit} className="space-y-4">
      <div className="grid gap-4 sm:grid-cols-2">
        <label className="block text-sm">
          <span className="font-medium text-ink">Name</span>
          <input className={`${field} mt-1`} value={label} onChange={(e) => setLabel(e.target.value)} placeholder="e.g. State CACFP sponsors, FY26 Q2" />
        </label>
        <label className="block text-sm">
          <span className="font-medium text-ink">What kind of data</span>
          <select className={`${field} mt-1`} value={dataClass} onChange={(e) => setDataClass(e.target.value)}>
            <option value="client">Agency data</option>
            <option value="public">Public records</option>
            <option value="simulated">Simulated data</option>
          </select>
        </label>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        {FILES.map((x) => (
          <label key={x.key} className="block rounded-md border border-slate-200 p-3 text-sm">
            <span className="font-medium text-ink">{x.label}</span>
            <span className="mt-0.5 block text-xs text-slate-500">{x.hint}</span>
            <input type="file" accept=".csv,text/csv" className="mt-2 block w-full text-xs" onChange={(e) => setFiles({ ...files, [x.key]: e.target.files?.[0] ?? null })} />
          </label>
        ))}
      </div>
      <p className="text-xs text-slate-500">Sponsors, retailers or WIC vendors are enough to start. Sites need their sponsors file for the capacity and site-cluster checks. The
        optional lists are matched to every entity by name, person and address.</p>
      <ErrorNote error={error} />
      <Button type="submit" disabled={busy || !analyst.trim() || !['sponsors', 'retailers', 'wic_vendors'].some((k) => files[k])}>
        {busy ? 'Screening…' : 'Screen these files'}
      </Button>
      {!analyst.trim() && <p className="text-xs text-crimson">Enter your name at the top of the page first.</p>}
    </form>
  )
}

export default function ProgramsPage() {
  const nav = useNavigate()
  const [analyst] = useAnalystName()
  const { data, error } = useAsync(() => api.programs(), [])
  const [busy, setBusy] = useState(false)
  const [demoError, setDemoError] = useState<string | null>(null)

  const loadDemo = async () => {
    setBusy(true)
    setDemoError(null)
    const f = new FormData()
    f.append('analyst', analyst)
    try {
      nav(`/programs/${(await api.createProgramDemo(f)).id}`)
    } catch (err) {
      setDemoError((err as Error).message)
      setBusy(false)
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-bold text-navy">Program integrity</h1>
        <p className="mt-1 max-w-3xl text-sm text-slate-600">
          Screen a state's nutrition program files for the patterns behind recent fraud cases: sponsors claiming more meals than their sites can serve,
          new sites piled onto a few addresses, terminated sponsors or disqualified stores reopening under new names, and the same people across programs.
        </p>
      </div>

      <Card
        title="Try it on a simulated state"
        action={
          <Button onClick={loadDemo} disabled={busy}>
            {busy ? 'Loading…' : 'Load simulated demo'}
          </Button>
        }
      >
        <p className="text-sm text-slate-600">
          The State of Northland is fictional: 49 meal sponsors, 319 sites, 184 SNAP stores and 41 WIC vendors, with six planted cases modeled on public
          prosecutions, plus exclusion lists, court cases and open-source findings. Everything it produces is labeled simulated.
        </p>
        <ErrorNote error={demoError} />
      </Card>

      <Card title="Screens">
        {error ? (
          <ErrorNote error={error} />
        ) : !data ? (
          <Loading />
        ) : data.length === 0 ? (
          <p className="text-sm text-slate-500">No program screens yet. Load the simulated demo or upload a state's files below.</p>
        ) : (
          <table className="w-full text-sm">
            <thead className="text-left text-xs text-slate-500">
              <tr>
                <th className="py-1.5 font-medium">Screen</th>
                <th className="font-medium">Data</th>
                <th className="text-right font-medium">Sponsors</th>
                <th className="text-right font-medium">Sites</th>
                <th className="text-right font-medium">Stores and vendors</th>
                <th className="text-right font-medium">Leads</th>
                <th className="pl-4 font-medium">By</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {data.map((m: ProgramMeta) => (
                <tr key={m.id}>
                  <td className="py-2">
                    <Link to={`/programs/${m.id}`} className="font-medium text-navy hover:underline">
                      {m.label}
                    </Link>
                    <div className="text-xs text-slate-500">{new Date(m.created).toLocaleString()}</div>
                  </td>
                  <td>
                    <ProgramClassBadge dataClass={m.data_class} label={m.data_class_label} />
                  </td>
                  <td className="tabular text-right">{(m.counts.sponsor ?? 0).toLocaleString()}</td>
                  <td className="tabular text-right">{(m.counts.site ?? 0).toLocaleString()}</td>
                  <td className="tabular text-right">{((m.counts.retailer ?? 0) + (m.counts.vendor ?? 0)).toLocaleString()}</td>
                  <td className="tabular text-right font-semibold">{m.flagged}</td>
                  <td className="pl-4 text-slate-600">{m.analyst}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      <Card title="Screen a state's files">
        <NewProgram onDone={(id) => nav(`/programs/${id}`)} />
      </Card>
    </div>
  )
}
