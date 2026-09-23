import Link from "next/link";

import {Badge, button} from "@/app/components/ui";
import {SiteFooter, SiteHeader} from "@/app/components/site-header";

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

/* The lifecycle every change travels. No stage may be skipped, and that
   sentence is the product -- so it is drawn, not buried in a paragraph. */
const lifecycle = [
  "Finding",
  "Opportunity",
  "Proposal",
  "Validation",
  "Approval",
  "Deployment",
  "Verification",
  "Measurement",
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

  const proven = stages.filter(([, status]) => status === "Proven").length;

  return (
    <>
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{__html: JSON.stringify(jsonLd)}}
      />
      <div className="bg-paper text-ink">
        <SiteHeader />

        <main>
          <section className="dot-field border-b border-rule">
            <div className="mx-auto grid max-w-6xl gap-14 px-6 pt-20 pb-20 lg:grid-cols-[1.35fr_1fr] lg:items-end lg:pt-28">
              <div>
                <p className="eyebrow flex items-center gap-3">
                  <span aria-hidden="true" className="size-1.5 rounded-full bg-signal" />
                  Governed SEO operations
                </p>
                <h1 className="mt-6 font-display text-[44px] leading-[1.02] font-light tracking-[-0.035em] text-balance text-ink sm:text-[68px]">
                  Your highest&#8209;value SEO work, <span className="text-accent">explained, verified,</span> and controlled.
                </h1>
                <p className="mt-8 max-w-xl text-[16px] leading-7 text-pretty text-ink-soft">
                  Connect crawl, Search Console, and mobile lab-performance evidence. Rank opportunities,
                  review exact diffs, and prepare governed changes. External deployment stays blocked until each connector passes certification.
                </p>
                <div className="mt-10 flex flex-wrap items-center gap-3">
                  <Link href="/pilot" className={`${button.primary} px-6 py-2.5 text-[14px]`}>
                    Connect your first site
                  </Link>
                  <Link href="/tutorial" className={`${button.secondary} px-6 py-2.5 text-[14px]`}>
                    Read the tutorial
                  </Link>
                </div>
              </div>

              <aside aria-labelledby="stages-heading" className="rounded-2xl border border-rule bg-surface p-7">
                <div className="flex items-start justify-between gap-4">
                  <p id="stages-heading" className="eyebrow">Operation stages</p>
                  <Badge tone="accent">Live</Badge>
                </div>
                <p className="mt-5 flex items-baseline gap-2">
                  <span className="figure text-[64px] text-ink">{proven}</span>
                  <span className="figure text-[28px] text-ink-faint">/ {stages.length}</span>
                  <span className="ml-2 font-mono text-[12px] tracking-wider text-ink-faint uppercase">proven</span>
                </p>
                <ol className="relative mt-7">
                  <span aria-hidden="true" className="absolute top-2 bottom-2 left-[5px] w-px bg-rule-strong" />
                  {stages.map(([stage, stageStatus]) => {
                    const done = stageStatus === "Proven";
                    return (
                      <li key={stage} className="relative flex items-center gap-4 py-2">
                        <span
                          aria-hidden="true"
                          className={`relative size-[11px] shrink-0 rounded-full border-2 ${done ? "border-signal bg-signal" : "border-rule-strong bg-surface"}`}
                        />
                        <span className={`flex-1 text-[13px] ${done ? "text-ink" : "text-ink-soft"}`}>{stage}</span>
                        <span className="font-mono text-[11px] text-ink-faint">{stageStatus}</span>
                      </li>
                    );
                  })}
                </ol>
              </aside>
            </div>
          </section>

          {/* The lifecycle band. */}
          <section id="lifecycle" aria-labelledby="lifecycle-heading" className="border-b border-rule bg-surface">
            <div className="mx-auto max-w-6xl px-6 py-14">
              <div className="flex flex-col gap-2 sm:flex-row sm:items-baseline sm:justify-between">
                <h2 id="lifecycle-heading" className="font-display text-[22px] font-light tracking-tight text-ink">
                  One path for every change. No stage may be skipped.
                </h2>
                <p className="eyebrow">8 stages · each one audited</p>
              </div>
              <ol className="mt-10 grid grid-cols-2 gap-px overflow-hidden rounded-2xl border border-rule bg-rule sm:grid-cols-4 lg:grid-cols-8">
                {lifecycle.map((stage, index) => (
                  <li key={stage} className="flex flex-col gap-6 bg-surface p-5">
                    <span className="font-mono text-[11px] text-accent">{String(index + 1).padStart(2, "0")}</span>
                    <span className="font-display text-[15px] font-medium tracking-tight text-ink">{stage}</span>
                  </li>
                ))}
              </ol>
            </div>
          </section>

          <section aria-labelledby="workflows-heading" className="mx-auto max-w-6xl px-6 py-24">
            <div className="grid gap-6 lg:grid-cols-[1fr_2fr]">
              <div>
                <p className="eyebrow">Workflows</p>
                <h2 id="workflows-heading" className="mt-4 font-display text-[34px] leading-[1.1] font-light tracking-tight text-balance text-ink">
                  Seven specialist workflows, one governed lifecycle
                </h2>
                <p className="mt-4 max-w-sm text-[14px] leading-6 text-pretty text-ink-soft">
                  Six rule-backed evidence dimensions are active. The content-generation workflow is planned and remains human-reviewed.
                </p>
              </div>
              <div className="grid gap-px overflow-hidden rounded-2xl border border-rule bg-rule sm:grid-cols-2">
                {agents.map(([name, purpose, agentStatus], index) => (
                  <article key={name} className="flex flex-col bg-surface p-6 transition-colors hover:bg-sunk">
                    <div className="flex items-center justify-between gap-3">
                      <span className="font-mono text-[11px] text-ink-faint">{String(index + 1).padStart(2, "0")}</span>
                      <Badge tone={agentStatus === "Planned" ? "neutral" : "good"}>{agentStatus}</Badge>
                    </div>
                    <h3 className="mt-6 font-display text-[17px] font-medium tracking-tight text-balance text-ink">{name}</h3>
                    <p className="mt-2 text-[13px] leading-6 text-pretty text-ink-soft">{purpose}</p>
                  </article>
                ))}
                {/* Seven items in a two-column grid leaves one cell open; it
                    carries the call to action rather than sitting empty. */}
                <Link href="/pilot" className="group flex flex-col justify-between bg-surface p-6 transition-colors hover:bg-sunk">
                  <span className="font-mono text-[11px] text-ink-faint">Start</span>
                  <span className="mt-6 font-display text-[17px] font-medium tracking-tight text-accent">
                    Open the control plane <span aria-hidden="true">&rarr;</span>
                  </span>
                </Link>
              </div>
            </div>
          </section>

          <section aria-labelledby="faq-heading" className="border-t border-rule">
            <div className="mx-auto grid max-w-6xl gap-10 px-6 py-24 lg:grid-cols-[1fr_2fr]">
              <div>
                <p className="eyebrow">Questions</p>
                <h2 id="faq-heading" className="mt-4 font-display text-[34px] leading-[1.1] font-light tracking-tight text-ink">
                  Frequently asked questions
                </h2>
              </div>
              <dl className="divide-y divide-rule border-y border-rule">
                {faqs.map((faq) => (
                  <div key={faq.q} className="grid gap-3 py-7 sm:grid-cols-[1fr_1.3fr] sm:gap-8">
                    <dt className="font-display text-[16px] leading-6 font-medium tracking-tight text-balance text-ink">{faq.q}</dt>
                    <dd className="text-[14px] leading-6 text-pretty text-ink-soft">{faq.a}</dd>
                  </div>
                ))}
              </dl>
            </div>
          </section>
        </main>

        {/* Google will not publish an External OAuth app whose privacy policy
            and terms are not reachable, and it checks the links rather than
            taking the form's word for it. The footer carries both. */}
        <SiteFooter />
      </div>
    </>
  );
}
