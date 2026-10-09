import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import { Card, ErrorNote, Loading, useAsync } from '../ui'

const ACTION_LABEL: Record<string, string> = {
  run_created: 'Import started',
  disposition: 'Disposition',
  restored: 'Restored to queue',
  view: 'Viewed',
  download: 'Downloaded',
  export: 'Exported',
  person: 'People',
  backup: 'Backup',
}

type Filters = { person: string; action: string; since: string; until: string }
const EMPTY: Filters = { person: '', action: '', since: '', until: '' }

const query = (f: Filters) => new URLSearchParams(Object.entries(f).filter(([, v]) => v) as [string, string][]).toString()

export default function AuditPage() {
  const [form, setForm] = useState<Filters>(EMPTY)
  const [applied, setApplied] = useState<Filters>(EMPTY)
  const q = query(applied)
  const { data, error } = useAsync(() => api.audit(q), [q])
  const actions = useAsync(() => api.auditActions(), [])
  const filtered = q !== ''
  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-semibold text-navy">Audit log</h1>
        <p className="mt-1 text-sm text-slate-600">
          Every import, decision, policy change and change to who has access, plus who opened which vendor or subject screen and who downloaded which
          file, with their sign-in email and the time.
        </p>
      </div>
      <Card>
        <form
          className="flex flex-wrap items-end gap-3 text-sm"
          onSubmit={(e) => {
            e.preventDefault()
            setApplied(form)
          }}
        >
          <label className="space-y-1">
            <span className="block font-medium">Who</span>
            <input
              value={form.person}
              placeholder="Name or email"
              onChange={(e) => setForm({ ...form, person: e.target.value })}
              className="w-48 rounded-md border border-slate-300 px-2 py-1.5"
            />
          </label>
          <label className="space-y-1">
            <span className="block font-medium">What</span>
            <select
              value={form.action}
              onChange={(e) => setForm({ ...form, action: e.target.value })}
              className="rounded-md border border-slate-300 px-2 py-1.5"
            >
              <option value="">Everything</option>
              {(actions.data?.actions ?? []).map((a) => (
                <option key={a} value={a}>
                  {ACTION_LABEL[a] ?? a}
                </option>
              ))}
            </select>
          </label>
          <label className="space-y-1">
            <span className="block font-medium">From</span>
            <input
              type="date"
              value={form.since}
              onChange={(e) => setForm({ ...form, since: e.target.value })}
              className="rounded-md border border-slate-300 px-2 py-1.5"
            />
          </label>
          <label className="space-y-1">
            <span className="block font-medium">To</span>
            <input
              type="date"
              value={form.until}
              onChange={(e) => setForm({ ...form, until: e.target.value })}
              className="rounded-md border border-slate-300 px-2 py-1.5"
            />
          </label>
          <button type="submit" className="rounded-md bg-navy px-3 py-1.5 font-medium text-white hover:bg-ink">
            Show
          </button>
          {filtered && (
            <button
              type="button"
              className="text-navy underline"
              onClick={() => {
                setForm(EMPTY)
                setApplied(EMPTY)
              }}
            >
              Clear
            </button>
          )}
          <a
            href={`/api/audit.csv${filtered ? `?${q}` : ''}`}
            className="ml-auto rounded-md bg-white px-3 py-1.5 font-medium text-navy ring-1 ring-slate-300 hover:bg-slate-50"
            title="Admins and Executives. Up to 100,000 entries, with the filters shown."
          >
            Export CSV
          </a>
        </form>
      </Card>
      <Card>
        <ErrorNote error={error} />
        {!data && !error && <Loading />}
        {data && data.length === 0 && <p className="text-sm text-slate-500">{filtered ? 'Nothing matches.' : 'Nothing yet.'}</p>}
        {data && data.length > 0 && (
          <div className="-mx-5 -my-5 overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-slate-50 text-left text-xs text-slate-500">
                <tr>
                  <th className="px-5 py-2 font-medium">When</th>
                  <th className="px-3 py-2 font-medium">Who</th>
                  <th className="px-3 py-2 font-medium">Action</th>
                  <th className="px-5 py-2 font-medium">Detail</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {data.map((h, i) => (
                  <tr key={i} className="align-top">
                    <td className="whitespace-nowrap px-5 py-2 text-slate-600">{new Date(h.at).toLocaleString()}</td>
                    <td className="px-3 py-2">
                      {h.analyst}
                      {h.email && h.email !== h.analyst && <span className="block text-xs text-slate-500">{h.email}</span>}
                    </td>
                    <td className="px-3 py-2">{ACTION_LABEL[h.action] ?? h.action}</td>
                    <td className="px-5 py-2">
                      {h.uei && h.run_id ? (
                        <Link className="font-mono text-xs text-navy hover:underline" to={`/runs/${h.run_id}/vendors/${h.uei}`}>
                          {h.uei}
                        </Link>
                      ) : h.run_id ? (
                        <Link className="font-mono text-xs text-navy hover:underline" to={`/runs/${h.run_id}`}>
                          {h.run_id}
                        </Link>
                      ) : null}{' '}
                      {h.detail}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {data.length >= 200 && !filtered && (
              <p className="px-5 py-3 text-xs text-slate-500">Showing the newest 200. Filter, or export the CSV for everything.</p>
            )}
          </div>
        )}
      </Card>
    </div>
  )
}
