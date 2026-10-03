import Link from "next/link";

import {Badge, button} from "@/app/components/ui";
import {SiteFooter, SiteHeader} from "@/app/components/site-header";

/* What the product does today. Every line here is live on production; nothing
   planned is listed, and nothing promises a ranking or an AI citation. */
const capabilities = [
  [
    "Technical and content audit",
    "A rendered crawl checks indexability, canonicals, titles, headings and thin content, then ranks the top 20 issues by evidence.",
  ],
  [
    "Fixes as pull requests",
    "Title, heading, FAQ-markup and llms.txt fixes arrive as exact diffs on your repository, checked against the live file before they are opened.",
  ],
  [
    "Search Console and Analytics",
    "One Google consent connects both. Queries are grouped into topics, and landing pages show what visitors did once they arrived.",
  ],
  [
    "AI answer readiness",
    "Flags questions on a page that have no quotable answer, answers that lack FAQ markup, and AI crawlers your robots.txt turns away.",
  ],
  [
    "AI citation tracking",
    "Asks Claude, ChatGPT and Perplexity the questions you track, every week, and shows who they cite instead of you, and what kind of source it is.",
  ],
  [
    "Blog and answer drafting",
    "Drafts from your own Search Console demand, written with your own Claude or OpenAI key. Every claim and figure is flagged for a person to check.",
  ],
  [
    "Zero-click scorecard",
    "Most searches end without a click. Track branded searches, queries answered on the results page, direct visits and AI mentions, week by week.",
  ],
  [
    "Routines that run themselves",
    "Weekly audits, data syncs, citation checks and reports on a schedule, each with a spend cap and a record of what it did.",
  ],
] as const;

/* How a site gets connected. Each step is one action. */
const connect = [
  ["Sign in with Google", "Your workspace, its sites and its keys belong to you alone."],
  ["Verify your domain", "One DNS record proves the site is yours."],
  ["Connect Google once", "Search Console and Analytics in a single consent, read-only."],
  ["Pick a repository", "Sign in to GitHub and choose the repo the site is built from."],
] as const;

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

const controls = [
  ["Nothing changes unreviewed", "Every fix is a proposal with an exact diff. Authors cannot approve their own work, and new pages and AI-written text need two approvers."],
  ["Publish in one click, if you allow it", "An owner can let approved changes go live from the dashboard. Only the exact reviewed content is merged, and GitHub's branch protection always applies."],
  ["Brakes you set", "Observe, recommend or autopilot per site, a daily change budget, freeze windows, and an emergency freeze that stops everything."],
  ["Undo and audit", "Any change can be reverted with a pull request, live pages are checked after each change, and every step is logged."],
] as const;

const faqs = [
  {
    q: "Will SEO Autopilot change my site on its own?",
    a: "No. Every change is a proposal you review as an exact diff. After approval it becomes a pull request on your repository, merged by a person on GitHub, or with one click from the dashboard if an owner has turned that on. Robots, redirect, configuration and site-wide template changes are always merged on GitHub.",
  },
  {
    q: "How is AI-written content kept honest?",
    a: "Drafts use your own Claude or OpenAI key and never publish themselves. Every claim and figure is flagged for a person to verify, the post or answer becomes a high-risk proposal that needs two approvers, and only then can it reach the site.",
  },
  {
    q: "Can it get my site cited by ChatGPT or Perplexity?",
    a: "Nobody can promise that. It checks whether your pages answer their questions in a way an engine can quote, whether AI crawlers can reach them, and which sources the engines cite instead of you, then proposes the fixes that are yours to make.",
  },
  {
    q: "What does it connect to, and what can it touch?",
    a: "Google Search Console and Analytics through one read-only consent, and the GitHub repositories you pick. It writes only branches and pull requests, and keeps each workspace's data and keys separate from every other.",
  },
  {
    q: "Does it prove a change improved my rankings?",
    a: "It measures the 28 days before and after a verified change and reports the association. It does not claim the change caused it: search moves for many reasons, and saying otherwise would need a controlled experiment.",
  },
];

export default function Home() {
  const siteUrl = process.env.NEXT_PUBLIC_APP_URL || "https://seo.oryxenlabs.com";
  const jsonLd = {
    "@context": "https://schema.org",
    "@graph": [
      {
        "@type": "SoftwareApplication",
        "name": "SEO Autopilot",
        "url": siteUrl,
        "applicationCategory": "BusinessApplication",
        "operatingSystem": "Web",
        "description":
          "SEO and AI-search operations: audits, fixes as reviewed pull requests, Search Console and Analytics insight, AI citation tracking and drafting with your own AI key.",
        "publisher": {"@type": "Organization", "name": "Oryxen Labs", "url": "https://oryxenlabs.com"},
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
      <div className="bg-paper text-ink">
        <SiteHeader />

        <main>
          <section className="dot-field border-b border-rule">
            <div className="mx-auto grid max-w-6xl gap-14 px-6 pt-20 pb-20 lg:grid-cols-[1.35fr_1fr] lg:items-end lg:pt-28">
              <div>
                <p className="eyebrow flex items-center gap-3">
                  <span aria-hidden="true" className="size-1.5 rounded-full bg-signal" />
                  SEO and AI-search operations
                </p>
                <h1 className="mt-6 font-display text-[44px] leading-[1.02] font-light tracking-[-0.035em] text-balance text-ink sm:text-[64px]">
                  Find what holds your site back. <span className="text-accent">Fix it in a reviewed pull request.</span>
                </h1>
                <p className="mt-8 max-w-xl text-[16px] leading-7 text-pretty text-ink-soft">
                  SEO Autopilot audits your site, reads your Search Console and Analytics, tracks how Claude,
                  ChatGPT and Perplexity answer your customers&rsquo; questions, and turns what it finds into
                  exact changes you approve. Nothing reaches your site without a person saying yes.
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

              <aside aria-labelledby="connect-heading" className="rounded-2xl border border-rule bg-surface p-7">
                <div className="flex items-start justify-between gap-4">
                  <p id="connect-heading" className="eyebrow">Connected in four steps</p>
                  <Badge tone="good">Live</Badge>
                </div>
                <ol className="relative mt-7">
                  <span aria-hidden="true" className="absolute top-3 bottom-3 left-[5px] w-px bg-rule-strong" />
                  {connect.map(([step, detail]) => (
                    <li key={step} className="relative flex gap-4 py-2.5">
                      <span aria-hidden="true" className="relative mt-1.5 size-[11px] shrink-0 rounded-full border-2 border-signal bg-signal" />
                      <span className="flex flex-col gap-0.5">
                        <span className="text-[14px] font-medium text-ink">{step}</span>
                        <span className="text-[12px] leading-5 text-pretty text-ink-faint">{detail}</span>
                      </span>
                    </li>
                  ))}
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

          <section aria-labelledby="capabilities-heading" className="mx-auto max-w-6xl px-6 py-24">
            <div className="grid gap-6 lg:grid-cols-[1fr_2fr]">
              <div>
                <p className="eyebrow">What it does</p>
                <h2 id="capabilities-heading" className="mt-4 font-display text-[34px] leading-[1.1] font-light tracking-tight text-balance text-ink">
                  Search and AI answers, in one place
                </h2>
                <p className="mt-4 max-w-sm text-[14px] leading-6 text-pretty text-ink-soft">
                  Classic SEO still decides whether you are found. AI answers increasingly decide whether you are
                  chosen. Both are measured here, and both end in changes you can review.
                </p>
              </div>
              <div className="grid gap-px overflow-hidden rounded-2xl border border-rule bg-rule sm:grid-cols-2">
                {capabilities.map(([name, purpose], index) => (
                  <article key={name} className="flex flex-col bg-surface p-6 transition-colors hover:bg-sunk">
                    <span className="font-mono text-[11px] text-ink-faint">{String(index + 1).padStart(2, "0")}</span>
                    <h3 className="mt-6 font-display text-[17px] font-medium tracking-tight text-balance text-ink">{name}</h3>
                    <p className="mt-2 text-[13px] leading-6 text-pretty text-ink-soft">{purpose}</p>
                  </article>
                ))}
              </div>
            </div>
          </section>

          <section aria-labelledby="controls-heading" className="border-t border-rule bg-surface">
            <div className="mx-auto grid max-w-6xl gap-10 px-6 py-24 lg:grid-cols-[1fr_2fr]">
              <div>
                <p className="eyebrow">You stay in control</p>
                <h2 id="controls-heading" className="mt-4 font-display text-[34px] leading-[1.1] font-light tracking-tight text-balance text-ink">
                  Fast when you want it, never unattended
                </h2>
                <Link href="/pilot" className="mt-6 inline-flex text-[14px] font-medium text-accent underline-offset-4 hover:underline">
                  Open the control plane <span aria-hidden="true">&nbsp;&rarr;</span>
                </Link>
              </div>
              <dl className="grid gap-8 sm:grid-cols-2">
                {controls.map(([title, body]) => (
                  <div key={title} className="flex flex-col gap-2">
                    <dt className="font-display text-[16px] font-medium tracking-tight text-balance text-ink">{title}</dt>
                    <dd className="text-[13px] leading-6 text-pretty text-ink-soft">{body}</dd>
                  </div>
                ))}
              </dl>
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
