import { Navigate, useParams } from 'react-router-dom'
import { api } from '../api'
import { Breadcrumbs, usePlace } from '../nav'
import { ErrorNote, Loading, useAsync } from '../ui'
import { MoneyByYear, ScreenEvidence } from '../VendorRecord'

// /vendors/<UEI>: the vendor record. A vendor in any run opens on the newest run's record (decisions, notes, sign-off);
// a vendor only on subject screens shows what those screens found.
export default function VendorLookupPage() {
  const { uei = '' } = useParams()
  const { data, error } = useAsync(() => api.vendorWhere(uei), [uei])
  usePlace(data ? `${data.name || data.uei} (vendor)` : null)
  if (error) return <ErrorNote error={error} />
  if (!data) return <Loading />
  if (data.runs.length) return <Navigate replace to={`/runs/${data.runs[0].id}/vendors/${encodeURIComponent(data.uei)}`} />
  return (
    <div className="space-y-6">
      <Breadcrumbs items={[{ label: 'Vendors', to: '/vendors' }, { label: data.name || data.uei }]} />
      <div>
        <h1 className="text-2xl font-semibold text-navy">{data.name || data.uei}</h1>
        <p className="mt-1 font-mono text-sm text-slate-500">UEI {data.uei}</p>
        <p className="mt-2 text-sm text-slate-600">
          This vendor is not in any import yet, so it has no tier or decision here. Below is what subject screens found about it.
        </p>
      </div>
      <MoneyByYear uei={data.uei} screens={data.screens} />
      <ScreenEvidence screens={data.screens} />
    </div>
  )
}
