import { Link } from 'react-router-dom'
import { api } from '../api'
import { Card, ErrorNote, Loading, useAsync } from '../ui'

const ACTION_LABEL: Record<string, string> = {
  run_created: 'Import started',
  disposition: 'Disposition',
  restored: 'Restored to queue',
}

export default function AuditPage() {
  const { data, error } = useAsync(() => api.audit(), [])
  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-semibold text-navy">Audit log</h1>
        <p className="mt-1 text-sm text-slate-600">Every import, restore and disposition, with who did it and when.</p>
      </div>
      <Card>
        <ErrorNote error={error} />
        {!data && !error && <Loading />}
        {data && data.length === 0 && <p className="text-sm text-slate-500">Nothing yet.</p>}
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
                    <td className="px-3 py-2">{h.analyst}</td>
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
          </div>
        )}
      </Card>
    </div>
  )
}
