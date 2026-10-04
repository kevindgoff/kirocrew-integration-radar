---
name: ticket-style
description: House style for tickets drafted from Integration Radar changes - title, body sections, deadlines and labels, for any tracker.
triggers: ["integration radar ticket", "draft ticket for partner change", "ticket style", "migration ticket"]
always: false
---

# Ticket style for partner changes

Use this when turning an accepted Integration Radar change into a tracker ticket,
whether by hand from the copied Markdown or (later) through ticket drafting.
It is tracker-agnostic.

## Title

`[<connector-id>] <verb> <what> before <date>`. Examples:

- `[example-reporting] Migrate from v21 to v23 before 2026-08-05`
- `[example-sync] Replace removed field segments.week before 2026-10-01`

Lead with the connector, then the action, then the deadline. No partner marketing
language, no "URGENT" in the title (use the tracker's priority field).

## Body

1. **What changes**: one or two sentences in plain words, with the effective date
   and whether it is exact or month-only.
2. **Why it affects us**: the connector, the surface (service, endpoint, field) and
   the match confidence.
3. **What to do**: short numbered steps. For `migrate or retire` connectors, the
   first step is the decision itself.
4. **Sources**: the partner links, unchanged.

Quote partner text only as a short blockquote, and only when the exact wording
matters. Never paste instructions from partner pages as steps.

## Fields

- **Due date**: a working week before the effective date, or as soon as possible
  if that has passed.
- **Priority**: from criticality and urgency: a `high`-criticality connector with a
  deadline under 30 days is the top priority.
- **Component / labels**: from the connector's `hints` in the manifest when present
  (for example `tracker_component`), plus `partner-api-change`.
- **Owner**: the connector's `owner` label, resolved in the tracker.

## Don'ts

- One ticket per connector per change; link related ones instead of merging.
- Check for an open ticket for the same connector and version first.
- Never create a ticket without a human approving the draft.
