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
      <main className="min-h-dvh bg-sunk text-ink">
        <header className="border-b border-rule bg-surface">
          <div className="mx-auto flex max-w-7xl items-center justify-between px-6 py-4">
            <div>
              <p className="font-display text-[15px] font-semibold tracking-tight text-ink">SEO Autopilot</p>
              <p className="text-xs text-ink-faint">Evidence to measured change</p>
            </div>
            <div className="flex items-center gap-3">
              <Link
                href="/pilot"
                className="rounded-[4px] bg-accent px-3.5 py-1.5 text-xs font-semibold text-accent-ink shadow-sm hover:bg-accent"
              >
                Control Plane
              </Link>
            </div>
          </div>
        </header>

        <div className="mx-auto max-w-7xl px-6 py-12">
          <section className="grid gap-8 lg:grid-cols-[1.4fr_1fr]">
            <div>
              <p className="eyebrow mb-3">Governed SEO Operations</p>
              <h1 className="max-w-3xl font-display text-balance text-[40px] leading-[1.12] font-semibold tracking-tight text-ink sm:text-[52px]">
                Your highest-value SEO work, explained, verified, and controlled.
              </h1>
              <p className="mt-5 max-w-2xl text-pretty text-lg leading-8 text-ink-soft">
                Connect crawl, Search Console, and mobile lab-performance evidence. Rank opportunities,
                review exact diffs, and prepare governed changes. External deployment stays blocked until each connector passes certification.
              </p>
              <div className="mt-8 flex gap-4">
                <Link
                  href="/pilot"
                  className="inline-flex rounded-[4px] bg-accent px-4 py-2.5 text-sm font-semibold text-accent-ink shadow-sm hover:bg-accent"
                >
                  Connect your first site
                </Link>
              </div>
            </div>
            <aside className="rounded-[4px] border border-rule bg-surface p-6 shadow-sm">
              <p className="text-sm font-medium text-ink-faint">Operation Stages</p>
              <p className="mt-1 font-display text-[28px] font-semibold tabular-nums text-ink">2 / 6 Proven</p>
              <ol className="mt-6 space-y-3">
                {stages.map(([stage, stageStatus], index) => (
                  <li key={stage} className="flex items-center gap-3 text-sm text-ink-soft">
                    <span className="flex size-7 items-center justify-center rounded-full border border-accent-rule bg-accent-soft font-medium tabular-nums text-accent">
                      {index + 1}
                    </span>
                    <span className="flex-1">{stage}</span>
                    <span className="text-xs text-ink-faint">{stageStatus}</span>
                  </li>
                ))}
              </ol>
            </aside>
          </section>

          {/* 7-Agent Architecture Section */}
          <section className="mt-16">
            <h2 className="font-display text-balance text-[26px] font-semibold tracking-tight text-ink">
              Seven Specialist Workflows, One Governed Lifecycle
            </h2>
            <p className="mt-2 text-pretty text-sm text-ink-faint">
              Six rule-backed evidence dimensions are active. The content-generation workflow is planned and remains human-reviewed.
            </p>
            <div className="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              {agents.map(([name, purpose, agentStatus]) => (
                /* h-full + mt-auto so the status label sits on one baseline
                   across the row. Without it each card is only as tall as its
                   own copy, and a row of cards whose footers wander is the
                   thing that reads as unfinished before anyone can say why. */
                <article
                  key={name}
                  className="flex h-full flex-col rounded-[4px] border border-rule bg-surface p-5"
                >
                  <h3 className="text-balance font-semibold text-ink">{name}</h3>
                  <p className="mt-2 text-pretty text-xs leading-5 text-ink-soft">{purpose}</p>
                  <p className="eyebrow mt-auto pt-4">{agentStatus}</p>
                </article>
              ))}
            </div>
          </section>

          {/* FAQ Section with Rich Answerability */}
          <section className="mt-16 border-t border-rule pt-12">
            <h2 className="font-display text-[26px] font-semibold tracking-tight text-ink">
              Frequently Asked Questions
            </h2>
            <div className="mt-6 grid gap-6 sm:grid-cols-3">
              {faqs.map((faq) => (
                <div key={faq.q} className="rounded-[4px] border border-rule bg-surface p-5 shadow-sm">
                  <h3 className="text-sm font-semibold text-ink">{faq.q}</h3>
                  <p className="mt-2 text-xs leading-relaxed text-ink-soft">{faq.a}</p>
                </div>
              ))}
            </div>
          </section>
        </div>

        {/* Google will not publish an External OAuth app whose privacy policy
            and terms are not reachable, and it checks the links rather than
            taking the form's word for it. They belong in a footer regardless. */}
        <footer className="border-t border-rule bg-surface">
          <div className="mx-auto flex max-w-7xl flex-col gap-2 px-6 py-8 text-xs text-ink-faint sm:flex-row sm:items-center sm:justify-between">
            <p>SEO Autopilot &middot; Oryxen Labs</p>
            <nav className="flex gap-4">
              <Link href="/privacy" className="hover:text-ink">
                Privacy Policy
              </Link>
              <Link href="/terms" className="hover:text-ink">
                Terms of Service
              </Link>
            </nav>
          </div>
        </footer>
      </main>
    </>
  );
}
