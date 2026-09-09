import Link from "next/link";

const agents = [
  ["Technical SEO", "Crawl, indexability, canonical tags, and status code evidence.", "Rule-backed"],
  ["Content SEO", "Deterministic checks for titles, descriptions, headings, and thin content.", "Rule-backed"],
  ["Keyword Opportunity", "Search Console demand signals and high-impression page discovery.", "Rule-backed"],
  ["Internal Linking", "Link graph evidence, anchor relevance, and orphan-page detection.", "Rule-backed"],
  ["SEO Content", "Evidence-bound structured proposals with reviewable diff previews.", "Planned"],
  ["Performance", "Repeated mobile Lighthouse lab samples and readiness summaries.", "Rule-backed"],
  ["GEO / AI Visibility", "Entity clarity and structured-data evidence for answer visibility.", "Rule-backed"],
] as const;

const stages = [
  ["Connect site & DNS", "Proven"],
  ["Bounded crawl evidence", "Proven"],
  ["Top 20 opportunity ranking", "In validation"],
  ["Separation-of-duties approval", "In validation"],
  ["Git / CMS deployment", "Certification gate"],
  ["28-day outcome tracking", "In validation"],
];

const faqs = [
  {
    q: "How does SEO Autopilot prevent harmful or hallucinated changes?",
    a: "Current findings are deterministic and retain crawl evidence references. Proposals are schema-validated and reviewable, authors cannot self-approve, and external deployment remains blocked until its connectors pass certification.",
  },
  {
    q: "What does the 28-day measurement currently prove?",
    a: "It reports a before-and-after association only after independent deployment verification and a complete follow-up window. Controlled comparisons and causal attribution remain planned work.",
  },
  {
    q: "What safety kill switches exist in Autopilot mode?",
    a: "Fail-closed deployment flags, site freezes, daily change budgets, freeze windows, approval policy, and drift checks are implemented. External deployment and rollback connectors remain blocked until certified.",
  },
];

export default function Home() {
  const jsonLd = {
    "@context": "https://schema.org",
    "@graph": [
      {
        "@type": "SoftwareApplication",
        "name": "SEO Autopilot",
        "applicationCategory": "BusinessApplication",
        "operatingSystem": "Cloud / Web",
        "description":
          "Auditable multi-tenant SEO operations system connecting crawl evidence, Search Console metrics, reviewable proposals, and outcome tracking.",
      },
      {
        "@type": "Organization",
        "name": "SEO Autopilot",
        "url": "https://seoautopilot.dev",
        "logo": "https://seoautopilot.dev/icon.png",
      },
      {
        "@type": "FAQPage",
        "mainEntity": faqs.map((faq) => ({
          "@type": "Question",
          "name": faq.q,
          "acceptedAnswer": {
            "@type": "Answer",
            "text": faq.a,
          },
        })),
      },
    ],
  };

  return (
    <>
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{__html: JSON.stringify(jsonLd)}}
      />
      <main className="min-h-dvh bg-slate-50 text-slate-900">
        <header className="border-b border-slate-200 bg-white">
          <div className="mx-auto flex max-w-7xl items-center justify-between px-6 py-4">
            <div>
              <p className="text-sm font-semibold text-emerald-700">SEO Autopilot</p>
              <p className="text-xs text-slate-500">Evidence to measured change</p>
            </div>
            <div className="flex items-center gap-3">
              <span className="rounded-full border border-emerald-300 bg-emerald-50 px-3 py-1 text-xs font-medium text-emerald-900">
                Internal alpha
              </span>
              <Link
                href="/pilot"
                className="rounded-lg bg-emerald-700 px-3.5 py-1.5 text-xs font-semibold text-white shadow-sm hover:bg-emerald-600 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-emerald-700"
              >
                Control Plane
              </Link>
            </div>
          </div>
        </header>

        <div className="mx-auto max-w-7xl px-6 py-12">
          <section className="grid gap-8 lg:grid-cols-[1.4fr_1fr]">
            <div>
              <p className="mb-3 text-sm font-semibold text-emerald-700">Governed SEO Operations</p>
              <h1 className="max-w-3xl text-balance text-4xl font-bold tracking-tight text-slate-950 sm:text-5xl">
                Your highest-value SEO work, explained, verified, and controlled.
              </h1>
              <p className="mt-5 max-w-2xl text-pretty text-lg leading-8 text-slate-600">
                Connect crawl, Search Console, and mobile lab-performance evidence. Rank opportunities,
                review exact diffs, and prepare governed changes. External deployment stays blocked until each connector passes certification.
              </p>
              <div className="mt-8 flex gap-4">
                <Link
                  href="/pilot"
                  className="inline-flex rounded-lg bg-emerald-700 px-4 py-2.5 text-sm font-semibold text-white shadow-sm hover:bg-emerald-600 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-emerald-700"
                >
                  Connect your first site
                </Link>
              </div>
            </div>
            <aside className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
              <p className="text-sm font-medium text-slate-500">Operation Stages</p>
              <p className="mt-1 text-3xl font-semibold tabular-nums text-slate-950">2 / 6 Proven</p>
              <ol className="mt-6 space-y-3">
                {stages.map(([stage, stageStatus], index) => (
                  <li key={stage} className="flex items-center gap-3 text-sm text-slate-700">
                    <span className="flex size-7 items-center justify-center rounded-full border border-emerald-400 bg-emerald-50 font-medium tabular-nums text-emerald-800">
                      {index + 1}
                    </span>
                    <span className="flex-1">{stage}</span>
                    <span className="text-xs text-slate-500">{stageStatus}</span>
                  </li>
                ))}
              </ol>
            </aside>
          </section>

          {/* 7-Agent Architecture Section */}
          <section className="mt-16">
            <h2 className="text-balance text-2xl font-bold tracking-tight text-slate-950">
              Seven Specialist Workflows, One Governed Lifecycle
            </h2>
            <p className="mt-2 text-pretty text-sm text-slate-500">
              Six rule-backed evidence dimensions are active. The content-generation workflow is planned and remains human-reviewed.
            </p>
            <div className="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              {agents.map(([name, purpose, agentStatus]) => (
                <article key={name} className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
                  <h3 className="text-balance font-semibold text-slate-900">{name}</h3>
                  <p className="mt-2 text-pretty text-xs leading-5 text-slate-600">{purpose}</p>
                  <p className="mt-4 text-[10px] font-semibold text-emerald-700 uppercase tracking-wider">
                    {agentStatus}
                  </p>
                </article>
              ))}
            </div>
          </section>

          {/* FAQ Section with Rich Answerability */}
          <section className="mt-16 border-t border-slate-200 pt-12">
            <h2 className="text-2xl font-bold tracking-tight text-slate-950">
              Frequently Asked Questions
            </h2>
            <div className="mt-6 grid gap-6 sm:grid-cols-3">
              {faqs.map((faq) => (
                <div key={faq.q} className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
                  <h3 className="text-sm font-semibold text-slate-900">{faq.q}</h3>
                  <p className="mt-2 text-xs leading-relaxed text-slate-600">{faq.a}</p>
                </div>
              ))}
            </div>
          </section>
        </div>

        {/* Google will not publish an External OAuth app whose privacy policy
            and terms are not reachable, and it checks the links rather than
            taking the form's word for it. They belong in a footer regardless. */}
        <footer className="border-t border-slate-200 bg-white">
          <div className="mx-auto flex max-w-7xl flex-col gap-2 px-6 py-8 text-xs text-slate-500 sm:flex-row sm:items-center sm:justify-between">
            <p>SEO Autopilot &middot; Oryxen Labs</p>
            <nav className="flex gap-4">
              <Link href="/privacy" className="hover:text-slate-900">
                Privacy Policy
              </Link>
              <Link href="/terms" className="hover:text-slate-900">
                Terms of Service
              </Link>
            </nav>
          </div>
        </footer>
      </main>
    </>
  );
}
