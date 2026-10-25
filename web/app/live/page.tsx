"use client";

import { useEffect, useRef, useState } from "react";

type Bubble = { who: "caller" | "agent"; text: string; ts: number };
type Check = { key: string; label: string; status: "pending" | "ok" | "warn" | "bad" | "na"; text: string; chips?: string[] };
type Banner = { severity: "progress" | "ok" | "warn" | "alert" | "neutral"; headline: string; detail: string; note: string };
type Call = {
  case_id: string;
  persona: string | null;
  channel: "web" | "line";
  known_number: boolean | null;
  language: "en" | "es";
  outcome: string | null;
  state: string;
  transcript: Bubble[];
  checks: Check[];
  banner: Banner;
};
type Stats = { calls: number; impostor_attempts: number; impostors_verified: number; pii_leaks: number };

const ICON: Record<Check["status"], { icon: string; cls: string }> = {
  pending: { icon: "…", cls: "border-slate-600 text-slate-400" },
  ok: { icon: "✓", cls: "border-emerald-500 bg-emerald-500/20 text-emerald-300" },
  warn: { icon: "!", cls: "border-amber-400 bg-amber-400/20 text-amber-300" },
  bad: { icon: "✕", cls: "border-red-500 bg-red-500/20 text-red-300" },
  na: { icon: "–", cls: "border-slate-700 text-slate-500" },
};
const BANNER_CLS: Record<Banner["severity"], string> = {
  progress: "bg-blue-700",
  ok: "bg-emerald-600",
  warn: "bg-amber-500 text-black",
  alert: "bg-red-600",
  neutral: "bg-slate-600",
};

export default function Live() {
  const [key, setKey] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [bad, setBad] = useState(false);
  const [call, setCall] = useState<Call | null>(null);
  const [stats, setStats] = useState<Stats | null>(null);
  const chatEnd = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const fromUrl = new URLSearchParams(window.location.search).get("key");
    const saved = (() => { try { return localStorage.getItem("demo_key"); } catch { return null; } })();
    const k = fromUrl || saved;
    if (fromUrl) {
      try { localStorage.setItem("demo_key", fromUrl); } catch {}
      window.history.replaceState(null, "", "/live"); // keep the key out of the address bar
    }
    setKey(k);
  }, []);

  useEffect(() => {
    if (!key) return;
    let stop = false;
    const tick = async () => {
      try {
        const r = await fetch(`/api/live?key=${encodeURIComponent(key)}`);
        if (r.status === 401) { setBad(true); setKey(null); try { localStorage.removeItem("demo_key"); } catch {} return; }
        const d = await r.json();
        if (!stop) { setCall(d.call); setStats(d.stats); }
      } catch {}
    };
    tick();
    const id = setInterval(tick, 1000);
    return () => { stop = true; clearInterval(id); };
  }, [key]);

  useEffect(() => { chatEnd.current?.scrollIntoView({ behavior: "smooth" }); }, [call?.transcript.length]);

  if (!key) {
    return (
      <main className="mx-auto flex min-h-dvh max-w-sm flex-col justify-center gap-4 bg-slate-950 p-6 text-white">
        <h1 className="text-xl font-semibold">Live call view</h1>
        <p className="text-sm opacity-70">Enter the demo key.{bad && " That key was not accepted."}</p>
        <input type="password" value={draft} onChange={(e) => setDraft(e.target.value)} autoComplete="off"
          className="rounded-lg border border-slate-600 bg-transparent p-3" />
        <button onClick={() => { try { localStorage.setItem("demo_key", draft); } catch {} setBad(false); setKey(draft); }}
          className="rounded-lg bg-blue-600 p-3 font-semibold">Open</button>
      </main>
    );
  }

  const counter = (name: string, value: number | undefined, danger?: boolean) => (
    <div className="flex flex-col items-center rounded-2xl bg-slate-900 px-6 py-2">
      <span className={`text-5xl font-bold ${danger && value ? "text-red-400" : danger ? "text-emerald-400" : ""}`}>{value ?? "-"}</span>
      <span className="text-sm uppercase tracking-wide opacity-60">{name}</span>
    </div>
  );

  return (
    <main className="flex h-dvh flex-col gap-4 overflow-hidden bg-slate-950 p-5 text-white">
      <header className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-4xl font-semibold">GuardLine live</h1>
          <p className="text-lg opacity-60">Bay Area Community CU · sandbox data only · digits and member details are masked</p>
        </div>
        <div className="flex gap-3">
          {counter("Calls", stats?.calls)}
          {counter("Impostor attempts", stats?.impostor_attempts)}
          {counter("Impostors verified", stats?.impostors_verified, true)}
          {counter("PII leaks", stats?.pii_leaks, true)}
        </div>
      </header>

      {!call ? (
        <section className="flex flex-1 items-center justify-center rounded-3xl bg-slate-900 text-4xl opacity-70">
          Waiting for a call…
        </section>
      ) : (
        <>
          <div className="flex flex-wrap gap-3 text-xl">
            {call.persona && <span className="rounded-full bg-slate-800 px-4 py-1 font-semibold">Demo member {call.persona}</span>}
            <span className="rounded-full bg-slate-800 px-4 py-1">{call.channel === "line" ? "Phone line" : "Web call"}</span>
            {call.channel === "line" && (
              <span className={`rounded-full px-4 py-1 font-semibold ${call.known_number ? "bg-emerald-800" : "bg-amber-800"}`}>
                {call.known_number ? "Known number" : "Unknown number"}
              </span>
            )}
            <span className="rounded-full bg-slate-800 px-4 py-1">{call.language === "es" ? "Spanish" : "English"}</span>
          </div>

          <section className="grid min-h-0 flex-1 grid-cols-[1.15fr_1fr] gap-5">
            <div className="flex min-h-0 flex-col gap-3 overflow-y-auto rounded-3xl bg-slate-900 p-5">
              {call.transcript.map((b, i) => (
                <p key={i} className={`max-w-[88%] rounded-3xl px-5 py-3 text-3xl leading-snug ${
                  b.who === "agent" ? "bg-slate-200 text-black" : "ml-auto bg-blue-600 text-white"}`}>
                  {b.text}
                </p>
              ))}
              <div ref={chatEnd} />
            </div>

            <ul className="flex min-h-0 flex-col gap-3 overflow-y-auto rounded-3xl bg-slate-900 p-5">
              {call.checks.map((c) => (
                <li key={c.key} className="flex items-start gap-4">
                  <span className={`flex h-14 w-14 shrink-0 items-center justify-center rounded-full border-2 text-3xl font-bold ${ICON[c.status].cls} ${c.status === "pending" ? "animate-pulse" : ""}`}>
                    {ICON[c.status].icon}
                  </span>
                  <div>
                    <p className="text-xl uppercase tracking-wide text-slate-400">{c.label}</p>
                    {c.key === "tactics" && c.chips && c.chips.length > 0 ? (
                      <div className="mt-1 flex flex-wrap gap-2">
                        {c.chips.map((t) => <span key={t} className="rounded-full bg-purple-800 px-4 py-1 text-2xl font-semibold">{t}</span>)}
                      </div>
                    ) : (
                      <p className="text-3xl font-medium leading-tight">{c.text}</p>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          </section>

          <section className={`rounded-3xl px-8 py-5 text-center ${BANNER_CLS[call.banner.severity]}`}>
            <p className="text-6xl font-extrabold tracking-wide">{call.banner.headline.toUpperCase()}</p>
            {call.banner.detail && <p className="mt-1 text-4xl font-semibold">{call.banner.detail}</p>}
            {call.banner.note && <p className="mt-2 text-2xl opacity-90">{call.banner.note}</p>}
          </section>
        </>
      )}
    </main>
  );
}
