"use client";

import { useRef, useState } from "react";
import { pickMime, playBase64Wav, toWav16k, unlockAudio } from "@/lib/audio";

type Line = { who: "agent" | "caller"; text: string };
type TurnResp = {
  transcript?: string;
  agent_text: string;
  agent_audio: string | null;
  done?: boolean;
  outcome: string | null;
  language: "en" | "es";
  awaiting_approval: boolean;
  expect?: "name" | "dob" | "zip" | "ssn_last4" | null;
  case_id?: string;
};

const KEYPAD_FIELDS = {
  dob: { label: "Date of birth (MMDDYYYY)", length: 8, mask: false },
  zip: { label: "ZIP code", length: 5, mask: false },
  ssn_last4: { label: "Last 4 of SSN", length: 4, mask: true },
} as const;

const OUTCOME_LABEL: Record<string, string> = {
  verified_handoff: "Verified. Passed to a team member.",
  unverified_handoff: "Passed to a team member (not verified).",
  escalated_high: "Escalated to a team member.",
  ended_safe: "Call ended.",
};

export default function Call() {
  const [started, setStarted] = useState(false);
  const [callingFrom, setCallingFrom] = useState<"member" | "other">("member");
  const [lang, setLang] = useState<"en" | "es">("en");
  const [persona, setPersona] = useState("P01");
  const [lines, setLines] = useState<Line[]>([]);
  const [status, setStatus] = useState("");
  const [recording, setRecording] = useState(false); // button held
  const [capturing, setCapturing] = useState(false); // mic is actually delivering audio
  const [busy, setBusy] = useState(false);
  const [outcome, setOutcome] = useState<string | null>(null);
  const [awaiting, setAwaiting] = useState(false);
  const [expect, setExpect] = useState<TurnResp["expect"]>(null);
  const [typed, setTyped] = useState("");
  const [caseIdShown, setCaseIdShown] = useState("");
  const caseId = useRef("");
  const stream = useRef<MediaStream | null>(null);
  const recorder = useRef<MediaRecorder | null>(null);
  const chunks = useRef<Blob[]>([]);
  const langRef = useRef<"en" | "es">("en");

  const setLanguage = (l: "en" | "es") => {
    langRef.current = l;
    setLang(l);
  };

  const applyResp = async (r: TurnResp) => {
    if (r.transcript) setLines((x) => [...x, { who: "caller", text: r.transcript! }]);
    setLines((x) => [...x, { who: "agent", text: r.agent_text }]);
    setOutcome(r.outcome);
    setAwaiting(r.awaiting_approval);
    setExpect(r.expect ?? null);
    setTyped("");
    if (r.agent_audio) await playBase64Wav(r.agent_audio);
  };

  const start = async () => {
    setBusy(true);
    setStatus("Starting...");
    try {
      await unlockAudio(); // inside the tap: iOS audio unlock
      stream.current = await navigator.mediaDevices.getUserMedia({ audio: true });
      const res = await fetch("/api/call/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ persona, calling_from: callingFrom, language: langRef.current }),
      });
      if (!res.ok) throw new Error("start failed");
      const r: TurnResp = await res.json();
      caseId.current = r.case_id!;
      setCaseIdShown(r.case_id!);
      setStarted(true);
      setStatus("");
      await applyResp(r);
    } catch (e) {
      setStatus(`Could not start: ${(e as Error).message}`);
    }
    setBusy(false);
  };

  // Typed details go straight to the policy: not recorded, not transcribed, not shown in the transcript.
  const submitTyped = async () => {
    if (!expect || expect === "name") return;
    setBusy(true);
    setStatus("Checking...");
    try {
      const res = await fetch("/api/entry", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ case_id: caseId.current, field: expect, value: typed }),
      });
      if (!res.ok) throw new Error(`entry failed (${res.status})`);
      const r: TurnResp = await res.json();
      setStatus("");
      await applyResp(r);
    } catch (e) {
      setStatus(`Error: ${(e as Error).message}`);
    }
    setBusy(false);
  };

  const press = () => {
    if (busy || outcome || !stream.current) return;
    chunks.current = [];
    const mime = pickMime();
    const rec = new MediaRecorder(stream.current, mime ? { mimeType: mime } : undefined);
    rec.ondataavailable = (e) => e.data.size && chunks.current.push(e.data);
    rec.onstart = () => setCapturing(true); // only now is the first word safe to say
    rec.start();
    recorder.current = rec;
    setRecording(true);
    setStatus("");
  };

  const release = async () => {
    const rec = recorder.current;
    if (!rec || rec.state !== "recording") return;
    setRecording(false);
    setCapturing(false);
    setBusy(true);
    setStatus("Thinking...");
    const stopped = new Promise<void>((res) => (rec.onstop = () => res()));
    rec.stop();
    await stopped;
    try {
      const wav = await toWav16k(new Blob(chunks.current, { type: rec.mimeType }));
      const form = new FormData();
      form.append("case_id", caseId.current);
      form.append("language", langRef.current);
      form.append("audio", wav, "turn.wav");
      const res = await fetch("/api/turn", { method: "POST", body: form });
      if (!res.ok) throw new Error(`turn failed (${res.status})`);
      const r: TurnResp = await res.json();
      if (r.language) setLanguage(r.language);
      setStatus("");
      await applyResp(r);
    } catch (e) {
      setStatus(`Error: ${(e as Error).message}`);
    }
    setBusy(false);
  };

  const chip = (
    <button
      onClick={() => setLanguage(lang === "en" ? "es" : "en")}
      className="rounded-full border px-4 py-1 text-sm font-semibold"
      aria-label="Tap to change language"
    >
      {lang === "en" ? "EN · English" : "ES · Español"}
    </button>
  );

  if (!started) {
    return (
      <main className="mx-auto flex min-h-dvh max-w-md flex-col gap-5 p-6">
        <h1 className="text-2xl font-semibold">GuardLine</h1>
        <p className="text-sm opacity-70">Credit union phone line. Sandbox data only.</p>
        <fieldset className="flex flex-col gap-2 rounded-xl border p-4">
          <legend className="px-1 text-xs font-semibold uppercase text-amber-600">Simulated caller ID</legend>
          {(["member", "other"] as const).map((v) => (
            <label key={v} className="flex items-center gap-3 text-lg">
              <input type="radio" checked={callingFrom === v} onChange={() => setCallingFrom(v)} />
              {v === "member" ? "Member phone (simulated)" : "Other phone (simulated)"}
            </label>
          ))}
        </fieldset>
        <label className="flex items-center gap-3 text-sm">
          Demo member
          <select className="rounded border p-1 text-black" value={persona} onChange={(e) => setPersona(e.target.value)}>
            {["P01", "P13"].map((p) => <option key={p}>{p}</option>)}
          </select>
        </label>
        <div>{chip}</div>
        <button disabled={busy} onClick={start} className="rounded-2xl bg-blue-600 p-5 text-xl font-semibold text-white disabled:opacity-50">
          Start call
        </button>
        {status && <p className="text-sm">{status}</p>}
      </main>
    );
  }

  return (
    <main className="mx-auto flex min-h-dvh max-w-md select-none flex-col gap-3 p-4" style={{ WebkitTouchCallout: "none" }}>
      <header className="flex items-center justify-between">
        <span className="text-xs font-semibold uppercase text-amber-600">
          Simulated caller: {callingFrom === "member" ? "member phone" : "other phone"}
        </span>
        {chip}
      </header>
      <section className="flex-1 space-y-2 overflow-y-auto">
        {lines.map((l, i) => (
          <p key={i} className={`max-w-[85%] rounded-2xl px-3 py-2 ${l.who === "agent" ? "bg-gray-200 text-black" : "ml-auto bg-blue-600 text-white"}`}>
            {l.text}
          </p>
        ))}
      </section>
      {awaiting && <p className="rounded-xl border-2 border-amber-500 p-3 text-sm">Approval requested in the simulated banking app (/approve). Approve there, then tell the agent.</p>}
      {outcome ? (
        <p className="rounded-xl bg-gray-900 p-4 text-center text-white">{OUTCOME_LABEL[outcome] ?? outcome}</p>
      ) : expect && expect !== "name" ? (
        <div className="flex flex-col gap-2">
          <p className="text-center text-sm">{KEYPAD_FIELDS[expect].label}</p>
          <input
            key={expect}
            autoFocus
            type={KEYPAD_FIELDS[expect].mask ? "password" : "text"}
            inputMode="numeric"
            pattern="[0-9]*"
            autoComplete="off"
            maxLength={KEYPAD_FIELDS[expect].length}
            value={typed}
            onChange={(e) => setTyped(e.target.value.replace(/[^0-9]/g, "").slice(0, KEYPAD_FIELDS[expect].length))}
            onKeyDown={(e) => { if (e.key === "Enter" && typed.length === KEYPAD_FIELDS[expect].length) submitTyped(); }}
            className="rounded-xl border bg-transparent p-4 text-center text-3xl tracking-widest"
          />
          <button
            disabled={busy || typed.length !== KEYPAD_FIELDS[expect].length}
            onClick={submitTyped}
            className="h-14 rounded-xl bg-green-600 text-2xl font-semibold text-white disabled:opacity-40"
          >
            OK
          </button>
        </div>
      ) : (
        <button
          onPointerDown={(e) => { e.currentTarget.setPointerCapture(e.pointerId); press(); }}
          onPointerUp={release}
          onPointerCancel={release}
          onContextMenu={(e) => e.preventDefault()}
          disabled={busy && !recording}
          style={{ touchAction: "none", WebkitUserSelect: "none", WebkitTouchCallout: "none" }}
          className={`h-32 rounded-full text-xl font-bold text-white ${capturing ? "bg-red-600" : "bg-blue-600"} disabled:opacity-40`}
        >
          {capturing ? "Release to send" : recording ? "Starting mic..." : "Hold to talk"}
        </button>
      )}
      <p className="h-5 text-center text-sm opacity-70">
        {capturing ? <span className="font-semibold text-red-500">● Recording</span> : status}
      </p>
      <p className="text-center text-[10px] opacity-40">Case {caseIdShown}</p>
    </main>
  );
}
