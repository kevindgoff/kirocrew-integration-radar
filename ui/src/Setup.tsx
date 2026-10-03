import { useEffect, useState } from 'react'
import { useNotify } from '@kirocrew/app-sdk'
import { Badge, Btn, Card, CardTitle, Input, Toggle } from '@kirocrew/app-sdk/ui'
import { RefreshCw } from 'lucide-react'
import {
  errorText,
  type Candidate,
  type RadarApi,
  type RoleState,
  type Schedule,
  type Setup,
  type SourceHealth,
} from './api'

const ROLE_LABEL: Record<RoleState['role'], string> = { tracker: 'Tracker', wiki: 'Wiki', chat: 'Chat', repo: 'Repo' }
const STATUS_VARIANT: Record<RoleState['status'], 'ok' | 'err' | 'warn' | 'aim' | 'muted'> = {
  bound: 'ok',
  degraded: 'err',
  proposed: 'aim',
  needs_choice: 'warn',
  off: 'muted',
}
const STATUS_LABEL: Record<RoleState['status'], string> = {
  bound: 'bound',
  degraded: 'degraded',
  proposed: 'proposed',
  needs_choice: 'choose one',
  off: 'off',
}
const DAYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']
const selectClass = 'bg-bg-elevated border border-border rounded-md px-2 py-1.5 text-sm text-text'

export function SetupTab({ r, version, onChanged }: { r: RadarApi; version: number; onChanged: () => void }) {
  const notify = useNotify()
  const [setup, setSetup] = useState<Setup | null>(null)
  const [sources, setSources] = useState<SourceHealth[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [detecting, setDetecting] = useState(false)

  useEffect(() => {
    r.setup().then(setSetup).catch((e) => setError(errorText(e)))
    r.sources().then((s) => setSources(s.sources)).catch(() => setSources([]))
  }, [r, version])

  const detect = async () => {
    setDetecting(true)
    try {
      setSetup(await r.detect())
    } catch (e) {
      notify(`Detection failed: ${errorText(e)}`)
    } finally {
      setDetecting(false)
    }
  }

  const bind = async (role: string, body: unknown) => {
    try {
      setSetup(await r.bind(role, body))
      onChanged()
    } catch (e) {
      notify(errorText(e))
    }
  }

  if (error) return <p className="text-sm text-danger">{error}</p>
  if (!setup) return <p className="text-sm text-muted">Loading…</p>

  return (
    <div className="grid gap-4">
      <ManifestCard r={r} setup={setup} onLoaded={(s) => { setSetup(s); onChanged() }} />

      <Card>
        <div className="flex items-center justify-between mb-2">
          <CardTitle>Tools</CardTitle>
          <Btn onClick={detect} disabled={detecting}>
            <RefreshCw size={14} className={detecting ? 'animate-spin' : ''} /> Re-detect
          </Btn>
        </div>
        <p className="text-sm text-muted mb-3">
          Roles bind only to MCP servers and messaging interfaces already configured in Crew. Every role is
          optional; with none bound, this dashboard is the fallback.
        </p>
        {setup.detection.errors.length > 0 && (
          <ul className="text-sm text-danger mb-3 list-disc pl-5">
            {setup.detection.errors.map((e) => (
              <li key={e}>{e}</li>
            ))}
          </ul>
        )}
        <div className="grid gap-3">
          {(Object.keys(ROLE_LABEL) as RoleState['role'][]).map((role) => (
            <RoleRow key={role} r={r} state={setup.roles[role]} onBind={(body) => bind(role, body)} />
          ))}
        </div>
      </Card>

      <ScheduleCard r={r} setup={setup} onSaved={(scheduler) => setSetup({ ...setup, scheduler })} />
      <SourcesCard sources={sources} />
    </div>
  )
}

function ManifestCard({ r, setup, onLoaded }: { r: RadarApi; setup: Setup; onLoaded: (s: Setup) => void }) {
  const notify = useNotify()
  const m = setup.manifest
  const [path, setPath] = useState(setup.manifestPath ?? m.inUse?.path ?? '')
  const [busy, setBusy] = useState(false)

  const load = async () => {
    setBusy(true)
    try {
      const status = await r.loadManifest(path.trim())
      onLoaded({ ...setup, manifest: status, manifestPath: status.pathSaved ? path.trim() : setup.manifestPath })
      notify(status.lastAttempt?.ok ? 'Connector manifest loaded' : 'The manifest has errors; see below')
    } catch (e) {
      notify(errorText(e))
    } finally {
      setBusy(false)
    }
  }

  const last = m.lastAttempt
  return (
    <Card>
      <CardTitle>Connector manifest</CardTitle>
      <div className="flex gap-2 mb-2">
        <Input
          value={path}
          onChange={(e) => setPath(e.target.value)}
          placeholder="/absolute/path/to/integration-radar.manifest.yaml"
          aria-label="Connector manifest path"
        />
        <Btn primary onClick={load} disabled={busy || !path.trim()}>
          {busy ? 'Loading…' : 'Load'}
        </Btn>
      </div>
      <p className="text-[12px] text-muted mb-3">Every sweep re-reads this file. If it becomes invalid, the last valid manifest stays in use.</p>

      {m.inUse ? (
        <div className="text-sm">
          <div>
            In use: <code className="text-[12px]">{m.inUse.path}</code> {'·'} {m.inUse.partners.length} partners,{' '}
            {m.inUse.connectors.length} connectors
          </div>
          <div className="text-muted text-[12px] mt-1">Allowed hosts: {m.inUse.allowedHosts.join(', ')}</div>
          <ul className="mt-2 grid gap-1">
            {m.inUse.connectors.map((c) => (
              <li key={c.id} className="text-[13px]">
                <code className="text-[12px]">{c.id}</code>{' '}
                <span className="text-muted">
                  {c.partner} {c.version} {'·'} {c.criticality}
                </span>{' '}
                {c.lifecycle !== 'active' && <Badge variant="warn">{c.action}</Badge>}
              </li>
            ))}
          </ul>
        </div>
      ) : (
        <p className="text-sm text-muted">No connector manifest loaded yet. Start from examples/integration-radar.manifest.yaml.</p>
      )}

      {m.usingFallback && (
        <p className="text-sm text-warn mt-3">The latest version of the file is invalid; the last valid manifest is still in use.</p>
      )}
      {last && last.errors.length > 0 && (
        <div className="mt-3">
          <div className="text-sm font-medium text-danger mb-1">
            {last.errors.length} error{last.errors.length === 1 ? '' : 's'} in {last.path}
          </div>
          <ul className="grid gap-0.5 font-mono text-[12px]">
            {last.errors.map((e, i) => (
              <li key={i}>
                <span className="text-danger">{e.line ? `line ${e.line}` : 'file'}</span>
                {e.path ? ` ${e.path}` : ''}: {e.message}
              </li>
            ))}
          </ul>
        </div>
      )}
      {last && last.notices.length > 0 && (
        <ul className="mt-2 grid gap-0.5 text-[12px] text-muted">
          {last.notices.map((n, i) => (
            <li key={i}>
              line {n.line}: {n.message}
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}

function candidateLabel(c: Candidate): string {
  return c.kind === 'interface' ? `${c.name} (Crew interface)` : `${c.name} (MCP server)`
}

function RoleRow({ r, state, onBind }: { r: RadarApi; state: RoleState; onBind: (body: unknown) => Promise<void> }) {
  const notify = useNotify()
  const usable = state.candidates.filter((c) => c.usable && c.deliverable)
  const bound = state.binding?.candidate
  const [choice, setChoice] = useState<string>(bound ?? state.proposal ?? usable[0]?.id ?? '')
  const [destinations, setDestinations] = useState<{ id: string; name: string }[] | null>(null)
  const [destination, setDestination] = useState<string>(state.binding?.destination?.id ?? '')

  // Chat destinations always come from the live list for the chosen interface.
  useEffect(() => {
    setDestinations(null)
    if (state.role !== 'chat' || !choice) return
    r.destinations(choice)
      .then((d) => {
        setDestinations(d)
        if (!d.some((x) => x.id === destination)) setDestination(d[0]?.id ?? '')
      })
      .catch((e) => notify(`Could not list destinations: ${errorText(e)}`))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [r, state.role, choice])

  const confirm = () =>
    onBind(state.role === 'chat' ? { candidate: choice, destination: { id: destination } } : { candidate: choice })

  return (
    <div className="border border-border rounded-md p-3">
      <div className="flex flex-wrap items-center gap-2 mb-1">
        <span className="font-medium w-[64px]">{ROLE_LABEL[state.role]}</span>
        <Badge variant={STATUS_VARIANT[state.status]}>{STATUS_LABEL[state.status]}</Badge>
        {bound && (
          <span className="text-sm">
            {bound}
            {state.binding?.destination ? ` → ${state.binding.destination.name}` : ''}
          </span>
        )}
      </div>
      {state.reason && <p className="text-[12px] text-muted mb-2">{state.reason}</p>}

      {state.candidates.length > 0 && (
        <ul className="text-[12px] text-muted mb-2 grid gap-0.5">
          {state.candidates.map((c) => (
            <li key={c.id}>
              {candidateLabel(c)} {'·'} {c.status}
              {c.evidence.length ? ` · e.g. ${c.evidence.join(', ')}` : ''}
              {c.note ? ` · ${c.note}` : ''}
            </li>
          ))}
        </ul>
      )}

      <div className="flex flex-wrap items-center gap-2">
        {usable.length > 0 && (
          <select className={selectClass} value={choice} onChange={(e) => setChoice(e.target.value)} aria-label={`${state.role} tool`}>
            {usable.map((c) => (
              <option key={c.id} value={c.id}>
                {candidateLabel(c)}
              </option>
            ))}
          </select>
        )}
        {state.role === 'chat' && usable.length > 0 && (
          <select
            className={selectClass}
            value={destination}
            onChange={(e) => setDestination(e.target.value)}
            disabled={!destinations}
            aria-label="Chat destination"
          >
            {(destinations ?? []).map((d) => (
              <option key={d.id} value={d.id}>
                {d.name}
              </option>
            ))}
          </select>
        )}
        {usable.length > 0 && (
          <Btn primary onClick={confirm} disabled={!choice || (state.role === 'chat' && !destination)}>
            {bound ? 'Update' : 'Confirm'}
          </Btn>
        )}
        {state.status !== 'off' || state.binding ? (
          state.binding?.off ? (
            <Btn onClick={() => onBind({ candidate: null })}>Back to automatic</Btn>
          ) : (
            <Btn onClick={() => onBind({ off: true })}>Switch off</Btn>
          )
        ) : null}
      </div>
    </div>
  )
}

function ScheduleCard({ r, setup, onSaved }: { r: RadarApi; setup: Setup; onSaved: (s: Setup['scheduler']) => void }) {
  const notify = useNotify()
  const [s, setS] = useState<Schedule>(setup.scheduler.schedule)
  const set = (patch: Partial<Schedule>) => setS({ ...s, ...patch })

  const save = async () => {
    try {
      onSaved(await r.saveSchedule(s))
      notify('Schedule saved')
    } catch (e) {
      notify(errorText(e))
    }
  }

  const chatBound = setup.roles.chat.status === 'bound'
  return (
    <Card>
      <CardTitle>Schedule</CardTitle>
      <div className="grid gap-3 text-sm">
        <label className="flex flex-wrap items-center gap-2">
          Daily sweep at
          <Input type="time" value={s.sweepTime} onChange={(e) => set({ sweepTime: e.target.value })} className="max-w-[120px]" />
          in timezone
          <Input value={s.timezone} onChange={(e) => set({ timezone: e.target.value })} className="max-w-[200px]" />
        </label>
        <label className="inline-flex items-center gap-2">
          <Toggle
            checked={s.digestEnabled}
            onChange={(v) => set({ digestEnabled: v })}
            label="Weekly digest"
          />
          {chatBound ? 'Weekly digest to the bound chat' : 'Weekly digest (dashboard only until chat is bound)'}
        </label>
        {s.digestEnabled && (
          <label className="flex flex-wrap items-center gap-2">
            Send on
            <select className={selectClass} value={s.digestDay} onChange={(e) => set({ digestDay: e.target.value })}>
              {DAYS.map((d) => (
                <option key={d} value={d}>
                  {d}
                </option>
              ))}
            </select>
            at
            <Input type="time" value={s.digestTime} onChange={(e) => set({ digestTime: e.target.value })} className="max-w-[120px]" />
          </label>
        )}
        <div className="flex items-center gap-3">
          <Btn primary onClick={save}>
            Save schedule
          </Btn>
          <span className="text-[12px] text-muted">
            Next sweep {setup.scheduler.nextSweepAt.replace('T', ' ')} UTC
            {setup.scheduler.lastScheduledSweepError ? ` · last scheduled sweep: ${setup.scheduler.lastScheduledSweepError}` : ''}
          </span>
        </div>
      </div>
    </Card>
  )
}

function SourcesCard({ sources }: { sources: SourceHealth[] | null }) {
  return (
    <Card>
      <CardTitle>Sources</CardTitle>
      {!sources || sources.length === 0 ? (
        <p className="text-sm text-muted">No sweep has run yet.</p>
      ) : (
        <ul className="grid gap-1.5 text-sm">
          {sources.map((s) => (
            <li key={`${s.partner} ${s.sourceUrl}`} className="flex flex-wrap items-baseline gap-2">
              <Badge variant={s.stale ? 'err' : s.status === 'ok' ? 'ok' : s.status === 'failing' ? 'warn' : 'muted'}>
                {s.stale ? 'stale' : s.status}
              </Badge>
              <span className="w-[90px] shrink-0">{s.partner}</span>
              <span className="text-muted w-[120px] shrink-0">{s.sourceKind}</span>
              <a href={s.sourceUrl} target="_blank" rel="noreferrer noopener" className="truncate min-w-0 flex-1 hover:text-accent">
                {s.sourceUrl}
              </a>
              {s.status === 'ok' && <span className="text-[12px] text-muted">{s.itemsLastRun} items</span>}
              {s.lastError && <span className="text-[12px] text-danger basis-full pl-[100px]">{s.lastError}</span>}
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}
