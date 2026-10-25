"use client";

import { useCallback, useEffect, useRef, useState } from "react";

type Request = {
  case_id: string;
  language: "en" | "es";
  calling_from: "member" | "other";
  requested_at: number;
  deadline: number;
};

const LANGUAGE = { en: "English", es: "Spanish" } as const;

export default function MemberApp() {
  const [req, setReq] = useState<Request | null>(null);
  const [left, setLeft] = useState(60);
  const [answer, setAnswer] = useState<string | null>(null);
  const [alertsOn, setAlertsOn] = useState(false);
  const [notice, setNotice] = useState<{ case_id: string; ended_at: number } | null>(null);
  const dismissed = useRef("");
  const offset = useRef(0); // server clock minus phone clock, in seconds
  const ctx = useRef<AudioContext | null>(null);
  const alertsRef = useRef(false);

  // Sound needs a user gesture on iOS, so alerts are switched on with one tap.
  const enableAlerts = async () => {
    const AC = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
    ctx.current = new AC();
    await ctx.current.resume();
    alertsRef.current = true;
    setAlertsOn(true);
    ring();
  };

  const ring = () => {
    navigator.vibrate?.([300, 150, 300, 150, 600]); // not available on iPhone Safari; the sound is the backup
    const c = ctx.current;
    if (!c) return;
    [0, 0.25, 0.5].forEach((t) => {
      const osc = c.createOscillator();
      const gain = c.createGain();
      osc.frequency.value = 880;
      gain.gain.setValueAtTime(0.25, c.currentTime + t);
      gain.gain.exponentialRampToValueAtTime(0.001, c.currentTime + t + 0.2);
      osc.connect(gain).connect(c.destination);
      osc.start(c.currentTime + t);
      osc.stop(c.currentTime + t + 0.22);
    });
  };

  const show = useCallback((r: Request) => {
    setReq(r);
    setAnswer(null);
    if (alertsRef.current) ring();
  }, []);

  const lastCase = useRef("");
  useEffect(() => {
    // Polling is the fallback: quick tunnels do not carry server-sent events.
    const load = async () => {
      try {
        const r = await (await fetch("/api/approval")).json();
        offset.current = r.now - Date.now() / 1000;
        if (r.pending && r.case_id !== lastCase.current) {
          lastCase.current = r.case_id;
          show(r);
        } else if (!r.pending) {
          setReq(null);
        }
        // a call that ended without verification: the credit union will call back (simulated notice)
        const n = await (await fetch("/api/notice")).json();
        setNotice(n.notice && n.case_id !== dismissed.current ? n : null);
      } catch {}
    };
    load();
    const poll = setInterval(load, 1500);
    const es = new EventSource("/api/events");
    es.onmessage = (m) => {
      const ev = JSON.parse(m.data);
      if (ev.type === "approval_requested") {
        load();
      } else if (ev.type === "approval_resolved" || ev.type === "approval_cancelled") {
        setReq((cur) => (cur && cur.case_id === ev.case_id ? null : cur));
      }
    };
    return () => { es.close(); clearInterval(poll); };
  }, [show]);

  useEffect(() => {
    if (!req) return;
    const tick = () => setLeft(Math.max(0, Math.ceil(req.deadline - (Date.now() / 1000 + offset.current))));
    tick();
    const id = setInterval(tick, 250);
    return () => clearInterval(id);
  }, [req]);

  const decide = async (decision: "approved" | "denied") => {
    if (!req) return;
    const id = req.case_id;
    setReq(null);
    setAnswer(decision === "approved" ? "You approved the request." : "You denied the request.");
    await fetch("/api/approval", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ case_id: id, decision }),
    });
  };

  const expired = req !== null && left === 0;

  return (
    <main className="flex min-h-dvh flex-col bg-[#0f204a] text-white" style={{ paddingTop: "env(safe-area-inset-top)" }}>
      <div className="bg-amber-400 px-4 py-1 text-center text-xs font-bold uppercase tracking-wide text-black">
        Demo member app (simulated)
      </div>
      <header className="px-6 pb-2 pt-6">
        <p className="text-sm opacity-70">Bay Area Community CU</p>
        <h1 className="text-2xl font-semibold">Security center</h1>
      </header>

      {req ? (
        <section className="flex flex-1 flex-col gap-5 px-6 pb-8">
          <div className="rounded-3xl bg-white p-6 text-[#0f204a]">
            <p className="text-sm font-semibold uppercase tracking-wide text-amber-600">Security request</p>
            <h2 className="mt-1 text-3xl font-bold leading-tight">Is this you calling Bay Area Community CU?</h2>
            <dl className="mt-5 space-y-2 text-base">
              <div className="flex justify-between"><dt className="opacity-60">Call time</dt>
                <dd className="font-semibold">{new Date(req.requested_at * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}</dd></div>
              <div className="flex justify-between"><dt className="opacity-60">Language</dt>
                <dd className="font-semibold">{LANGUAGE[req.language]}</dd></div>
              <div className="flex justify-between"><dt className="opacity-60">Caller ID (simulated)</dt>
                <dd className="font-semibold">{req.calling_from === "member" ? "Your phone" : "Another phone"}</dd></div>
            </dl>
            <div className="mt-5 h-2 overflow-hidden rounded-full bg-gray-200">
              <div className="h-full bg-amber-500 transition-all duration-200" style={{ width: `${(left / 60) * 100}%` }} />
            </div>
            <p className="mt-2 text-center text-sm font-semibold">{expired ? "Expired" : `${left}s left to answer`}</p>
          </div>
          <div className="mt-auto flex flex-col gap-3">
            <button disabled={expired} onClick={() => decide("approved")}
              className="h-20 rounded-2xl bg-green-500 text-2xl font-bold text-white active:scale-95 disabled:opacity-40">
              Yes, it is me
            </button>
            <button disabled={expired} onClick={() => decide("denied")}
              className="h-20 rounded-2xl bg-red-500 text-2xl font-bold text-white active:scale-95 disabled:opacity-40">
              No, deny
            </button>
          </div>
        </section>
      ) : (
        <section className="flex flex-1 flex-col items-center justify-center gap-4 px-6 text-center">
          {notice && (
            <div className="w-full rounded-3xl border-2 border-sky-300 bg-white p-6 text-left text-[#0f204a]">
              <p className="text-sm font-semibold uppercase tracking-wide text-sky-700">Notice (simulated)</p>
              <p className="mt-1 text-2xl font-bold leading-tight">Bay Area Community CU will call you back about a recent call.</p>
              <p className="mt-2 text-sm opacity-70">They will use the number we have on file. No action is needed from you.</p>
              <button onClick={() => { dismissed.current = notice.case_id; setNotice(null); }}
                className="mt-4 rounded-xl bg-[#0f204a] px-5 py-3 font-semibold text-white">OK</button>
            </div>
          )}
          <p className="text-xl font-medium">{answer ?? "No pending requests"}</p>
          <p className="text-sm opacity-60">You will be alerted here when the credit union needs to confirm a call.</p>
          {!alertsOn && (
            <button onClick={enableAlerts} className="mt-4 rounded-2xl bg-white px-6 py-4 text-lg font-semibold text-[#0f204a]">
              Turn on alerts (sound and vibration)
            </button>
          )}
        </section>
      )}
    </main>
  );
}
