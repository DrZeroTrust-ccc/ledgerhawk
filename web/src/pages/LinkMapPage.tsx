import { useParams } from 'react-router-dom'
import { api } from '../api'
import LinkMap from '../LinkMap'
import { Breadcrumbs, queueHref, runLabel, usePlace, useRuns } from '../nav'
import { useAsync } from '../ui'

// The link map at full width, with the details panel beside it.
export default function LinkMapPage() {
  const { id = '', uei = '' } = useParams()
  const { runs } = useRuns()
  const { data: v } = useAsync(() => api.vendor(id, uei), [id, uei])
  const name = v?.name ?? uei
  usePlace(v ? `${v.name} (link map)` : null)
  const record = `/runs/${id}/vendors/${encodeURIComponent(uei)}`
  return (
    <div className="space-y-4">
      <Breadcrumbs
        items={[
          { label: 'Imports', to: '/' },
          { label: runLabel(runs, id), to: `/runs/${id}` },
          { label: 'Queue', to: queueHref(id) },
          { label: name, to: `${record}?tab=map` },
          { label: 'Link map' },
        ]}
      />
      <h1 className="text-2xl font-semibold text-navy">Link map: {name}</h1>
      <LinkMap runId={id} uei={uei} full />
    </div>
  )
}
