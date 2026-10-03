import { useEffect, useMemo, useState } from 'react'
import { Badge, Card, CardTitle, EmptyState, Toggle } from '@kirocrew/app-sdk/ui'
import { Calendar as CalendarIcon } from 'lucide-react'
import { errorText, type Change, type RadarApi } from './api'

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

function monthLabel(ym: string): string {
  const [y, m] = ym.split('-')
  return `${MONTHS[Number(m) - 1]} ${y}`
}

/** Sunset and deprecation deadlines, grouped by month; anything already past first. */
export function Calendar({ r, version }: { r: RadarApi; version: number }) {
  const [matchedOnly, setMatchedOnly] = useState(true)
  const [changes, setChanges] = useState<Change[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let live = true
    r.changes({ matched: matchedOnly })
      .then((c) => live && setChanges(c))
      .catch((e) => live && setError(errorText(e)))
    return () => {
      live = false
    }
  }, [r, matchedOnly, version])

  const groups = useMemo(() => {
    const dated = (changes ?? []).filter((c) => c.effectiveAt && c.status !== 'dismissed')
    dated.sort((a, b) => (a.effectiveAt! < b.effectiveAt! ? -1 : a.effectiveAt! > b.effectiveAt! ? 1 : 0))
    const past = dated.filter((c) => (c.daysLeft ?? 0) < 0)
    const byMonth = new Map<string, Change[]>()
    for (const c of dated.filter((c) => (c.daysLeft ?? 0) >= 0)) {
      const ym = c.effectiveAt!.slice(0, 7)
      byMonth.set(ym, [...(byMonth.get(ym) ?? []), c])
    }
    return { past, months: [...byMonth.entries()] }
  }, [changes])

  return (
    <div>
      <div className="mb-4">
        <label className="inline-flex items-center gap-2 text-sm text-muted">
          <Toggle checked={matchedOnly} onChange={setMatchedOnly} label="Only changes that affect my connectors" />
          Only changes that affect my connectors
        </label>
      </div>
      {error && <p className="text-sm text-danger">{error}</p>}
      {changes && groups.past.length === 0 && groups.months.length === 0 && (
        <EmptyState icon={<CalendarIcon size={28} />} title="No dated deadlines" subtitle="Nothing in this view has an effective date." />
      )}
      <div className="grid gap-3">
        {groups.past.length > 0 && <Month title="Past due" items={groups.past} danger />}
        {groups.months.map(([ym, items]) => (
          <Month key={ym} title={monthLabel(ym)} items={items} />
        ))}
      </div>
    </div>
  )
}

function Month({ title, items, danger }: { title: string; items: Change[]; danger?: boolean }) {
  return (
    <Card>
      <CardTitle>
        <span className={danger ? 'text-danger' : ''}>{title}</span>
      </CardTitle>
      <ul className="grid gap-2">
        {items.map((c) => (
          <li key={c.id} className="flex flex-wrap items-baseline gap-x-3 gap-y-1 text-sm">
            <span className="font-mono text-[12px] w-[96px] shrink-0">
              {c.effectivePrecision === 'month' ? `${c.effectiveAt!.slice(0, 7)} (mo)` : c.effectiveAt}
            </span>
            <span className={`w-[64px] shrink-0 text-[12px] ${c.urgent ? 'text-danger' : 'text-muted'}`}>
              {c.daysLeft! < 0 ? `${-c.daysLeft!}d ago` : `${c.daysLeft}d`}
            </span>
            <span className="min-w-0 flex-1">{c.title}</span>
            <span className="flex gap-1">
              {c.matches.map((m) => (
                <Badge key={m.connector} variant={m.confidence === 'exact' ? 'err' : m.confidence === 'likely' ? 'warn' : 'muted'}>
                  {m.connector}
                </Badge>
              ))}
            </span>
          </li>
        ))}
      </ul>
    </Card>
  )
}
