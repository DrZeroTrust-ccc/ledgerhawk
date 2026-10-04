import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api, SUBJECT_STATUS, type RunMeta, type Source } from '../api'
import { useAnalystName } from '../App'
import { Button, Card, DataClassBadge, ErrorNote, Loading, useAsync } from '../ui'

const field = 'w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm'

function NewScreen({ sources, runs }: { sources: Source[]; runs: RunMeta[] }) {
  const [analyst] = useAnalystName()
  const nav = useNavigate()
  const samSources = sources.filter((s) => s.kind === 'sam')
  const exSources = sources.filter((s) => s.kind === 'exclusions')
  const [matter, setMatter] = useState('')
  const [client, setClient] = useState('')
  const [privileged, setPrivileged] = useState(true)
  const [text, setText] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [samSource, setSamSource] = useState(samSources[0]?.id ?? '')
  const [exSource, setExSource] = useState(exSources[0]?.id ?? '')
  const [dollarsRun, setDollarsRun] = useState('')
  const [synthetic, setSynthetic] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    const f = new FormData()
    f.append('subjects_text', text)
    if (file) f.append('subjects_file', file)
    f.append('analyst', analyst)
    f.append('matter', matter)
    f.append('client', client)
    f.append('privileged', String(privileged))
    f.append('synthetic', String(synthetic))
    f.append('sam_source', samSource)
    f.append('exclusions_source', exSource)
    f.append('dollars_run', dollarsRun)
    try {
      const { id } = await api.createSubjectScreen(f)
      nav(`/subjects/${id}`)
    } catch (err) {
      setError((err as Error).message)
      setBusy(false)
    }
  }

  const noSources = samSources.length === 0 && exSources.length === 0
  return (
    <Card title="Screen named subjects">
      <p className="mb-4 max-w-3xl text-sm text-slate-600">
        Check a target and its affiliates, or a client's supplier list, against the SAM exclusions list and SAM registrations. Each subject is
        screened whatever its size, along with other registrations that share a contact, a suite or a legal name with it.
      </p>
      {noSources ? (
        <p className="text-sm text-slate-500">
          Add a SAM entity extract or an exclusions extract on the <Link to="/" className="text-navy underline">Runs</Link> page first.
        </p>
      ) : (
        <form onSubmit={submit} className="grid gap-4 md:grid-cols-2">
          <label className="space-y-1">
            <span className="block text-xs font-medium text-slate-600">Matter</span>
            <input value={matter} onChange={(e) => setMatter(e.target.value)} placeholder="Matter name or number" className={field} />
          </label>
          <label className="space-y-1">
            <span className="block text-xs font-medium text-slate-600">Client</span>
            <input value={client} onChange={(e) => setClient(e.target.value)} placeholder="Client or instructing counsel" className={field} />
          </label>
          <label className="space-y-1 md:col-span-2">
            <span className="block text-xs font-medium text-slate-600">Subjects, one per line: a UEI, a company name, or "UEI, name"</span>
            <textarea value={text} onChange={(e) => setText(e.target.value)} rows={6} className={`${field} font-mono`} />
          </label>
          <label className="space-y-1">
            <span className="block text-xs font-medium text-slate-600">Or upload a list (CSV or XLSX with UEI and/or Name columns; Role is optional)</span>
            <input type="file" accept=".csv,.xlsx" onChange={(e) => setFile(e.target.files?.[0] ?? null)} className="text-sm" />
          </label>
          <label className="space-y-1">
            <span className="block text-xs font-medium text-slate-600">Dollars from a run (optional)</span>
            <select value={dollarsRun} onChange={(e) => setDollarsRun(e.target.value)} className={field}>
              <option value="">No dollars</option>
              {runs.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.label} · {r.created_at.slice(0, 10)}
                  {r.data_class === 'synthetic' ? ' · synthetic' : ''}
                </option>
              ))}
            </select>
          </label>
          <label className="space-y-1">
            <span className="block text-xs font-medium text-slate-600">SAM entity extract</span>
            <select value={samSource} onChange={(e) => setSamSource(e.target.value)} className={field}>
              <option value="">Don't use</option>
              {samSources.map((s) => (
                <option key={s.id} value={s.id}>
                  As of {s.as_of} · {s.file}
                </option>
              ))}
            </select>
          </label>
          <label className="space-y-1">
            <span className="block text-xs font-medium text-slate-600">Exclusions extract</span>
            <select value={exSource} onChange={(e) => setExSource(e.target.value)} className={field}>
              <option value="">Don't use</option>
              {exSources.map((s) => (
                <option key={s.id} value={s.id}>
                  As of {s.as_of} · {s.file}
                </option>
              ))}
            </select>
          </label>
          <div className="flex flex-wrap items-center gap-x-6 gap-y-2 md:col-span-2">
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={privileged} onChange={(e) => setPrivileged(e.target.checked)} />
              Mark exports "Privileged and Confidential, prepared at the direction of counsel"
            </label>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={synthetic} onChange={(e) => setSynthetic(e.target.checked)} />
              Synthetic data
            </label>
          </div>
          <div className="flex flex-wrap items-center gap-3 md:col-span-2">
            <Button type="submit" disabled={busy || !analyst.trim() || (!text.trim() && !file) || (!samSource && !exSource)}>
              {busy ? 'Screening…' : 'Screen subjects'}
            </Button>
            {!analyst.trim() && <span className="text-xs text-slate-500">Enter your name in the header first.</span>}
            <ErrorNote error={error} />
          </div>
        </form>
      )}
    </Card>
  )
}

export default function SubjectsPage() {
  const sources = useAsync(() => api.sources(), [])
  const runs = useAsync(() => api.runs(), [])
  const screens = useAsync(() => api.subjectScreens(), [])
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold text-ink">Subject screen</h1>
        <p className="text-sm text-slate-500">For investigations and diligence on named companies.</p>
      </div>
      <ErrorNote error={sources.error || runs.error || screens.error} />
      {sources.data && runs.data ? <NewScreen sources={sources.data.sources} runs={runs.data} /> : <Loading />}
      <Card title="Past subject screens">
        {!screens.data && <Loading />}
        {screens.data && screens.data.length === 0 && <p className="text-sm text-slate-500">No subject screens yet.</p>}
        {screens.data && screens.data.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-xs text-slate-500">
                <tr>
                  <th className="pb-2 font-medium">Matter</th>
                  <th className="pb-2 font-medium">Subjects</th>
                  <th className="pb-2 font-medium">Excluded or tied</th>
                  <th className="pb-2 font-medium">Sources as of</th>
                  <th className="pb-2 font-medium">By</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {screens.data.map((s) => (
                  <tr key={s.id}>
                    <td className="py-2">
                      <Link to={`/subjects/${s.id}`} className="font-medium text-navy hover:underline">
                        {s.matter || 'Untitled matter'}
                      </Link>
                      {s.client && <span className="text-slate-500"> · {s.client}</span>} <DataClassBadge dataClass={s.data_class} />
                    </td>
                    <td className="tabular py-2">{s.counts.subjects}</td>
                    <td className="tabular py-2" title={`${SUBJECT_STATUS.excluded}, ${SUBJECT_STATUS.tied}`}>
                      {(s.counts.excluded ?? 0) + (s.counts.tied ?? 0)}
                    </td>
                    <td className="py-2 text-slate-600">
                      {[s.sources.sam_extract_date && `SAM ${s.sources.sam_extract_date}`, s.sources.exclusions_extract_date && `exclusions ${s.sources.exclusions_extract_date}`]
                        .filter(Boolean)
                        .join(' · ')}
                    </td>
                    <td className="py-2 text-slate-600">
                      {s.created_by} · {s.created_at.slice(0, 10)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  )
}
