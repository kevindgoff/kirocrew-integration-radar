import { useEffect, useState } from 'react'
import { useAppApi } from '@kirocrew/app-sdk'
import { Badge, Card, CardTitle, PageHeader } from '@kirocrew/app-sdk/ui'

const API = '/apps/integration-radar/api'

interface Status {
  app: string
  version: string
  dataDir: string
}

export default function IntegrationRadar() {
  const api = useAppApi()
  const [status, setStatus] = useState<Status | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api
      .get<Status>(`${API}/status`)
      .then(setStatus)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)))
  }, [api])

  return (
    <>
      <PageHeader title="Integration Radar" subtitle="Partner API changes mapped to the connectors you use" />
      <div className="px-6 pb-8 overflow-y-auto flex-1 min-h-0">
        <Card>
          <CardTitle>Backend</CardTitle>
          {error ? (
            <Badge variant="err">Unreachable: {error}</Badge>
          ) : status ? (
            <p className="text-sm">
              <Badge variant="ok">Connected</Badge> {status.app} v{status.version}
            </p>
          ) : (
            <p className="text-sm text-muted">Checking…</p>
          )}
        </Card>
      </div>
    </>
  )
}
