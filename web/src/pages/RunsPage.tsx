import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { lastPlace } from '../nav'
import { api, money, num, queueTotal, type AutoSources, type ImportJob, type RunMeta, type Source } from '../api'
import { ImportProgress, RecentImports, useImportJob, WeeklyRescreen } from '../imports'
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
        <select
          value={kind}
          onChange={(e) => setKind(e.target.value as 'sam' | 'exclusions')}
          className="rounded-md border border-slate-300 px-2 py-1 text-sm"
        >
          <option value="sam">SAM entity extract (V2 .dat)</option>
          <option value="exclusions">SAM exclusions extract (.csv)</option>
        </select>
      </label>
      <label className="space-y-1">
        <span className="block text-xs font-medium text-slate-600">File</span>
        <input
          type="file"
          accept={kind === 'sam' ? '.zip,.dat,.txt' : '.zip,.csv'}
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          className="text-sm"
        />
      </label>
      <label className="space-y-1">
        <span className="block text-xs font-medium text-slate-600">Extract date</span>
        <input
          type="date"
          required
          value={asOf}
          onChange={(e) => setAsOf(e.target.value)}
          className="rounded-md border border-slate-300 px-2 py-1 text-sm"
        />
      </label>
      <Button type="submit" variant="secondary" disabled={!file || !asOf || busy || !analyst.trim()}>
        {busy ? 'Uploading…' : 'Add source'}
      </Button>
      <ErrorNote error={error} />
    </form>
  )
}

const when = (iso?: string) => (iso ? new Date(iso).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }) : 'never')

function AutoFetch({ auto, reload }: { auto?: AutoSources; reload: () => void }) {
  const [analyst] = useAnalystName()
  const [error, setError] = useState<string | null>(null)
  const [asked, setAsked] = useState(false)
  if (!auto) return null
  if (!auto.enabled)
    return (
      <p className="mb-4 rounded-md bg-slate-50 px-3 py-2 text-sm text-slate-600">
        Automatic SAM.gov downloads are off. An admin turns them on by adding a SAM.gov API key as <code>SAM_API_KEY</code> in Render. Until then,
        upload the extracts below.
      </p>
    )
  const errors = (['exclusions', 'sam'] as const).map((k) => auto[k]?.error).filter(Boolean)
  const check = async () => {
    setError(null)
    const f = new FormData()
    f.set('analyst', analyst)
    try {
      await api.refreshSources(f)
      setAsked(true)
      setTimeout(reload, 15000)
    } catch (e) {
      setError((e as Error).message)
    }
  }
  return (
    <div className="mb-4 rounded-md bg-emerald-50 px-3 py-2 text-sm text-emerald-900">
      <div className="flex flex-wrap items-center gap-2">
        <span>Updated automatically from SAM.gov: exclusions daily, the entity file monthly. Last checked {when(auto.exclusions?.checked_at)}.</span>
        <Button variant="secondary" className="ml-auto" disabled={auto.running || asked || !analyst.trim()} onClick={check}>
          {auto.running || asked ? 'Checking SAM.gov…' : 'Check SAM.gov now'}
        </Button>
      </div>
      {(auto.running || asked) && (
        <p className="mt-1 text-xs">A new entity file is large, so it can take several minutes to download and index. Refresh this page to see it.</p>
      )}
      {errors.map((e) => (
        <p key={e} className="mt-1 text-xs text-crimson">
          {e}
        </p>
      ))}
      <ErrorNote error={error} />
    </div>
  )
}

function DataSources({ sources, auto, reload }: { sources: Source[] | null; auto?: AutoSources; reload: () => void }) {
  return (
    <Card title="Data sources">
      <AutoFetch auto={auto} reload={reload} />
      {!sources && <Loading />}
      {sources && sources.length === 0 && (
        <p className="mb-4 text-sm text-slate-500">No SAM or exclusions extracts loaded yet. Add them once here and every import can use them.</p>
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
                    <span
                      className={`ml-1 rounded px-1.5 py-0.5 text-xs ${s.stale ? 'bg-amber-50 text-amber-800' : 'bg-emerald-50 text-emerald-800'}`}
                    >
                      {s.age_days} {s.age_days === 1 ? 'day' : 'days'} old
                      {s.stale ? `, stale after ${s.stale_after_days}` : ''}
                    </span>
                  </td>
                  <td className="py-2 text-slate-600">
                    {s.file} · {sizeLabel(s.bytes)}
                    {s.kind === 'sam' && s.entities !== undefined && (
                      <span className={s.entities ? '' : 'text-crimson'}>
                        {' '}
                        · {s.entities ? `${s.entities.toLocaleString()} entities` : 'no entities could be read'}
                      </span>
                    )}
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

function UploadForm({ sources, runs }: { sources: Source[]; runs: RunMeta[] }) {
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
  const [follows, setFollows] = useState('')
  const packs = useAsync(() => api.policies(), [])
  const [pack, setPack] = useState('')
  // The chosen pack's freshness limits, checked against the extracts picked above.
  const packRules = useAsync(() => api.policy(pack || 'ledgerhawk-defaults'), [pack])
  const limits = packRules.data?.live_rules as { sam_stale_days?: number; exclusions_stale_days?: number } | undefined
  const stale: string[] = []
  const samPicked = samSources.find((s) => s.id === samSource)
  const exPicked = exclusions ? null : exSources.find((s) => s.id === exSource)
  if (limits && samPicked && limits.sam_stale_days !== undefined && samPicked.age_days > limits.sam_stale_days)
    stale.push(`The SAM extract is ${samPicked.age_days} days old (limit ${limits.sam_stale_days}).`)
  if (limits && exPicked && limits.exclusions_stale_days !== undefined && exPicked.age_days > limits.exclusions_stale_days)
    stale.push(`The exclusions extract is ${exPicked.age_days} days old (limit ${limits.exclusions_stale_days}).`)
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
    if (follows) f.append('follows', follows)
    if (pack) f.append('policy_pack', pack)
    try {
      const { id, job } = await api.createRun(f)
      if (id) nav(`/runs/${id}`)
      else setJob(job)
    } catch (err) {
      setError((err as Error).message)
      setBusy(false)
    }
  }
  // The import runs on the server; open it when it's done, or say why it failed.
  const [job, setJob] = useState<ImportJob | null>(null)
  const watched = useImportJob(job, (j) => {
    if (j.state === 'done') nav(`/runs/${j.run_id}`)
    else {
      setError(`The import didn’t finish: ${j.error}`)
      setBusy(false)
      setJob(null)
    }
  })

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
            <input type="file" accept=".zip,.csv" onChange={(e) => setExclusions(e.target.files?.[0] ?? null)} className={field} />
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
          <span className="block text-xs text-slate-500">
            Adds SAM cards, the relationship screen, and address and contact ties to excluded parties.
          </span>
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
        <label className="space-y-1.5">
          <span className="block text-sm font-medium">Follow-up to an earlier import?</span>
          <select
            value={follows}
            onChange={(e) => {
              setFollows(e.target.value)
              // a follow-up keeps the pack of the import it follows, unless the analyst picks another
              const prior = runs.find((r) => r.id === e.target.value)?.policy?.pack_id
              if (prior) setPack(prior === 'ledgerhawk-defaults' ? '' : prior)
            }}
            className="block max-w-xs rounded-md border border-slate-300 px-2 py-1 text-sm"
          >
            <option value="">No, this is a new list</option>
            {runs.map((r) => (
              <option key={r.id} value={r.id}>
                {r.label} · {r.created_at.slice(0, 10)}
              </option>
            ))}
          </select>
          <span className="block text-xs text-slate-500">A follow-up shows what changed and carries the earlier import's decisions, labeled.</span>
        </label>
        <label className="space-y-1.5">
          <span className="block text-sm font-medium">Policy pack</span>
          <select
            value={pack}
            onChange={(e) => setPack(e.target.value)}
            className="block max-w-xs rounded-md border border-slate-300 px-2 py-1 text-sm"
          >
            {(packs.data?.packs ?? []).map((p) => (
              <option key={p.id} value={p.locked ? '' : p.id}>
                {p.name} · v{p.live}
              </option>
            ))}
          </select>
          <span className="block text-xs text-slate-500">
            The rules this import is screened with.{' '}
            <Link to="/policies" className="text-navy underline">
              See the packs
            </Link>
          </span>
        </label>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={synthetic} onChange={(e) => setSynthetic(e.target.checked)} />
          This is synthetic or demo data
        </label>
        <Button type="submit" disabled={!vendors || busy || !analyst.trim()} className="ml-auto">
          {busy ? 'Importing…' : 'Import and screen'}
        </Button>
      </div>
      {watched && <ImportProgress job={watched} />}
      {stale.length > 0 && (
        <p className="rounded-md bg-amber-50 px-3 py-2 text-sm text-amber-900">
          {stale.join(' ')} The {packRules.data?.name ?? 'selected'} pack expects fresher data; you can still go ahead.
        </p>
      )}
      {!analyst.trim() && <p className="text-xs text-slate-500">Enter your name in the header so the import is attributed to you.</p>}
      <ErrorNote error={error} />
    </form>
  )
}

function PickUp() {
  const p = lastPlace()
  if (!p) return null
  return (
    <Link
      to={p.path}
      className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-navy-100 bg-navy-50 px-4 py-3 text-sm text-navy hover:border-navy"
    >
      <span>
        <span className="text-slate-500">Pick up where you left off:</span> <span className="font-medium">{p.label}</span>
      </span>
      <span className="font-medium">Continue →</span>
    </Link>
  )
}

export default function RunsPage() {
  const { data: runs, error } = useAsync(() => api.runs(), [])
  const sources = useAsync(() => api.sources(), [])
  return (
    <div className="space-y-6">
      <PickUp />
      <RecentImports />
      <div>
        <h1 className="text-2xl font-semibold text-navy">Imports</h1>
        <p className="mt-1 text-sm text-slate-600">
          Each import screens one vendor file against the SAM and exclusions extracts you pick. Imports never change after they finish, so their
          numbers stay reproducible.
        </p>
        <div className="mt-2">
          <WeeklyRescreen />
        </div>
      </div>
      <Card title="Start a new import">
        <UploadForm key={sources.data ? 'loaded' : 'loading'} sources={sources.data?.sources ?? []} runs={runs ?? []} />
      </Card>
      <DataSources sources={sources.data?.sources ?? null} auto={sources.data?.auto} reload={sources.reload} />
      <Card title="Previous imports">
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
                        {r.follows_id && (
                          <div className="text-xs text-slate-500">
                            Follow-up to {runs.find((x) => x.id === r.follows_id)?.label ?? r.follows_id} of{' '}
                            {runs.find((x) => x.id === r.follows_id)?.created_at.slice(0, 10) ?? 'an earlier import'}
                          </div>
                        )}
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
