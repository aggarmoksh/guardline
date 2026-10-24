import Link from "next/link";

export default function Home() {
  return (
    <main className="mx-auto flex min-h-dvh max-w-md flex-col justify-center gap-4 p-6">
      <h1 className="text-2xl font-semibold">GuardLine</h1>
      <p className="text-sm opacity-70">Sandbox data only. Calls and approvals are simulated.</p>
      <Link className="rounded-xl bg-blue-600 p-4 text-center text-white" href="/call">Phone: /call</Link>
      <Link className="rounded-xl border p-4 text-center" href="/approve">Simulated banking app: /approve</Link>
    </main>
  );
}
