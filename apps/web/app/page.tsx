import Link from "next/link";

const agents = [
  ["Technical SEO", "Crawl, indexability, canonical tags, and status code verification."],
  ["Content SEO", "Search intent alignment, semantic density, and structured meta content."],
  ["Keyword Opportunity", "GSC demand signals, high-impression position 4-20 page discovery."],
  ["Internal Linking", "Authority distribution, anchor relevance, and orphan page resolution."],
  ["SEO Content", "Evidence-bound structured proposals with unified diff previews."],
  ["Performance", "Mobile Core Web Vitals (LCP, INP, CLS) lab runs and optimization."],
  ["GEO / AI Visibility", "Entity clarity, JSON-LD knowledge graph schema, and LLM answer grounding."],
] as const;

const stages = [
  "Connect site & DNS",
  "Bounded crawl evidence",
  "Multi-agent Top 20 ranking",
  "Two-person approval",
  "Safe Git / CMS deployment",
  "28-day causal measurement",
];

const faqs = [
  {
    q: "How does SEO Autopilot prevent harmful or hallucinated changes?",
    a: "SEO Autopilot enforces hard evidence grounding: proposals cannot cite findings outside audited crawl observations. Furthermore, separation of duties prevents authors from self-approving changes, and drift detection blocks deployments if base content changed.",
  },
  {
    q: "What makes the 28-day measurement engine different from rank trackers?",
    a: "Instead of simple before-and-after correlation, SEO Autopilot uses Synthetic Control Groups (Difference-in-Differences causal inference) on unchanged pages within the same domain to isolate true SEO impact from Google core updates or seasonality.",
  },
  {
    q: "What safety kill switches exist in Autopilot mode?",
    a: "Sites feature one-click Emergency Freeze controls, daily change budgets (default 5/day), scheduled freeze windows during peak traffic, and one-action cryptographic rollbacks.",
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
        "offers": {
          "@type": "Offer",
          "price": "0",
          "priceCurrency": "USD",
        },
        "description":
          "Auditable multi-tenant autonomous SEO operations system connecting crawl evidence, GSC search metrics, and Core Web Vitals to deploy and measure ranking improvements.",
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
                10/10 Enterprise
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
              <p className="mb-3 text-sm font-semibold text-emerald-700">Production SEO Operations</p>
              <h1 className="max-w-3xl text-balance text-4xl font-bold tracking-tight text-slate-950 sm:text-5xl">
                Your highest-value SEO work, explained, verified, and controlled.
              </h1>
              <p className="mt-5 max-w-2xl text-pretty text-lg leading-8 text-slate-600">
                Connect crawl, Search Console, analytics, and delivery data. Rank opportunities,
                review exact diffs, then deploy through policy with complete cryptographic audit receipts.
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
              <p className="mt-1 text-3xl font-semibold tabular-nums text-slate-950">6 / 6 Complete</p>
              <ol className="mt-6 space-y-3">
                {stages.map((stage, index) => (
                  <li key={stage} className="flex items-center gap-3 text-sm text-slate-700">
                    <span className="flex size-7 items-center justify-center rounded-full border border-emerald-400 bg-emerald-50 font-medium tabular-nums text-emerald-800">
                      {index + 1}
                    </span>
                    {stage}
                  </li>
                ))}
              </ol>
            </aside>
          </section>

          {/* 7-Agent Architecture Section */}
          <section className="mt-16">
            <h2 className="text-balance text-2xl font-bold tracking-tight text-slate-950">
              Seven Specialized Agents, One Governed Workflow
            </h2>
            <p className="mt-2 text-pretty text-sm text-slate-500">
              Agents synthesize evidence-backed findings. People and policy control production deployments.
            </p>
            <div className="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              {agents.map(([name, purpose]) => (
                <article key={name} className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
                  <h3 className="text-balance font-semibold text-slate-900">{name}</h3>
                  <p className="mt-2 text-pretty text-xs leading-5 text-slate-600">{purpose}</p>
                  <p className="mt-4 text-[10px] font-semibold text-emerald-700 uppercase tracking-wider">
                    Evidence Grounded
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
      </main>
    </>
  );
}
