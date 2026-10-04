import { useCallback, useEffect, useMemo, useState } from 'react'
import { useAppApi, useNavBadge, useNotify } from '@kirocrew/app-sdk'
import { Badge, Btn, PageHeader, SegmentedControl } from '@kirocrew/app-sdk/ui'
import { RefreshCw } from 'lucide-react'
import { errorText, radar, type Summary } from './api'
import { Feed } from './Feed'
import { Calendar } from './Calendar'
import { SetupTab } from './Setup'

type Tab = 'feed' | 'calendar' | 'setup'

export default function IntegrationRadar() {
  const api = useAppApi()
  const r = useMemo(() => radar(api), [api])
  const notify = useNotify()
  const setBadge = useNavBadge()
  const [tab, setTab] = useState<Tab>('feed')
  const [summary, setSummary] = useState<Summary | null>(null)
  const [sweeping, setSweeping] = useState(false)
  // Bumped after anything that changes the data, so every tab refetches.
  const [version, setVersion] = useState(0)
  const refresh = useCallback(() => setVersion((v) => v + 1), [])

  useEffect(() => {
    r.summary()
      .then((s) => {
        setSummary(s)
        setBadge(s.untriaged)
      })
      .catch(() => setSummary(null))
  }, [r, version, setBadge])

  const sweep = async () => {
    setSweeping(true)
    try {
      const report = await r.sweep()
      if (report.status === 'running' || !report.outcomes) {
        notify('Sweep is still running; the feed refreshes when it finishes')
        setTimeout(refresh, 20000)
      } else {
        const o = report.outcomes
        notify(`Sweep done: ${o.new ?? 0} new, ${o.updated ?? 0} updated, ${o.merged ?? 0} merged`)
      }
      refresh()
    } catch (e) {
      notify(`Sweep failed: ${errorText(e)}`)
    } finally {
      setSweeping(false)
    }
  }

  const subtitle = summary
    ? `${summary.untriaged} to triage` +
      (summary.urgent ? `, ${summary.urgent} urgent` : '') +
      (summary.lastSweepAt ? ` · last sweep ${summary.lastSweepAt.replace('T', ' ').slice(0, 16)} UTC` : '')
    : 'Partner API changes mapped to the connectors you use'

  return (
    <>
      <PageHeader
        title="Integration Radar"
        subtitle={subtitle}
        actions={
          <div className="flex items-center gap-2">
            {summary?.failedLessons ? (
              <Btn
                title="Some dismissal lessons could not be saved to Crew; retry"
                onClick={async () => {
                  try {
                    await r.deliverLessons()
                    notify('Saving lessons to Crew…')
                    setTimeout(refresh, 15000)
                  } catch (e) {
                    notify(errorText(e))
                  }
                }}
              >
                {summary.failedLessons} lesson{summary.failedLessons === 1 ? '' : 's'} not saved {'\u00b7'} retry
              </Btn>
            ) : null}
            {summary?.stalePartners.length ? (
              <Badge variant="warn" title="A source failed twice in a row; see Setup">
                stale: {summary.stalePartners.join(', ')}
              </Badge>
            ) : null}
            <Btn onClick={sweep} disabled={sweeping || summary?.manifestLoaded === false}>
              <RefreshCw size={14} className={sweeping ? 'animate-spin' : ''} />
              {sweeping ? 'Sweeping…' : 'Sweep now'}
            </Btn>
          </div>
        }
      />
      <div className="px-6 pb-8 overflow-y-auto flex-1 min-h-0">
        <div className="mb-4">
          <SegmentedControl<Tab>
            segments={[
              { key: 'feed', label: 'Feed', count: summary?.untriaged },
              { key: 'calendar', label: 'Calendar' },
              { key: 'setup', label: 'Setup' },
            ]}
            value={tab}
            onChange={setTab}
            collapse={false}
          />
        </div>
        {tab === 'feed' && <Feed r={r} version={version} onChanged={refresh} goToSetup={() => setTab('setup')} />}
        {tab === 'calendar' && <Calendar r={r} version={version} />}
        {tab === 'setup' && <SetupTab r={r} version={version} onChanged={refresh} />}
      </div>
    </>
  )
}
