import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, type PolicyChange } from '../api'
import { useAnalystName } from '../App'
import { Breadcrumbs, usePlace } from '../nav'
import { formatSetting, SETTINGS, settingLabel } from '../policyLabels'
import { Button, Card, ErrorNote, Loading, useAsync } from '../ui'
import { useCanManagePolicies } from './PoliciesPage'

function Change({ c }: { c: PolicyChange }) {
  if (c.added || c.removed)
    return (
      <li>
        {settingLabel(c.key)}: {c.added?.length ? `added ${c.added.join(', ')}` : ''}
        {c.added?.length && c.removed?.length ? '; ' : ''}
        {c.removed?.length ? `removed ${c.removed.join(', ')}` : ''}
      </li>
    )
  return (
    <li>
      {settingLabel(c.key)}: <s className="text-slate-500">{formatSetting(c.key, c.from)}</s> → <strong>{formatSetting(c.key, c.to)}</strong>
    </li>
  )
}

export default function PolicyPage() {
  const { id = '' } = useParams()
  const [analyst] = useAnalystName()
  const canManage = useCanManagePolicies()
  const { data: p, error, reload } = useAsync(() => api.policy(id), [id])
  usePlace(p ? `${p.name} (policy pack)` : null)
  const [edit, setEdit] = useState<{
    name: string
    description: string
  } | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [showAll, setShowAll] = useState(false)
  if (error) return <ErrorNote error={error} />
  if (!p) return <Loading />

  const groups: Record<string, [string, unknown][]> = {}
  for (const [k, v] of Object.entries(p.live_rules)) (groups[SETTINGS[k]?.group ?? 'Other'] ??= []).push([k, v])

  return (
    <div className="space-y-6">
      <Breadcrumbs items={[{ label: 'Policies', to: '/policies' }, { label: p.name }]} />
      {edit ? (
        <form
          className="space-y-2 rounded-lg border border-slate-200 bg-white p-4"
          onSubmit={async (e) => {
            e.preventDefault()
            const f = new FormData()
            f.append('name', edit.name)
            f.append('description', edit.description)
            f.append('analyst', analyst)
            try {
              await api.describePolicy(p.id, f)
              setEdit(null)
              setErr(null)
              reload()
            } catch (x) {
              setErr((x as Error).message)
            }
          }}
        >
          <input
            aria-label="Pack name"
            value={edit.name}
            onChange={(e) => setEdit({ ...edit, name: e.target.value })}
            className="w-full rounded-md border border-slate-300 px-2 py-1.5 text-lg font-semibold"
          />
          <input
            aria-label="What it's for"
            value={edit.description}
            onChange={(e) => setEdit({ ...edit, description: e.target.value })}
            className="w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm"
          />
          <div className="flex gap-2">
            <Button type="submit">Save</Button>
            <Button type="button" variant="secondary" onClick={() => setEdit(null)}>
              Cancel
            </Button>
          </div>
          <ErrorNote error={err} />
        </form>
      ) : (
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h1 className="text-2xl font-semibold text-navy">{p.name}</h1>
            {p.description && <p className="mt-1 text-sm text-slate-600">{p.description}</p>}
            <p className="mt-1 text-xs text-slate-500">
              {p.locked
                ? 'Built in and read-only: the rules LedgerHawk ships with.'
                : `Created by ${p.created_by} on ${p.created_at.slice(0, 10)}` +
                  (p.copied_from ? `, copied from ${p.copied_from.pack_name} v${p.copied_from.version}` : '')}
            </p>
          </div>
          {canManage && !p.locked && (
            <Button variant="secondary" onClick={() => setEdit({ name: p.name, description: p.description })}>
              Rename
            </Button>
          )}
        </div>
      )}

      <div className="grid gap-6 lg:grid-cols-3">
        <div className="space-y-6 lg:col-span-2">
          {!p.locked && (
            <Card title={`How v${p.live} differs from LedgerHawk defaults`}>
              {p.vs_defaults.length === 0 ? (
                <p className="text-sm text-slate-600">No differences: this version screens exactly like LedgerHawk defaults.</p>
              ) : (
                <ul className="list-disc space-y-1 pl-5 text-sm">
                  {p.vs_defaults.map((c) => (
                    <Change key={c.key} c={c} />
                  ))}
                </ul>
              )}
            </Card>
          )}
          <Card
            title={`All rules in v${p.live}`}
            action={
              <Button variant="ghost" onClick={() => setShowAll(!showAll)}>
                {showAll ? 'Hide' : 'Show'}
              </Button>
            }
          >
            {!showAll ? (
              <p className="text-sm text-slate-500">
                {Object.keys(p.live_rules).length} settings. Plain-English sentences you can edit come in the next step.
              </p>
            ) : (
              <div className="space-y-4">
                {Object.entries(groups).map(([g, items]) => (
                  <div key={g}>
                    <h3 className="text-xs font-semibold tracking-wide text-slate-500 uppercase">{g}</h3>
                    <dl className="mt-1 divide-y divide-slate-100 text-sm">
                      {items.map(([k, v]) => (
                        <div key={k} className="flex justify-between gap-4 py-1.5">
                          <dt className="text-slate-700">{settingLabel(k)}</dt>
                          <dd className="tabular shrink-0 font-medium" title={Array.isArray(v) ? (v as string[]).join(', ') : undefined}>
                            {formatSetting(k, v)}
                          </dd>
                        </div>
                      ))}
                    </dl>
                  </div>
                ))}
              </div>
            )}
          </Card>
        </div>
        <div className="space-y-6">
          <Card title="Versions">
            <ol className="space-y-3 text-sm">
              {p.versions.map((v) => (
                <li key={v.n}>
                  <div className="flex items-center gap-2">
                    <span className="font-semibold">v{v.n}</span>
                    <span
                      className={`rounded px-1.5 py-0.5 text-xs font-medium ${v.status === 'live' ? 'bg-emerald-50 text-emerald-800' : v.status === 'draft' ? 'bg-amber-50 text-amber-800' : 'bg-slate-100 text-slate-600'}`}
                    >
                      {v.status === 'live' ? 'Live' : v.status === 'draft' ? 'Draft' : 'Retired'}
                    </span>
                  </div>
                  <div className="text-slate-700">{v.reason}</div>
                  <div className="text-xs text-slate-500">
                    {v.created_by}
                    {v.approved_by ? `, approved by ${v.approved_by}` : ''}
                    {v.at ? ` · ${v.at.slice(0, 10)}` : ''}
                  </div>
                </li>
              ))}
            </ol>
          </Card>
          <Card title="Imports screened with this pack">
            {p.imports.length === 0 ? (
              <p className="text-sm text-slate-500">None yet. Pick this pack when you start an import.</p>
            ) : (
              <ul className="space-y-1.5 text-sm">
                {p.imports.map((i) => (
                  <li key={i.id}>
                    <Link to={`/runs/${i.id}`} className="text-navy hover:underline">
                      {i.label}
                    </Link>{' '}
                    <span className="text-xs text-slate-500">
                      {i.created_at.slice(0, 10)} · v{i.version}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </div>
      </div>
    </div>
  )
}
