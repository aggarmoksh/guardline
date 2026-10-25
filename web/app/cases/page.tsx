"use client";

import { useEffect, useState } from "react";

type Case = {
  case_id: string;
  outcome: string | null;
  risk: string;
  verified: boolean;
  language: "en" | "es";
  state: string;
  calling_from: "member" | "other";
  persona: string | null;
  channel?: "web" | "line";
  known_number?: boolean | null;
  evidence: { product: string; ran_at: number; result: string }[];
  reasons: string[];
  flags: string[];
  tactics: string[];
  created_at: number;
  updated_at: number;
};
type Stats = { calls: number; impostor_attempts: number; impostors_verified: number; pii_leaks: number };

const OUTCOME: Record<string, { label: string; cls: string }> = {
  verified_handoff: { label: "Verified, handed off", cls: "bg-green-600" },
  unverified_handoff: { label: "Unverified, handed off", cls: "bg-amber-500 text-black" },
  escalated_high: { label: "Escalated: high risk", cls: "bg-red-600" },
  ended_safe: { label: "Ended safely", cls: "bg-slate-600" },
};
const RISK_CLS: Record<string, string> = { LOW: "text-green-400", MEDIUM: "text-amber-400", HIGH: "text-red-400" };

const REASON: Record<string, string> = {
  VERIFIED: "Identity verified",
  HANDOFF_WITH_REQUEST: "Handed to a person with the request",
  CONTACT_CHANGE_STEP_UP: "Contact change needs member approval",
  STEP_UP_REQUIRED: "Member approval requested",
  APP_APPROVED_SIMULATED: "Member approved in app (simulated)",
  MEMBER_DENIED_CALL: "Member tapped Deny: not me calling",
  APP_APPROVAL_DENIED: "Member denied the approval",
  CALL_ABANDONED: "Call abandoned (no activity for 3 minutes)",
  APP_APPROVAL_TIMEOUT: "Approval timed out",
  ABANDONED_DURING_STEP_UP: "Caller left while approval was pending",
  REVERSE_PHONE_NAME_DIFFERS: "Calling number belongs to someone else",
  REVERSE_PHONE_NO_RECORD: "Calling number has no record",
  REDIRECT_TO_NEW_NUMBER: "Asked to send the approval to a new number",
  CONTACT_CHANGE_WITH_MONEY_OR_CREDENTIALS: "Contact change together with money or credentials",
  CRS_IDENTITY_MISMATCH_OR_SYNTHETIC: "Bureau: identity mismatch or synthetic",
  CRS_DECEASED_INDICATOR: "Bureau: deceased indicator",
  CRS_FRAUD_ALERT: "Bureau: fraud alert on file",
  CRS_NO_FILE: "Bureau: no file found",
  NAME_FUZZY_MATCH: "Name matched approximately (digits exact)",
  IDENTITY_REENTRY_REQUESTED: "Details did not match; asked to re-enter",
  IDENTITY_NOT_CONFIRMED: "Identity not confirmed after retry",
  NEW_EMAIL_INVALID: "New email address is invalid",
  CALLER_REQUESTED_HUMAN: "Caller asked for a person",
  CALLER_ENDED_CALL: "Caller ended the call",
  TOO_MANY_UNCLEAR_TURNS: "Too many unclear turns",
  LLM_UNAVAILABLE: "AI service unavailable",
};
const FLAG: Record<string, string> = {
  FRAUD_ALERT: "Fraud alert",
  HUMAN_REVIEW: "Human review",
  NO_PHONE_RECORD: "No phone record",
  EMAIL_INVALID: "Invalid email",
  ESCALATED_TO_HUMAN: "Escalated to human",
};
const label = (code: string) => REASON[code] ?? code.toLowerCase().replace(/_/g, " ");
const tactic = (code: string) => code.replace("TACTIC_", "").toLowerCase().replace(/_/g, " ");

function ago(ts: number, now: number) {
  const s = Math.max(0, Math.round(now - ts));
  return s < 60 ? `${s}s ago` : s < 3600 ? `${Math.floor(s / 60)}m ago` : `${Math.floor(s / 3600)}h ago`;
}

export default function Cases() {
  const [key, setKey] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [cases, setCases] = useState<Case[]>([]);
  const [stats, setStats] = useState<Stats | null>(null);
  const [bad, setBad] = useState(false);
  const [now, setNow] = useState(Date.now() / 1000);

  useEffect(() => {
    const fromUrl = new URLSearchParams(window.location.search).get("key");
    const saved = (() => { try { return localStorage.getItem("demo_key"); } catch { return null; } })();
    const k = fromUrl || saved;
    if (fromUrl) {
      try { localStorage.setItem("demo_key", fromUrl); } catch {}
      window.history.replaceState(null, "", "/cases"); // keep the key out of the address bar
    }
    setKey(k);
  }, []);

  useEffect(() => {
    if (!key) return;
    let es: EventSource | null = null;
    (async () => {
      const r = await fetch(`/api/cases?key=${encodeURIComponent(key)}`);
      if (r.status === 401) { setBad(true); setKey(null); try { localStorage.removeItem("demo_key"); } catch {} return; }
      const d = await r.json();
      setCases(d.cases);
      setStats(d.stats);
      es = new EventSource(`/api/cases/stream?key=${encodeURIComponent(key)}`);
      es.onmessage = (m) => {
        const ev = JSON.parse(m.data);
        if (ev.type !== "case_updated") return;
        setStats(ev.stats);
        setCases((cur) => [ev.case, ...cur.filter((c) => c.case_id !== ev.case.case_id)].sort((a, b) => b.created_at - a.created_at));
      };
    })();
    const poll = setInterval(async () => {  // fallback: quick tunnels do not carry server-sent events
      try {
        const r = await fetch(`/api/cases?key=${encodeURIComponent(key)}`);
        if (r.ok) { const d = await r.json(); setCases(d.cases); setStats(d.stats); }
      } catch {}
    }, 3000);
    return () => { es?.close(); clearInterval(poll); };
  }, [key]);

  useEffect(() => {
    const id = setInterval(() => setNow(Date.now() / 1000), 5000);
    return () => clearInterval(id);
  }, []);

  if (!key) {
    return (
      <main className="mx-auto flex min-h-dvh max-w-sm flex-col justify-center gap-4 bg-slate-950 p-6 text-white">
        <h1 className="text-xl font-semibold">Case dashboard</h1>
        <p className="text-sm opacity-70">Enter the demo key.{bad && " That key was not accepted."}</p>
        <input type="password" value={draft} onChange={(e) => setDraft(e.target.value)} autoComplete="off"
          className="rounded-lg border border-slate-600 bg-transparent p-3" />
        <button onClick={() => { try { localStorage.setItem("demo_key", draft); } catch {} setBad(false); setKey(draft); }}
          className="rounded-lg bg-blue-600 p-3 font-semibold">Open</button>
      </main>
    );
  }

  const counter = (name: string, value: number | undefined, good?: boolean) => (
    <div className="flex flex-col items-center rounded-xl bg-slate-900 px-5 py-2">
      <span className={`text-3xl font-bold ${good && value ? "text-red-400" : good ? "text-green-400" : ""}`}>{value ?? "-"}</span>
      <span className="text-xs uppercase tracking-wide opacity-60">{name}</span>
    </div>
  );

  return (
    <main className="flex h-dvh flex-col gap-3 overflow-hidden bg-slate-950 p-4 text-white">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">GuardLine cases</h1>
          <p className="text-xs opacity-60">Bay Area Community CU · live · sandbox data only</p>
        </div>
        <div className="flex gap-2" aria-label="Calls | Impostor attempts | Impostors verified | PII leaks">
          {counter("Calls", stats?.calls)}
          {counter("Impostor attempts", stats?.impostor_attempts)}
          {counter("Impostors verified", stats?.impostors_verified, true)}
          {counter("PII leaks", stats?.pii_leaks, true)}
        </div>
      </header>
      <section className="flex-1 space-y-2 overflow-y-auto">
        {cases.length === 0 && <p className="p-6 text-center opacity-60">No calls yet.</p>}
        {cases.map((c) => {
          const o = c.outcome ? OUTCOME[c.outcome] : { label: "In progress", cls: "bg-blue-600" };
          return (
            <article key={c.case_id} className="rounded-xl bg-slate-900 p-3">
              <div className="flex flex-wrap items-center gap-2">
                <span className={`rounded-full px-3 py-1 text-sm font-semibold ${o.cls}`}>{o.label}</span>
                <span className={`text-sm font-semibold ${RISK_CLS[c.risk] ?? ""}`}>{c.risk} risk</span>
                {c.persona && <span className="rounded bg-slate-700 px-2 py-0.5 text-xs font-semibold">Demo member {c.persona}</span>}
                <span className="rounded bg-slate-800 px-2 py-0.5 text-xs">{c.language === "es" ? "Spanish" : "English"}</span>
                {c.channel === "line" ? (
                  <>
                    <span className="rounded bg-slate-800 px-2 py-0.5 text-xs">Phone line</span>
                    <span className={`rounded px-2 py-0.5 text-xs font-semibold ${c.known_number ? "bg-emerald-900" : "bg-amber-900"}`}>
                      {c.known_number ? "Known number" : "Unknown number"}
                    </span>
                  </>
                ) : (
                  <span className="rounded bg-slate-800 px-2 py-0.5 text-xs">{c.calling_from === "member" ? "member phone (sim.)" : "other phone (sim.)"}</span>
                )}
                <span className="ml-auto text-xs opacity-60">{c.case_id.slice(-8)} · {ago(c.created_at, now)}</span>
              </div>
              {c.evidence?.length > 0 && (
                <div className="mt-2 rounded-lg border border-slate-700 p-2" aria-label="CRS evidence">
                  <p className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-slate-400">CRS evidence</p>
                  <ul className="space-y-0.5 text-xs">
                    {c.evidence.map((e, i) => (
                      <li key={i} className="flex flex-wrap items-baseline gap-x-2">
                        <span className="font-semibold text-sky-300">{e.product}</span>
                        <span className="text-slate-500">{new Date(e.ran_at * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit", second: "2-digit" })}</span>
                        <span>{e.result}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              <ul className="mt-2 flex flex-wrap gap-1.5 text-xs">
                {c.reasons.map((r) => <li key={r} className="rounded bg-slate-800 px-2 py-1">{label(r)}</li>)}
                {c.flags.map((f) => <li key={f} className="rounded bg-amber-900 px-2 py-1">Flag: {FLAG[f] ?? f.toLowerCase()}</li>)}
                {c.tactics.map((t) => <li key={t} className="rounded bg-purple-900 px-2 py-1">Tactic: {tactic(t)}</li>)}
              </ul>
            </article>
          );
        })}
      </section>
    </main>
  );
}
