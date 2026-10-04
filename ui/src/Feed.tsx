import { useEffect, useState } from 'react'
import { useNotify } from '@kirocrew/app-sdk'
import { Badge, Btn, Card, EmptyState, Input, Modal, SegmentedControl, Toggle } from '@kirocrew/app-sdk/ui'
import { AlertTriangle, Check, Clock, Download, ExternalLink, X } from 'lucide-react'
import { errorText, type Change, type Confidence, type RadarApi, type Status } from './api'

type Filter = Status | 'all'

const IMPACT_VARIANT: Record<Confidence, 'err' | 'warn' | 'muted'> = { exact: 'err', likely: 'warn', possible: 'muted' }

export function deadline(c: Pick<Change, 'effectiveAt' | 'effectivePrecision' | 'daysLeft'>): string | null {
  if (!c.effectiveAt || c.daysLeft === null) return null
  const date = c.effectivePrecision === 'month' ? `${c.effectiveAt.slice(0, 7)} (month)` : c.effectiveAt
  if (c.daysLeft < 0) return `${date} · ${-c.daysLeft}d overdue`
  if (c.daysLeft === 0) return `${date} · today`
  return `${date} · in ${c.daysLeft}d`
}

export function sourceLabel(url: string): string {
  const u = new URL(url)
  const last = u.pathname.split('/').filter(Boolean).pop()
  return last ? `${u.hostname}/\u2026/${last}` : u.hostname
}

function inDays(n: number): string {
  const d = new Date()
  d.setDate(d.getDate() + n)
  return d.toISOString().slice(0, 10)
}

export function Feed({
  r,
  version,
  onChanged,
  goToSetup,
}: {
  r: RadarApi
  version: number
  onChanged: () => void
  goToSetup: () => void
}) {
  const notify = useNotify()
  const [filter, setFilter] = useState<Filter>('new')
  const [matchedOnly, setMatchedOnly] = useState(true)
  const [changes, setChanges] = useState<Change[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [dismissing, setDismissing] = useState<Change | null>(null)
  const [snoozing, setSnoozing] = useState<Change | null>(null)
  const [tick, setTick] = useState(0)

  useEffect(() => {
    let live = true
    setError(null)
    r.changes({ status: filter === 'all' ? undefined : filter, matched: matchedOnly })
      .then((c) => live && setChanges(c))
      .catch((e) => live && setError(errorText(e)))
    return () => {
      live = false
    }
  }, [r, filter, matchedOnly, version, tick])

  // While the analyst is working on something in view, refresh every few seconds.
  const running = changes?.some((c) => c.analysis?.status === 'running') ?? false
  useEffect(() => {
    if (!running) return
    const t = setTimeout(() => setTick((n) => n + 1), 5000)
    return () => clearTimeout(t)
  }, [running, changes])

  const act = async (c: Change, action: 'accept' | 'dismiss' | 'snooze' | 'reopen' | 'analyze', body?: unknown) => {
    try {
      await r.triage(c.id, action, body)
      onChanged()
    } catch (e) {
      notify(`Could not ${action}: ${errorText(e)}`)
    }
  }

  const copy = async (c: Change) => {
    try {
      await navigator.clipboard.writeText(await r.markdown(c.id))
      notify('Copied as Markdown')
    } catch (e) {
      notify(`Could not copy: ${errorText(e)}`)
    }
  }

  return (
    <div>
      <div className="flex flex-wrap items-center gap-4 mb-4">
        <SegmentedControl<Filter>
          segments={[
            { key: 'new', label: 'To triage' },
            { key: 'accepted', label: 'Accepted' },
            { key: 'snoozed', label: 'Snoozed' },
            { key: 'dismissed', label: 'Dismissed' },
            { key: 'all', label: 'All' },
          ]}
          value={filter}
          onChange={setFilter}
        />
        <label className="inline-flex items-center gap-2 text-sm text-muted">
          <Toggle checked={matchedOnly} onChange={setMatchedOnly} label="Only changes that affect my connectors" />
          Only changes that affect my connectors
        </label>
      </div>

      {error && <p className="text-sm text-danger mb-3">{error}</p>}
      {changes === null && !error && <p className="text-sm text-muted">Loading…</p>}
      {changes?.length === 0 && (
        <EmptyState
          icon={<Check size={28} />}
          title={filter === 'new' ? 'Nothing to triage' : 'No changes here'}
          subtitle={
            matchedOnly
              ? 'No change in this view affects a connector in your manifest. Turn off the filter to see everything.'
              : 'Run a sweep, or check the connector manifest in Setup.'
          }
          action={<Btn onClick={goToSetup}>Open Setup</Btn>}
        />
      )}

      <div className="grid gap-3">
        {changes?.map((c) => (
          <ChangeCard
            key={c.id}
            c={c}
            onAccept={() => act(c, 'accept')}
            onDismiss={() => setDismissing(c)}
            onSnooze={() => setSnoozing(c)}
            onReopen={() => act(c, 'reopen')}
            onCopy={() => copy(c)}
            onAnalyze={() => act(c, 'analyze')}
          />
        ))}
      </div>

      <DismissModal
        change={dismissing}
        onClose={() => setDismissing(null)}
        onConfirm={async (reason) => {
          if (dismissing) await act(dismissing, 'dismiss', { reason })
          setDismissing(null)
        }}
      />
      <SnoozeModal
        change={snoozing}
        onClose={() => setSnoozing(null)}
        onConfirm={async (until) => {
          if (snoozing) await act(snoozing, 'snooze', { until })
          setSnoozing(null)
        }}
      />
    </div>
  )
}

function ChangeCard({
  c,
  onAccept,
  onDismiss,
  onSnooze,
  onReopen,
  onCopy,
  onAnalyze,
}: {
  c: Change
  onAccept: () => void
  onDismiss: () => void
  onSnooze: () => void
  onReopen: () => void
  onCopy: () => void
  onAnalyze: () => void
}) {
  // A change resembling a past dismissal starts collapsed, with the matching lesson. Never hidden.
  const [expanded, setExpanded] = useState(!c.priorDismissal)
  const [showText, setShowText] = useState(false)
  const due = deadline(c)

  if (!expanded && c.priorDismissal) {
    const p = c.priorDismissal
    return (
      <Card className="opacity-80">
        <div className="flex items-start justify-between gap-3">
          <div className="text-sm min-w-0">
            <div className="font-medium truncate">{c.title}</div>
            <div className="text-muted mt-1">
              Resembles a change you dismissed: <span className="italic">{p.title}</span>
              {p.reason ? <> {'—'} {p.reason}</> : null}
            </div>
            {p.lesson && <div className="text-muted text-[12px] mt-1">Lesson: {p.lesson.rule}</div>}
          </div>
          <Btn onClick={() => setExpanded(true)}>Show</Btn>
        </div>
      </Card>
    )
  }

  return (
    <Card>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-1.5 mb-1.5">
            {c.urgent && (
              <Badge variant="err">
                <AlertTriangle size={12} /> urgent
              </Badge>
            )}
            {c.impact && <Badge variant={IMPACT_VARIANT[c.impact]}>{c.impact}</Badge>}
            <Badge variant="aim">{c.kind}</Badge>
            <Badge variant="muted">{c.partnerName}</Badge>
            {c.status !== 'new' && (
              <Badge variant="muted">
                {c.status}
                {c.snoozedUntil ? ` until ${c.snoozedUntil}` : ''}
              </Badge>
            )}
          </div>
          <div className="font-medium text-text-strong">{c.title}</div>
          {due && <div className={`text-sm mt-0.5 ${c.urgent ? 'text-danger' : 'text-muted'}`}>{due}</div>}
        </div>
        <div className="flex flex-wrap justify-end gap-1.5 shrink-0">
          {c.status === 'new' ? (
            <>
              <Btn primary onClick={onAccept}>
                <Check size={14} /> Accept
              </Btn>
              <Btn onClick={onSnooze}>
                <Clock size={14} /> Snooze
              </Btn>
              <Btn danger onClick={onDismiss}>
                <X size={14} /> Dismiss
              </Btn>
            </>
          ) : (
            <Btn onClick={onReopen}>Reopen</Btn>
          )}
          <Btn onClick={onCopy} title="Copy a Markdown write-up to paste into any tracker">
            <Download size={14} /> Markdown
          </Btn>
        </div>
      </div>

      <AnalysisBlock c={c} onAnalyze={onAnalyze} />

      {c.matches.length > 0 && (
        <ul className="mt-3 grid gap-1 text-sm">
          {c.matches.map((m) => (
            <li key={m.connector}>
              <code className="text-[12px]">{m.connector}</code>{' '}
              <span className="text-muted">
                {m.confidence}
                {m.action !== 'fix' ? ` · ${m.action}` : ''} {'—'} {m.reasons.join('; ')}
              </span>
            </li>
          ))}
        </ul>
      )}

      {c.status === 'dismissed' && c.statusReason && (
        <div className="mt-3 text-sm text-muted">
          Dismissed: {c.statusReason}
          {c.lesson && (
            <span className="text-[12px]">
              {' '}
              {'·'} lesson {c.lesson.status === 'saved' ? 'saved to Crew' : c.lesson.status === 'failed' ? 'failed to save' : 'queued for Crew'}
            </span>
          )}
        </div>
      )}

      <div className="mt-3 flex flex-wrap items-center gap-3 text-[12px] text-muted">
        {c.body && (
          <button className="underline cursor-pointer bg-transparent border-none p-0 text-muted" onClick={() => setShowText(!showText)}>
            {showText ? 'Hide partner text' : 'Show partner text'}
          </button>
        )}
        {c.sources.map((url) => (
          <a key={url} href={url} target="_blank" rel="noreferrer noopener" className="inline-flex items-center gap-1 hover:text-accent">
            <ExternalLink size={12} /> {sourceLabel(url)}
          </a>
        ))}
        {c.priorDismissal && (
          <span>
            {'·'} resembles dismissed {'“'}
            {c.priorDismissal.title}
            {'”'}
          </span>
        )}
      </div>
      {/* Partner text is untrusted: shown as plain text, never rendered as HTML or Markdown. */}
      {showText && <p className="mt-2 text-sm whitespace-pre-wrap break-words border-l-2 border-border pl-3">{c.body}</p>}
    </Card>
  )
}

const ASSESSED_VARIANT = { high: 'err', medium: 'warn', low: 'muted', none: 'ok' } as const

/** The change-analyst's summary. Model-written, so labelled as such and kept apart from the deterministic match. */
function AnalysisBlock({ c, onAnalyze }: { c: Change; onAnalyze: () => void }) {
  const a = c.analysis
  if (!c.matches.length) return null
  if (!a) {
    return (
      <div className="mt-3">
        <Btn onClick={onAnalyze}>Summarize</Btn>
      </div>
    )
  }
  if (a.status === 'running') return <p className="mt-3 text-sm text-muted">Summarizing…</p>
  if (a.status === 'failed' && !a.analysis) {
    return (
      <div className="mt-3 flex items-center gap-2 text-[12px] text-muted">
        Summary failed: {a.error}
        <Btn onClick={onAnalyze}>Retry</Btn>
      </div>
    )
  }
  const x = a.analysis!
  return (
    <div className="mt-3 rounded-md border border-border p-3 text-sm">
      <div className="flex flex-wrap items-center gap-2 mb-1">
        <Badge variant={ASSESSED_VARIANT[x.impact]}>assessed impact: {x.impact}</Badge>
        <span className="text-[11px] text-muted">AI summary {'\u00b7'} check before acting</span>
      </div>
      <p>{x.summary}</p>
      <p className="text-muted mt-1">{x.rationale}</p>
      {x.actions.length > 0 && (
        <ol className="mt-1.5 list-decimal pl-5 text-muted">
          {x.actions.map((step, i) => (
            <li key={i}>{step}</li>
          ))}
        </ol>
      )}
    </div>
  )
}

function DismissModal({
  change,
  onClose,
  onConfirm,
}: {
  change: Change | null
  onClose: () => void
  onConfirm: (reason: string) => Promise<void>
}) {
  const [reason, setReason] = useState('')
  useEffect(() => setReason(''), [change])
  return (
    <Modal
      open={change !== null}
      onClose={onClose}
      title="Dismiss change"
      footer={
        <div className="flex justify-end gap-2">
          <Btn onClick={onClose}>Cancel</Btn>
          <Btn danger disabled={!reason.trim()} onClick={() => onConfirm(reason.trim())}>
            Dismiss
          </Btn>
        </div>
      }
    >
      <p className="text-sm mb-3">{change?.title}</p>
      <label className="text-sm text-muted block mb-1.5" htmlFor="dismiss-reason">
        Why does this need no work? This is saved as a Crew lesson, and similar changes are shown collapsed with it.
      </label>
      <Input
        id="dismiss-reason"
        autoFocus
        value={reason}
        onChange={(e) => setReason(e.target.value)}
        placeholder="e.g. We do not call this endpoint"
        className="w-full"
      />
    </Modal>
  )
}

function SnoozeModal({
  change,
  onClose,
  onConfirm,
}: {
  change: Change | null
  onClose: () => void
  onConfirm: (until: string) => Promise<void>
}) {
  const [until, setUntil] = useState(inDays(14))
  useEffect(() => setUntil(inDays(14)), [change])
  return (
    <Modal
      open={change !== null}
      onClose={onClose}
      title="Snooze change"
      footer={
        <div className="flex justify-end gap-2">
          <Btn onClick={onClose}>Cancel</Btn>
          <Btn primary disabled={!until} onClick={() => onConfirm(until)}>
            Snooze
          </Btn>
        </div>
      }
    >
      <p className="text-sm mb-3">{change?.title}</p>
      <label className="text-sm text-muted block mb-1.5" htmlFor="snooze-until">
        Back in the triage list on
      </label>
      <div className="flex gap-2">
        <Input id="snooze-until" type="date" min={inDays(1)} value={until} onChange={(e) => setUntil(e.target.value)} />
        {[7, 14, 30].map((n) => (
          <Btn key={n} onClick={() => setUntil(inDays(n))}>
            {n}d
          </Btn>
        ))}
      </div>
    </Modal>
  )
}
