import Link from "next/link";

const agents = [
  ["Technical SEO", "Crawl and indexability"],
  ["Content SEO", "Intent and content quality"],
  ["Keyword Opportunity", "GSC demand signals"],
  ["Internal Linking", "Authority and discovery"],
  ["SEO Content", "Evidence-bound proposals"],
  ["Performance", "Page experience"],
  ["GEO visibility", "Entity and answer clarity"],
] as const;
const stages = [
  "Connect site",
  "Crawl evidence",
  "Top 20",
  "Approve fixes",
  "Deploy safely",
  "Track results",
];

export default function Home() {
  return (
    <main className="min-h-dvh bg-slate-50">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-7xl items-center justify-between px-6 py-4">
          <div>
            <p className="text-sm font-semibold text-emerald-700">SEO Autopilot</p>
            <p className="text-sm text-slate-500">Evidence to measured change</p>
          </div>
          <span className="rounded-full border border-amber-300 bg-amber-50 px-3 py-1 text-sm font-medium text-amber-900">
            Observe mode
          </span>
        </div>
      </header>
      <div className="mx-auto max-w-7xl px-6 py-12">
        <section className="grid gap-8 lg:grid-cols-[1.4fr_1fr]">
          <div>
            <p className="mb-3 text-sm font-semibold text-emerald-700">Production foundation</p>
            <h1 className="max-w-3xl text-balance text-4xl font-semibold text-slate-950 sm:text-5xl">
              Your highest-value SEO work, explained and controlled.
            </h1>
            <p className="mt-5 max-w-2xl text-pretty text-lg leading-8 text-slate-600">
              Connect crawl, Search Console, analytics, and delivery data. Rank opportunities,
              review exact changes, then deploy through policy with a complete audit trail.
            </p>
            <Link
              href="/pilot"
              className="mt-8 inline-flex rounded-lg bg-emerald-700 px-4 py-2.5 text-sm font-semibold text-white shadow-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-emerald-700"
            >
              Connect your first site
            </Link>
          </div>
          <aside className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
            <p className="text-sm font-medium text-slate-500">Readiness</p>
            <p className="mt-1 text-3xl font-semibold tabular-nums text-slate-950">0 / 6</p>
            <ol className="mt-6 space-y-3">
              {stages.map((stage, index) => (
                <li key={stage} className="flex items-center gap-3 text-sm text-slate-700">
                  <span className="flex size-7 items-center justify-center rounded-full border border-slate-300 font-medium tabular-nums">
                    {index + 1}
                  </span>
                  {stage}
                </li>
              ))}
            </ol>
          </aside>
        </section>
        <section className="mt-14">
          <h2 className="text-balance text-2xl font-semibold text-slate-950">
            Seven agents, one governed workflow
          </h2>
          <p className="mt-2 text-pretty text-sm text-slate-500">
            Agents create cited findings. Policy and people control production effects.
          </p>
          <div className="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {agents.map(([name, purpose]) => (
              <article key={name} className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
                <h3 className="text-balance font-semibold">{name}</h3>
                <p className="mt-2 text-pretty text-sm leading-6 text-slate-600">{purpose}</p>
                <p className="mt-5 text-xs font-medium text-slate-500">Awaiting site evidence</p>
              </article>
            ))}
          </div>
        </section>
      </div>
    </main>
  );
}
