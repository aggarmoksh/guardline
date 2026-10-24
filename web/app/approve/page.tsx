"use client";

import { useEffect, useState } from "react";

export default function Approve() {
  const [caseId, setCaseId] = useState<string | null>(null);
  const [last, setLast] = useState<string | null>(null);

  useEffect(() => {
    const tick = async () => {
      try {
        const r = await (await fetch("/api/approval")).json();
        setCaseId(r.pending ? r.case_id : null);
      } catch {}
    };
    tick();
    const id = setInterval(tick, 1500);
    return () => clearInterval(id);
  }, []);

  const decide = async (decision: "approved" | "denied") => {
    if (!caseId) return;
    await fetch("/api/approval", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ case_id: caseId, decision }),
    });
    setLast(decision);
    setCaseId(null);
  };

  return (
    <main className="mx-auto flex min-h-dvh max-w-md flex-col gap-6 p-6">
      <header>
        <p className="text-xs font-semibold uppercase tracking-wide text-amber-600">Simulated member banking app</p>
        <h1 className="text-2xl font-semibold">Security approvals</h1>
      </header>
      {caseId ? (
        <section className="flex flex-col gap-4 rounded-2xl border-2 border-amber-500 p-5">
          <p className="text-lg font-medium">Your credit union is verifying a phone call. Was this you?</p>
          <button onClick={() => decide("approved")} className="rounded-xl bg-green-600 p-4 text-lg font-semibold text-white">Approve</button>
          <button onClick={() => decide("denied")} className="rounded-xl bg-red-600 p-4 text-lg font-semibold text-white">Deny</button>
        </section>
      ) : (
        <p className="opacity-70">No pending requests.{last && ` Last answer: ${last}.`}</p>
      )}
    </main>
  );
}
