import { jsxs as s, jsx as e, Fragment as O } from "react/jsx-runtime";
import { useState as h, useEffect as A, useMemo as H, useCallback as Q } from "react";
import { useNotify as z, useAppApi as Z, useNavBadge as ee } from "@kirocrew/app-sdk";
import { SegmentedControl as V, Toggle as B, EmptyState as q, Btn as N, Card as D, Badge as $, Modal as M, Input as R, CardTitle as E, PageHeader as te } from "@kirocrew/app-sdk/ui";
import { Check as J, AlertTriangle as ne, Clock as se, X as ae, Download as ie, ExternalLink as le, Calendar as re, RefreshCw as G } from "lucide-react";
const C = "/apps/integration-radar/api";
function k(t) {
  const n = t instanceof Error ? t.message : String(t), r = n.indexOf("{");
  if (r >= 0)
    try {
      const l = JSON.parse(n.slice(r));
      if (typeof l.error == "string") return l.error;
      if (Array.isArray(l.errors)) return l.errors.join("; ");
    } catch {
    }
  return n;
}
const de = (t) => ({
  summary: () => t.get(`${C}/summary`),
  changes: (n) => {
    const r = new URLSearchParams();
    n.status && r.set("status", n.status), n.matched && r.set("matched", "1");
    const l = r.toString();
    return t.get(`${C}/changes${l ? `?${l}` : ""}`).then((a) => a.changes);
  },
  triage: (n, r, l) => t.post(`${C}/changes/${encodeURIComponent(n)}/${r}`, l ?? {}),
  markdown: (n) => t.get(`${C}/changes/${encodeURIComponent(n)}/markdown`).then((r) => r.markdown),
  digestPreview: () => t.get(`${C}/digest/preview`),
  digestSend: () => t.post(`${C}/digest/send`, {}),
  digests: () => t.get(`${C}/digests`).then((n) => n.digests),
  deliverLessons: () => t.post(`${C}/lessons/deliver`, {}),
  sweep: () => t.post(`${C}/sweep`, {}),
  sources: () => t.get(`${C}/sources`),
  setup: () => t.get(`${C}/setup`),
  detect: () => t.post(`${C}/setup/detect`, {}),
  destinations: (n) => t.get(
    `${C}/setup/roles/chat/destinations?candidate=${encodeURIComponent(n)}`
  ).then((r) => r.destinations),
  bind: (n, r) => t.put(`${C}/setup/roles/${n}`, r),
  loadManifest: (n) => t.post(`${C}/manifest/load`, { path: n }),
  saveSchedule: (n) => t.put(`${C}/setup/schedule`, n)
}), oe = { exact: "err", likely: "warn", possible: "muted" };
function ce(t) {
  if (!t.effectiveAt || t.daysLeft === null) return null;
  const n = t.effectivePrecision === "month" ? `${t.effectiveAt.slice(0, 7)} (month)` : t.effectiveAt;
  return t.daysLeft < 0 ? `${n} · ${-t.daysLeft}d overdue` : t.daysLeft === 0 ? `${n} · today` : `${n} · in ${t.daysLeft}d`;
}
function me(t) {
  const n = new URL(t), r = n.pathname.split("/").filter(Boolean).pop();
  return r ? `${n.hostname}/…/${r}` : n.hostname;
}
function P(t) {
  const n = /* @__PURE__ */ new Date();
  return n.setDate(n.getDate() + t), n.toISOString().slice(0, 10);
}
function he({
  r: t,
  version: n,
  onChanged: r,
  goToSetup: l
}) {
  const a = z(), [c, o] = h("new"), [b, u] = h(!0), [d, m] = h(null), [w, i] = h(null), [f, x] = h(null), [v, p] = h(null), [T, U] = h(0);
  A(() => {
    let y = !0;
    return i(null), t.changes({ status: c === "all" ? void 0 : c, matched: b }).then((S) => y && m(S)).catch((S) => y && i(k(S))), () => {
      y = !1;
    };
  }, [t, c, b, n, T]);
  const g = (d == null ? void 0 : d.some((y) => {
    var S;
    return ((S = y.analysis) == null ? void 0 : S.status) === "running";
  })) ?? !1;
  A(() => {
    if (!g) return;
    const y = setTimeout(() => U((S) => S + 1), 5e3);
    return () => clearTimeout(y);
  }, [g, d]);
  const L = async (y, S, X) => {
    try {
      await t.triage(y.id, S, X), r();
    } catch (Y) {
      a(`Could not ${S}: ${k(Y)}`);
    }
  }, I = async (y) => {
    try {
      await navigator.clipboard.writeText(await t.markdown(y.id)), a("Copied as Markdown");
    } catch (S) {
      a(`Could not copy: ${k(S)}`);
    }
  };
  return /* @__PURE__ */ s("div", { children: [
    /* @__PURE__ */ s("div", { className: "flex flex-wrap items-center gap-4 mb-4", children: [
      /* @__PURE__ */ e(
        V,
        {
          segments: [
            { key: "new", label: "To triage" },
            { key: "accepted", label: "Accepted" },
            { key: "snoozed", label: "Snoozed" },
            { key: "dismissed", label: "Dismissed" },
            { key: "all", label: "All" }
          ],
          value: c,
          onChange: o
        }
      ),
      /* @__PURE__ */ s("label", { className: "inline-flex items-center gap-2 text-sm text-muted", children: [
        /* @__PURE__ */ e(B, { checked: b, onChange: u, label: "Only changes that affect my connectors" }),
        "Only changes that affect my connectors"
      ] })
    ] }),
    w && /* @__PURE__ */ e("p", { className: "text-sm text-danger mb-3", children: w }),
    d === null && !w && /* @__PURE__ */ e("p", { className: "text-sm text-muted", children: "Loading…" }),
    (d == null ? void 0 : d.length) === 0 && /* @__PURE__ */ e(
      q,
      {
        icon: /* @__PURE__ */ e(J, { size: 28 }),
        title: c === "new" ? "Nothing to triage" : "No changes here",
        subtitle: b ? "No change in this view affects a connector in your manifest. Turn off the filter to see everything." : "Run a sweep, or check the connector manifest in Setup.",
        action: /* @__PURE__ */ e(N, { onClick: l, children: "Open Setup" })
      }
    ),
    /* @__PURE__ */ e("div", { className: "grid gap-3", children: d == null ? void 0 : d.map((y) => /* @__PURE__ */ e(
      ue,
      {
        c: y,
        onAccept: () => L(y, "accept"),
        onDismiss: () => x(y),
        onSnooze: () => p(y),
        onReopen: () => L(y, "reopen"),
        onCopy: () => I(y),
        onAnalyze: () => L(y, "analyze")
      },
      y.id
    )) }),
    /* @__PURE__ */ e(
      ge,
      {
        change: f,
        onClose: () => x(null),
        onConfirm: async (y) => {
          f && await L(f, "dismiss", { reason: y }), x(null);
        }
      }
    ),
    /* @__PURE__ */ e(
      xe,
      {
        change: v,
        onClose: () => p(null),
        onConfirm: async (y) => {
          v && await L(v, "snooze", { until: y }), p(null);
        }
      }
    )
  ] });
}
function ue({
  c: t,
  onAccept: n,
  onDismiss: r,
  onSnooze: l,
  onReopen: a,
  onCopy: c,
  onAnalyze: o
}) {
  const [b, u] = h(!t.priorDismissal), [d, m] = h(!1), w = ce(t);
  if (!b && t.priorDismissal) {
    const i = t.priorDismissal;
    return /* @__PURE__ */ e(D, { className: "opacity-80", children: /* @__PURE__ */ s("div", { className: "flex items-start justify-between gap-3", children: [
      /* @__PURE__ */ s("div", { className: "text-sm min-w-0", children: [
        /* @__PURE__ */ e("div", { className: "font-medium truncate", children: t.title }),
        /* @__PURE__ */ s("div", { className: "text-muted mt-1", children: [
          "Resembles a change you dismissed: ",
          /* @__PURE__ */ e("span", { className: "italic", children: i.title }),
          i.reason ? /* @__PURE__ */ s(O, { children: [
            " ",
            "—",
            " ",
            i.reason
          ] }) : null
        ] }),
        i.lesson && /* @__PURE__ */ s("div", { className: "text-muted text-[12px] mt-1", children: [
          "Lesson: ",
          i.lesson.rule
        ] })
      ] }),
      /* @__PURE__ */ e(N, { onClick: () => u(!0), children: "Show" })
    ] }) });
  }
  return /* @__PURE__ */ s(D, { children: [
    /* @__PURE__ */ s("div", { className: "flex items-start justify-between gap-3", children: [
      /* @__PURE__ */ s("div", { className: "min-w-0", children: [
        /* @__PURE__ */ s("div", { className: "flex flex-wrap items-center gap-1.5 mb-1.5", children: [
          t.urgent && /* @__PURE__ */ s($, { variant: "err", children: [
            /* @__PURE__ */ e(ne, { size: 12 }),
            " urgent"
          ] }),
          t.impact && /* @__PURE__ */ e($, { variant: oe[t.impact], children: t.impact }),
          /* @__PURE__ */ e($, { variant: "aim", children: t.kind }),
          /* @__PURE__ */ e($, { variant: "muted", children: t.partnerName }),
          t.status !== "new" && /* @__PURE__ */ s($, { variant: "muted", children: [
            t.status,
            t.snoozedUntil ? ` until ${t.snoozedUntil}` : ""
          ] })
        ] }),
        /* @__PURE__ */ e("div", { className: "font-medium text-text-strong", children: t.title }),
        w && /* @__PURE__ */ e("div", { className: `text-sm mt-0.5 ${t.urgent ? "text-danger" : "text-muted"}`, children: w })
      ] }),
      /* @__PURE__ */ s("div", { className: "flex flex-wrap justify-end gap-1.5 shrink-0", children: [
        t.status === "new" ? /* @__PURE__ */ s(O, { children: [
          /* @__PURE__ */ s(N, { primary: !0, onClick: n, children: [
            /* @__PURE__ */ e(J, { size: 14 }),
            " Accept"
          ] }),
          /* @__PURE__ */ s(N, { onClick: l, children: [
            /* @__PURE__ */ e(se, { size: 14 }),
            " Snooze"
          ] }),
          /* @__PURE__ */ s(N, { danger: !0, onClick: r, children: [
            /* @__PURE__ */ e(ae, { size: 14 }),
            " Dismiss"
          ] })
        ] }) : /* @__PURE__ */ e(N, { onClick: a, children: "Reopen" }),
        /* @__PURE__ */ s(N, { onClick: c, title: "Copy a Markdown write-up to paste into any tracker", children: [
          /* @__PURE__ */ e(ie, { size: 14 }),
          " Markdown"
        ] })
      ] })
    ] }),
    /* @__PURE__ */ e(fe, { c: t, onAnalyze: o }),
    t.matches.length > 0 && /* @__PURE__ */ e("ul", { className: "mt-3 grid gap-1 text-sm", children: t.matches.map((i) => /* @__PURE__ */ s("li", { children: [
      /* @__PURE__ */ e("code", { className: "text-[12px]", children: i.connector }),
      " ",
      /* @__PURE__ */ s("span", { className: "text-muted", children: [
        i.confidence,
        i.action !== "fix" ? ` · ${i.action}` : "",
        " ",
        "—",
        " ",
        i.reasons.join("; ")
      ] })
    ] }, i.connector)) }),
    t.status === "dismissed" && t.statusReason && /* @__PURE__ */ s("div", { className: "mt-3 text-sm text-muted", children: [
      "Dismissed: ",
      t.statusReason,
      t.lesson && /* @__PURE__ */ s("span", { className: "text-[12px]", children: [
        " ",
        "·",
        " lesson ",
        t.lesson.status === "saved" ? "saved to Crew" : t.lesson.status === "failed" ? "failed to save" : "queued for Crew"
      ] })
    ] }),
    /* @__PURE__ */ s("div", { className: "mt-3 flex flex-wrap items-center gap-3 text-[12px] text-muted", children: [
      t.body && /* @__PURE__ */ e("button", { className: "underline cursor-pointer bg-transparent border-none p-0 text-muted", onClick: () => m(!d), children: d ? "Hide partner text" : "Show partner text" }),
      t.sources.map((i) => /* @__PURE__ */ s("a", { href: i, target: "_blank", rel: "noreferrer noopener", className: "inline-flex items-center gap-1 hover:text-accent", children: [
        /* @__PURE__ */ e(le, { size: 12 }),
        " ",
        me(i)
      ] }, i)),
      t.priorDismissal && /* @__PURE__ */ s("span", { children: [
        "·",
        " resembles dismissed ",
        "“",
        t.priorDismissal.title,
        "”"
      ] })
    ] }),
    d && /* @__PURE__ */ e("p", { className: "mt-2 text-sm whitespace-pre-wrap break-words border-l-2 border-border pl-3", children: t.body })
  ] });
}
const pe = { high: "err", medium: "warn", low: "muted", none: "ok" };
function fe({ c: t, onAnalyze: n }) {
  const r = t.analysis;
  if (!t.matches.length) return null;
  if (!r)
    return /* @__PURE__ */ e("div", { className: "mt-3", children: /* @__PURE__ */ e(N, { onClick: n, children: "Summarize" }) });
  if (r.status === "running") return /* @__PURE__ */ e("p", { className: "mt-3 text-sm text-muted", children: "Summarizing…" });
  if (r.status === "failed" && !r.analysis)
    return /* @__PURE__ */ s("div", { className: "mt-3 flex items-center gap-2 text-[12px] text-muted", children: [
      "Summary failed: ",
      r.error,
      /* @__PURE__ */ e(N, { onClick: n, children: "Retry" })
    ] });
  const l = r.analysis;
  return /* @__PURE__ */ s("div", { className: "mt-3 rounded-md border border-border p-3 text-sm", children: [
    /* @__PURE__ */ s("div", { className: "flex flex-wrap items-center gap-2 mb-1", children: [
      /* @__PURE__ */ s($, { variant: pe[l.impact], children: [
        "assessed impact: ",
        l.impact
      ] }),
      /* @__PURE__ */ s("span", { className: "text-[11px] text-muted", children: [
        "AI summary ",
        "·",
        " check before acting"
      ] })
    ] }),
    /* @__PURE__ */ e("p", { children: l.summary }),
    /* @__PURE__ */ e("p", { className: "text-muted mt-1", children: l.rationale }),
    l.actions.length > 0 && /* @__PURE__ */ e("ol", { className: "mt-1.5 list-decimal pl-5 text-muted", children: l.actions.map((a, c) => /* @__PURE__ */ e("li", { children: a }, c)) })
  ] });
}
function ge({
  change: t,
  onClose: n,
  onConfirm: r
}) {
  const [l, a] = h("");
  return A(() => a(""), [t]), /* @__PURE__ */ s(
    M,
    {
      open: t !== null,
      onClose: n,
      title: "Dismiss change",
      footer: /* @__PURE__ */ s("div", { className: "flex justify-end gap-2", children: [
        /* @__PURE__ */ e(N, { onClick: n, children: "Cancel" }),
        /* @__PURE__ */ e(N, { danger: !0, disabled: !l.trim(), onClick: () => r(l.trim()), children: "Dismiss" })
      ] }),
      children: [
        /* @__PURE__ */ e("p", { className: "text-sm mb-3", children: t == null ? void 0 : t.title }),
        /* @__PURE__ */ e("label", { className: "text-sm text-muted block mb-1.5", htmlFor: "dismiss-reason", children: "Why does this need no work? This is saved as a Crew lesson, and similar changes are shown collapsed with it." }),
        /* @__PURE__ */ e(
          R,
          {
            id: "dismiss-reason",
            autoFocus: !0,
            value: l,
            onChange: (c) => a(c.target.value),
            placeholder: "e.g. We do not call this endpoint",
            className: "w-full"
          }
        )
      ]
    }
  );
}
function xe({
  change: t,
  onClose: n,
  onConfirm: r
}) {
  const [l, a] = h(P(14));
  return A(() => a(P(14)), [t]), /* @__PURE__ */ s(
    M,
    {
      open: t !== null,
      onClose: n,
      title: "Snooze change",
      footer: /* @__PURE__ */ s("div", { className: "flex justify-end gap-2", children: [
        /* @__PURE__ */ e(N, { onClick: n, children: "Cancel" }),
        /* @__PURE__ */ e(N, { primary: !0, disabled: !l, onClick: () => r(l), children: "Snooze" })
      ] }),
      children: [
        /* @__PURE__ */ e("p", { className: "text-sm mb-3", children: t == null ? void 0 : t.title }),
        /* @__PURE__ */ e("label", { className: "text-sm text-muted block mb-1.5", htmlFor: "snooze-until", children: "Back in the triage list on" }),
        /* @__PURE__ */ s("div", { className: "flex gap-2", children: [
          /* @__PURE__ */ e(R, { id: "snooze-until", type: "date", min: P(1), value: l, onChange: (c) => a(c.target.value) }),
          [7, 14, 30].map((c) => /* @__PURE__ */ s(N, { onClick: () => a(P(c)), children: [
            c,
            "d"
          ] }, c))
        ] })
      ]
    }
  );
}
const ve = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
function be(t) {
  const [n, r] = t.split("-");
  return `${ve[Number(r) - 1]} ${n}`;
}
function we({ r: t, version: n }) {
  const [r, l] = h(!0), [a, c] = h(null), [o, b] = h(null);
  A(() => {
    let d = !0;
    return t.changes({ matched: r }).then((m) => d && c(m)).catch((m) => d && b(k(m))), () => {
      d = !1;
    };
  }, [t, r, n]);
  const u = H(() => {
    const d = (a ?? []).filter((i) => i.effectiveAt && i.status !== "dismissed");
    d.sort((i, f) => i.effectiveAt < f.effectiveAt ? -1 : i.effectiveAt > f.effectiveAt ? 1 : 0);
    const m = d.filter((i) => (i.daysLeft ?? 0) < 0), w = /* @__PURE__ */ new Map();
    for (const i of d.filter((f) => (f.daysLeft ?? 0) >= 0)) {
      const f = i.effectiveAt.slice(0, 7);
      w.set(f, [...w.get(f) ?? [], i]);
    }
    return { past: m, months: [...w.entries()] };
  }, [a]);
  return /* @__PURE__ */ s("div", { children: [
    /* @__PURE__ */ e("div", { className: "mb-4", children: /* @__PURE__ */ s("label", { className: "inline-flex items-center gap-2 text-sm text-muted", children: [
      /* @__PURE__ */ e(B, { checked: r, onChange: l, label: "Only changes that affect my connectors" }),
      "Only changes that affect my connectors"
    ] }) }),
    o && /* @__PURE__ */ e("p", { className: "text-sm text-danger", children: o }),
    a && u.past.length === 0 && u.months.length === 0 && /* @__PURE__ */ e(q, { icon: /* @__PURE__ */ e(re, { size: 28 }), title: "No dated deadlines", subtitle: "Nothing in this view has an effective date." }),
    /* @__PURE__ */ s("div", { className: "grid gap-3", children: [
      u.past.length > 0 && /* @__PURE__ */ e(_, { title: "Past due", items: u.past, danger: !0 }),
      u.months.map(([d, m]) => /* @__PURE__ */ e(_, { title: be(d), items: m }, d))
    ] })
  ] });
}
function _({ title: t, items: n, danger: r }) {
  return /* @__PURE__ */ s(D, { children: [
    /* @__PURE__ */ e(E, { children: /* @__PURE__ */ e("span", { className: r ? "text-danger" : "", children: t }) }),
    /* @__PURE__ */ e("ul", { className: "grid gap-2", children: n.map((l) => /* @__PURE__ */ s("li", { className: "flex flex-wrap items-baseline gap-x-3 gap-y-1 text-sm", children: [
      /* @__PURE__ */ e("span", { className: "font-mono text-[12px] w-[96px] shrink-0", children: l.effectivePrecision === "month" ? `${l.effectiveAt.slice(0, 7)} (mo)` : l.effectiveAt }),
      /* @__PURE__ */ e("span", { className: `w-[64px] shrink-0 text-[12px] ${l.urgent ? "text-danger" : "text-muted"}`, children: l.daysLeft < 0 ? `${-l.daysLeft}d ago` : `${l.daysLeft}d` }),
      /* @__PURE__ */ e("span", { className: "min-w-0 flex-1", children: l.title }),
      /* @__PURE__ */ e("span", { className: "flex gap-1", children: l.matches.map((a) => /* @__PURE__ */ e($, { variant: a.confidence === "exact" ? "err" : a.confidence === "likely" ? "warn" : "muted", children: a.connector }, a.connector)) })
    ] }, l.id)) })
  ] });
}
const K = { tracker: "Tracker", wiki: "Wiki", chat: "Chat", repo: "Repo" }, ye = {
  bound: "ok",
  degraded: "err",
  proposed: "aim",
  needs_choice: "warn",
  off: "muted"
}, Ne = {
  bound: "bound",
  degraded: "degraded",
  proposed: "proposed",
  needs_choice: "choose one",
  off: "off"
}, Ce = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"], j = "bg-bg-elevated border border-border rounded-md px-2 py-1.5 text-sm text-text";
function ke({ r: t, version: n, onChanged: r }) {
  const l = z(), [a, c] = h(null), [o, b] = h(null), [u, d] = h(null), [m, w] = h(!1);
  A(() => {
    t.setup().then(c).catch((x) => d(k(x))), t.sources().then((x) => b(x.sources)).catch(() => b([]));
  }, [t, n]);
  const i = async () => {
    w(!0);
    try {
      c(await t.detect());
    } catch (x) {
      l(`Detection failed: ${k(x)}`);
    } finally {
      w(!1);
    }
  }, f = async (x, v) => {
    try {
      c(await t.bind(x, v)), r();
    } catch (p) {
      l(k(p));
    }
  };
  return u ? /* @__PURE__ */ e("p", { className: "text-sm text-danger", children: u }) : a ? /* @__PURE__ */ s("div", { className: "grid gap-4", children: [
    /* @__PURE__ */ e(Se, { r: t, setup: a, onLoaded: (x) => {
      c(x), r();
    } }),
    /* @__PURE__ */ s(D, { children: [
      /* @__PURE__ */ s("div", { className: "flex items-center justify-between mb-2", children: [
        /* @__PURE__ */ e(E, { children: "Tools" }),
        /* @__PURE__ */ s(N, { onClick: i, disabled: m, children: [
          /* @__PURE__ */ e(G, { size: 14, className: m ? "animate-spin" : "" }),
          " Re-detect"
        ] })
      ] }),
      /* @__PURE__ */ e("p", { className: "text-sm text-muted mb-3", children: "Roles bind only to MCP servers and messaging interfaces already configured in Crew. Every role is optional; with none bound, this dashboard is the fallback." }),
      a.detection.errors.length > 0 && /* @__PURE__ */ e("ul", { className: "text-sm text-danger mb-3 list-disc pl-5", children: a.detection.errors.map((x) => /* @__PURE__ */ e("li", { children: x }, x)) }),
      /* @__PURE__ */ e("div", { className: "grid gap-3", children: Object.keys(K).map((x) => /* @__PURE__ */ e($e, { r: t, state: a.roles[x], onBind: (v) => f(x, v) }, x)) })
    ] }),
    /* @__PURE__ */ e(Ae, { r: t, setup: a, onSaved: (x) => c({ ...a, scheduler: x }) }),
    /* @__PURE__ */ e(De, { r: t, setup: a, version: n }),
    /* @__PURE__ */ e(ze, { sources: o })
  ] }) : /* @__PURE__ */ e("p", { className: "text-sm text-muted", children: "Loading…" });
}
function Se({ r: t, setup: n, onLoaded: r }) {
  var w;
  const l = z(), a = n.manifest, [c, o] = h(n.manifestPath ?? ((w = a.inUse) == null ? void 0 : w.path) ?? ""), [b, u] = h(!1), d = async () => {
    var i;
    u(!0);
    try {
      const f = await t.loadManifest(c.trim());
      r({ ...n, manifest: f, manifestPath: f.pathSaved ? c.trim() : n.manifestPath }), l((i = f.lastAttempt) != null && i.ok ? "Connector manifest loaded" : "The manifest has errors; see below");
    } catch (f) {
      l(k(f));
    } finally {
      u(!1);
    }
  }, m = a.lastAttempt;
  return /* @__PURE__ */ s(D, { children: [
    /* @__PURE__ */ e(E, { children: "Connector manifest" }),
    /* @__PURE__ */ s("div", { className: "flex gap-2 mb-2", children: [
      /* @__PURE__ */ e(
        R,
        {
          value: c,
          onChange: (i) => o(i.target.value),
          placeholder: "/absolute/path/to/integration-radar.manifest.yaml",
          "aria-label": "Connector manifest path"
        }
      ),
      /* @__PURE__ */ e(N, { primary: !0, onClick: d, disabled: b || !c.trim(), children: b ? "Loading…" : "Load" })
    ] }),
    /* @__PURE__ */ e("p", { className: "text-[12px] text-muted mb-3", children: "Every sweep re-reads this file. If it becomes invalid, the last valid manifest stays in use." }),
    a.inUse ? /* @__PURE__ */ s("div", { className: "text-sm", children: [
      /* @__PURE__ */ s("div", { children: [
        "In use: ",
        /* @__PURE__ */ e("code", { className: "text-[12px]", children: a.inUse.path }),
        " ",
        "·",
        " ",
        a.inUse.partners.length,
        " partners,",
        " ",
        a.inUse.connectors.length,
        " connectors"
      ] }),
      /* @__PURE__ */ s("div", { className: "text-muted text-[12px] mt-1", children: [
        "Allowed hosts: ",
        a.inUse.allowedHosts.join(", ")
      ] }),
      /* @__PURE__ */ e("ul", { className: "mt-2 grid gap-1", children: a.inUse.connectors.map((i) => /* @__PURE__ */ s("li", { className: "text-[13px]", children: [
        /* @__PURE__ */ e("code", { className: "text-[12px]", children: i.id }),
        " ",
        /* @__PURE__ */ s("span", { className: "text-muted", children: [
          i.partner,
          " ",
          i.version,
          " ",
          "·",
          " ",
          i.criticality
        ] }),
        " ",
        i.lifecycle !== "active" && /* @__PURE__ */ e($, { variant: "warn", children: i.action })
      ] }, i.id)) })
    ] }) : /* @__PURE__ */ e("p", { className: "text-sm text-muted", children: "No connector manifest loaded yet. Start from examples/integration-radar.manifest.yaml." }),
    a.usingFallback && /* @__PURE__ */ e("p", { className: "text-sm text-warn mt-3", children: "The latest version of the file is invalid; the last valid manifest is still in use." }),
    m && m.errors.length > 0 && /* @__PURE__ */ s("div", { className: "mt-3", children: [
      /* @__PURE__ */ s("div", { className: "text-sm font-medium text-danger mb-1", children: [
        m.errors.length,
        " error",
        m.errors.length === 1 ? "" : "s",
        " in ",
        m.path
      ] }),
      /* @__PURE__ */ e("ul", { className: "grid gap-0.5 font-mono text-[12px]", children: m.errors.map((i, f) => /* @__PURE__ */ s("li", { children: [
        /* @__PURE__ */ e("span", { className: "text-danger", children: i.line ? `line ${i.line}` : "file" }),
        i.path ? ` ${i.path}` : "",
        ": ",
        i.message
      ] }, f)) })
    ] }),
    m && m.notices.length > 0 && /* @__PURE__ */ e("ul", { className: "mt-2 grid gap-0.5 text-[12px] text-muted", children: m.notices.map((i, f) => /* @__PURE__ */ s("li", { children: [
      "line ",
      i.line,
      ": ",
      i.message
    ] }, f)) })
  ] });
}
function F(t) {
  return t.kind === "interface" ? `${t.name} (Crew interface)` : `${t.name} (MCP server)`;
}
function $e({ r: t, state: n, onBind: r }) {
  var f, x, v, p, T, U;
  const l = z(), a = n.candidates.filter((g) => g.usable && g.deliverable), c = (f = n.binding) == null ? void 0 : f.candidate, [o, b] = h(c ?? n.proposal ?? ((x = a[0]) == null ? void 0 : x.id) ?? ""), [u, d] = h(null), [m, w] = h(((p = (v = n.binding) == null ? void 0 : v.destination) == null ? void 0 : p.id) ?? "");
  A(() => {
    d(null), !(n.role !== "chat" || !o) && t.destinations(o).then((g) => {
      var L;
      d(g), g.some((I) => I.id === m) || w(((L = g[0]) == null ? void 0 : L.id) ?? "");
    }).catch((g) => l(`Could not list destinations: ${k(g)}`));
  }, [t, n.role, o]);
  const i = () => r(n.role === "chat" ? { candidate: o, destination: { id: m } } : { candidate: o });
  return /* @__PURE__ */ s("div", { className: "border border-border rounded-md p-3", children: [
    /* @__PURE__ */ s("div", { className: "flex flex-wrap items-center gap-2 mb-1", children: [
      /* @__PURE__ */ e("span", { className: "font-medium w-[64px]", children: K[n.role] }),
      /* @__PURE__ */ e($, { variant: ye[n.status], children: Ne[n.status] }),
      c && /* @__PURE__ */ s("span", { className: "text-sm", children: [
        c,
        (T = n.binding) != null && T.destination ? ` → ${n.binding.destination.name}` : ""
      ] })
    ] }),
    n.reason && /* @__PURE__ */ e("p", { className: "text-[12px] text-muted mb-2", children: n.reason }),
    n.candidates.length > 0 && /* @__PURE__ */ e("ul", { className: "text-[12px] text-muted mb-2 grid gap-0.5", children: n.candidates.map((g) => /* @__PURE__ */ s("li", { children: [
      F(g),
      " ",
      "·",
      " ",
      g.status,
      g.evidence.length ? ` · e.g. ${g.evidence.join(", ")}` : "",
      g.note ? ` · ${g.note}` : ""
    ] }, g.id)) }),
    /* @__PURE__ */ s("div", { className: "flex flex-wrap items-center gap-2", children: [
      a.length > 0 && /* @__PURE__ */ e("select", { className: j, value: o, onChange: (g) => b(g.target.value), "aria-label": `${n.role} tool`, children: a.map((g) => /* @__PURE__ */ e("option", { value: g.id, children: F(g) }, g.id)) }),
      n.role === "chat" && a.length > 0 && /* @__PURE__ */ e(
        "select",
        {
          className: j,
          value: m,
          onChange: (g) => w(g.target.value),
          disabled: !u,
          "aria-label": "Chat destination",
          children: (u ?? []).map((g) => /* @__PURE__ */ e("option", { value: g.id, children: g.name }, g.id))
        }
      ),
      a.length > 0 && /* @__PURE__ */ e(N, { primary: !0, onClick: i, disabled: !o || n.role === "chat" && !m, children: c ? "Update" : "Confirm" }),
      n.status !== "off" || n.binding ? (U = n.binding) != null && U.off ? /* @__PURE__ */ e(N, { onClick: () => r({ candidate: null }), children: "Back to automatic" }) : /* @__PURE__ */ e(N, { onClick: () => r({ off: !0 }), children: "Switch off" }) : null
    ] })
  ] });
}
function Ae({ r: t, setup: n, onSaved: r }) {
  const l = z(), [a, c] = h(n.scheduler.schedule), o = (d) => c({ ...a, ...d }), b = async () => {
    try {
      r(await t.saveSchedule(a)), l("Schedule saved");
    } catch (d) {
      l(k(d));
    }
  }, u = n.roles.chat.status === "bound";
  return /* @__PURE__ */ s(D, { children: [
    /* @__PURE__ */ e(E, { children: "Schedule" }),
    /* @__PURE__ */ s("div", { className: "grid gap-3 text-sm", children: [
      /* @__PURE__ */ s("label", { className: "flex flex-wrap items-center gap-2", children: [
        "Daily sweep at",
        /* @__PURE__ */ e(R, { type: "time", value: a.sweepTime, onChange: (d) => o({ sweepTime: d.target.value }), className: "max-w-[120px]" }),
        "in timezone",
        /* @__PURE__ */ e(R, { value: a.timezone, onChange: (d) => o({ timezone: d.target.value }), className: "max-w-[200px]" })
      ] }),
      /* @__PURE__ */ s("label", { className: "inline-flex items-center gap-2", children: [
        /* @__PURE__ */ e(
          B,
          {
            checked: a.digestEnabled,
            onChange: (d) => o({ digestEnabled: d }),
            label: "Weekly digest"
          }
        ),
        u ? "Weekly digest to the bound chat" : "Weekly digest (dashboard only until chat is bound)"
      ] }),
      a.digestEnabled && /* @__PURE__ */ s("label", { className: "flex flex-wrap items-center gap-2", children: [
        "Send on",
        /* @__PURE__ */ e("select", { className: j, value: a.digestDay, onChange: (d) => o({ digestDay: d.target.value }), children: Ce.map((d) => /* @__PURE__ */ e("option", { value: d, children: d }, d)) }),
        "at",
        /* @__PURE__ */ e(R, { type: "time", value: a.digestTime, onChange: (d) => o({ digestTime: d.target.value }), className: "max-w-[120px]" })
      ] }),
      /* @__PURE__ */ s("div", { className: "flex items-center gap-3", children: [
        /* @__PURE__ */ e(N, { primary: !0, onClick: b, children: "Save schedule" }),
        /* @__PURE__ */ s("span", { className: "text-[12px] text-muted", children: [
          "Next sweep ",
          n.scheduler.nextSweepAt.replace("T", " "),
          " UTC",
          n.scheduler.lastScheduledSweepError ? ` · last scheduled sweep: ${n.scheduler.lastScheduledSweepError}` : ""
        ] })
      ] })
    ] })
  ] });
}
const Te = {
  delivered: "ok",
  dashboard: "muted",
  queued: "warn",
  sending: "aim",
  failed: "err"
}, W = {
  delivered: "delivered",
  dashboard: "dashboard only",
  queued: "queued",
  sending: "sending",
  failed: "failed"
};
function Le(t) {
  var r, l, a;
  const n = t.roles.chat;
  return n.status === "bound" ? `Goes to ${(r = n.binding) == null ? void 0 : r.candidate} → ${(a = (l = n.binding) == null ? void 0 : l.destination) == null ? void 0 : a.name}.` : n.status === "degraded" ? "Chat is unavailable; digests are queued and sent when it is back." : "Chat is not bound, so digests stay in this dashboard. Nothing is posted until you bind chat above.";
}
function De({ r: t, setup: n, version: r }) {
  const l = z(), [a, c] = h(null), [o, b] = h(null), [u, d] = h(null), [m, w] = h(!1), [i, f] = h(0);
  A(() => {
    t.digests().then(c).catch(() => c([]));
  }, [t, r, i]);
  const x = async () => {
    try {
      b((await t.digestPreview()).text);
    } catch (p) {
      l(k(p));
    }
  }, v = async () => {
    w(!0);
    try {
      const p = await t.digestSend();
      "id" in p ? l(`Digest ${W[p.status]}${p.error ? `: ${p.error}` : ""}`) : l("Digest is being sent…"), f((T) => T + 1), setTimeout(() => f((T) => T + 1), 15e3);
    } catch (p) {
      l(k(p));
    } finally {
      w(!1);
    }
  };
  return /* @__PURE__ */ s(D, { children: [
    /* @__PURE__ */ e(E, { children: "Weekly digest" }),
    /* @__PURE__ */ s("p", { className: "text-sm text-muted mb-3", children: [
      Le(n),
      " It lists matched changes awaiting triage using partner names, versions, dates and connector ids only; partner wording stays in this dashboard."
    ] }),
    /* @__PURE__ */ s("div", { className: "flex gap-2 mb-3", children: [
      /* @__PURE__ */ e(N, { onClick: x, children: "Preview" }),
      /* @__PURE__ */ e(N, { primary: !0, onClick: v, disabled: m, children: m ? "Sending…" : "Send now" })
    ] }),
    a && a.length > 0 && /* @__PURE__ */ e("ul", { className: "grid gap-1 text-sm", children: a.map((p) => /* @__PURE__ */ s("li", { className: "flex flex-wrap items-baseline gap-2", children: [
      /* @__PURE__ */ e($, { variant: Te[p.status], children: W[p.status] }),
      /* @__PURE__ */ s("span", { className: "text-muted text-[12px]", children: [
        p.createdAt.replace("T", " ").slice(0, 16),
        " UTC ",
        "·",
        " ",
        p.trigger,
        p.destination ? ` · ${p.destination}` : ""
      ] }),
      p.error && /* @__PURE__ */ e("span", { className: "text-[12px] text-danger", children: p.error }),
      /* @__PURE__ */ e("button", { className: "underline text-[12px] text-muted bg-transparent border-none p-0 cursor-pointer", onClick: () => d(p), children: "view" })
    ] }, p.id)) }),
    /* @__PURE__ */ e(M, { open: o !== null, onClose: () => b(null), title: "Digest preview", maxWidth: 760, children: /* @__PURE__ */ e("pre", { className: "text-[12px] whitespace-pre-wrap font-mono", children: o }) }),
    /* @__PURE__ */ e(M, { open: u !== null, onClose: () => d(null), title: `Digest of ${(u == null ? void 0 : u.createdAt.slice(0, 10)) ?? ""}`, maxWidth: 760, children: /* @__PURE__ */ e("pre", { className: "text-[12px] whitespace-pre-wrap font-mono", children: u == null ? void 0 : u.text }) })
  ] });
}
function ze({ sources: t }) {
  return /* @__PURE__ */ s(D, { children: [
    /* @__PURE__ */ e(E, { children: "Sources" }),
    !t || t.length === 0 ? /* @__PURE__ */ e("p", { className: "text-sm text-muted", children: "No sweep has run yet." }) : /* @__PURE__ */ e("ul", { className: "grid gap-1.5 text-sm", children: t.map((n) => /* @__PURE__ */ s("li", { className: "flex flex-wrap items-baseline gap-2", children: [
      /* @__PURE__ */ e($, { variant: n.stale ? "err" : n.status === "ok" ? "ok" : n.status === "failing" ? "warn" : "muted", children: n.stale ? "stale" : n.status }),
      /* @__PURE__ */ e("span", { className: "w-[90px] shrink-0", children: n.partner }),
      /* @__PURE__ */ e("span", { className: "text-muted w-[120px] shrink-0", children: n.sourceKind }),
      /* @__PURE__ */ e("a", { href: n.sourceUrl, target: "_blank", rel: "noreferrer noopener", className: "truncate min-w-0 flex-1 hover:text-accent", children: n.sourceUrl }),
      n.status === "ok" && /* @__PURE__ */ s("span", { className: "text-[12px] text-muted", children: [
        n.itemsLastRun,
        " items"
      ] }),
      n.lastError && /* @__PURE__ */ e("span", { className: "text-[12px] text-danger basis-full pl-[100px]", children: n.lastError })
    ] }, `${n.partner} ${n.sourceUrl}`)) })
  ] });
}
function Ie() {
  const t = Z(), n = H(() => de(t), [t]), r = z(), l = ee(), [a, c] = h("feed"), [o, b] = h(null), [u, d] = h(!1), [m, w] = h(0), i = Q(() => w((v) => v + 1), []);
  A(() => {
    n.summary().then((v) => {
      b(v), l(v.untriaged);
    }).catch(() => b(null));
  }, [n, m, l]);
  const f = async () => {
    d(!0);
    try {
      const v = await n.sweep();
      if (v.status === "running" || !v.outcomes)
        r("Sweep is still running; the feed refreshes when it finishes"), setTimeout(i, 2e4);
      else {
        const p = v.outcomes;
        r(`Sweep done: ${p.new ?? 0} new, ${p.updated ?? 0} updated, ${p.merged ?? 0} merged`);
      }
      i();
    } catch (v) {
      r(`Sweep failed: ${k(v)}`);
    } finally {
      d(!1);
    }
  }, x = o ? `${o.untriaged} to triage` + (o.urgent ? `, ${o.urgent} urgent` : "") + (o.lastSweepAt ? ` · last sweep ${o.lastSweepAt.replace("T", " ").slice(0, 16)} UTC` : "") : "Partner API changes mapped to the connectors you use";
  return /* @__PURE__ */ s(O, { children: [
    /* @__PURE__ */ e(
      te,
      {
        title: "Integration Radar",
        subtitle: x,
        actions: /* @__PURE__ */ s("div", { className: "flex items-center gap-2", children: [
          o != null && o.failedLessons ? /* @__PURE__ */ s(
            N,
            {
              title: "Some dismissal lessons could not be saved to Crew; retry",
              onClick: async () => {
                try {
                  await n.deliverLessons(), r("Saving lessons to Crew…"), setTimeout(i, 15e3);
                } catch (v) {
                  r(k(v));
                }
              },
              children: [
                o.failedLessons,
                " lesson",
                o.failedLessons === 1 ? "" : "s",
                " not saved ",
                "·",
                " retry"
              ]
            }
          ) : null,
          o != null && o.stalePartners.length ? /* @__PURE__ */ s($, { variant: "warn", title: "A source failed twice in a row; see Setup", children: [
            "stale: ",
            o.stalePartners.join(", ")
          ] }) : null,
          /* @__PURE__ */ s(N, { onClick: f, disabled: u || (o == null ? void 0 : o.manifestLoaded) === !1, children: [
            /* @__PURE__ */ e(G, { size: 14, className: u ? "animate-spin" : "" }),
            u ? "Sweeping…" : "Sweep now"
          ] })
        ] })
      }
    ),
    /* @__PURE__ */ s("div", { className: "px-6 pb-8 overflow-y-auto flex-1 min-h-0", children: [
      /* @__PURE__ */ e("div", { className: "mb-4", children: /* @__PURE__ */ e(
        V,
        {
          segments: [
            { key: "feed", label: "Feed", count: o == null ? void 0 : o.untriaged },
            { key: "calendar", label: "Calendar" },
            { key: "setup", label: "Setup" }
          ],
          value: a,
          onChange: c,
          collapse: !1
        }
      ) }),
      a === "feed" && /* @__PURE__ */ e(he, { r: n, version: m, onChanged: i, goToSetup: () => c("setup") }),
      a === "calendar" && /* @__PURE__ */ e(we, { r: n, version: m }),
      a === "setup" && /* @__PURE__ */ e(ke, { r: n, version: m, onChanged: i })
    ] })
  ] });
}
export {
  Ie as default
};
