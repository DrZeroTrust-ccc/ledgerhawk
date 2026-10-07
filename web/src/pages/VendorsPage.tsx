import { useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api } from '../api'
import { usePlace } from '../nav'
import { Card, ErrorNote, Loading, useAsync } from '../ui'

// Find any vendor by name or UEI, whatever run or subject screen it appears in; each result opens its vendor record.
export default function VendorsPage() {
  const [sp, setSp] = useSearchParams()
  const q = sp.get('q') ?? ''
  const [text, setText] = useState(q)
  usePlace('Vendors')
  const { data, error } = useAsync(() => (q.trim().length >= 2 ? api.searchVendors(q) : Promise.resolve({ rows: [] })), [q])
  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-semibold text-navy">Vendors</h1>
        <p className="mt-1 text-sm text-slate-600">Search every run and subject screen by name or UEI. A vendor opens on one record with everything known about it.</p>
      </div>
      <form
        role="search"
        className="flex flex-wrap gap-2"
        onSubmit={(e) => {
          e.preventDefault()
          setSp(text.trim() ? { q: text.trim() } : {})
        }}
      >
        <input
          autoFocus
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Company name or UEI"
          aria-label="Company name or UEI"
          className="w-full max-w-md rounded-md border border-slate-300 px-3 py-2 text-sm"
        />
        <button type="submit" className="rounded-md bg-navy px-4 py-2 text-sm font-medium text-white hover:bg-ink">
          Search
        </button>
      </form>
      <ErrorNote error={error} />
      {q.trim().length >= 2 && !data && !error && <Loading />}
      {data && q.trim().length >= 2 && (
        <Card>
          {data.rows.length === 0 ? (
            <p className="py-4 text-center text-sm text-slate-500">No vendor matches “{q}”.</p>
          ) : (
            <ul className="-my-2 divide-y divide-slate-100">
              {data.rows.map((r) => (
                <li key={r.uei} className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 py-2.5">
                  <span>
                    <Link to={`/vendors/${encodeURIComponent(r.uei)}`} className="font-medium text-navy hover:underline">
                      {r.name || r.uei}
                    </Link>{' '}
                    <span className="font-mono text-xs text-slate-500">{r.uei}</span>
                  </span>
                  <span className="text-xs text-slate-500">
                    {[r.runs ? `${r.runs} ${r.runs === 1 ? 'run' : 'runs'}${r.run ? `, newest ${r.run.label}` : ''}` : '', r.screens ? `${r.screens} subject ${r.screens === 1 ? 'screen' : 'screens'}` : '']
                      .filter(Boolean)
                      .join(' · ')}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Card>
      )}
    </div>
  )
}
