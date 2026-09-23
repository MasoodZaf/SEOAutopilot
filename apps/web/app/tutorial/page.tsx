import type {Metadata} from "next";
import Link from "next/link";

import {Badge, button} from "@/app/components/ui";
import {SiteFooter, SiteHeader} from "@/app/components/site-header";

export const metadata: Metadata = {
  title: "Tutorial — from sign-in to a measured change",
  description:
    "A step-by-step guide to SEO Autopilot: create a workspace, verify a site, connect Google and GitHub, crawl, review ranked opportunities, approve a change and measure it.",
  alternates: {canonical: "/tutorial"},
};

/*
 * The tutorial.
 *
 * Public on purpose: the proxy guards /pilot, /settings and /onboarding only,
 * so someone deciding whether to sign up can read exactly what they will be
 * asked to do and what the product will never do on its own.
 *
 * Every step names the place in the app where it happens and the boundary it
 * does not cross. The boundaries are the point -- a guide that only says
 * "click Deploy" would describe a different, more dangerous product.
 */

type Step = {
  id: string;
  title: string;
  where: {label: string; href?: string};
  summary: string;
  actions: string[];
  never: string;
};

const steps: Step[] = [
  {
    id: "sign-in",
    title: "Sign in and name a workspace",
    where: {label: "Sign in", href: "/login"},
    summary:
      "Sign in with Google. On your first visit you name a workspace. It holds your sites, your keys and your data, and nobody else's.",
    actions: [
      "Choose Continue with Google. We ask only for your name and email address.",
      "Name the workspace. You become its owner and can rename it or invite people afterwards.",
    ],
    never: "Signing in connects nothing. Search Console, Analytics and GitHub are separate, later choices.",
  },
  {
    id: "add-site",
    title: "Add a site and prove you own it",
    where: {label: "Settings → Sites", href: "/settings/sites"},
    summary:
      "Enter a name and the site's address (scheme and host, no path). You get a one-time DNS TXT record to add at your DNS host.",
    actions: [
      "Add the TXT record exactly as shown. Each value has a copy box.",
      "Press Verify. DNS can take a few minutes to propagate, and retrying is safe.",
      "On Cloudflare, the DNS provider assistant can create the record for you with a scoped token.",
    ],
    never: "Nothing is crawled and no connector binds to a site until the DNS proof exists.",
  },
  {
    id: "connect",
    title: "Connect Google and your repository",
    where: {label: "Settings → Connectors", href: "/settings/connectors"},
    summary:
      "One Google sign-in connects Search Console and Analytics for every verified site whose property that account can see. The repository is where approved changes are written.",
    actions: [
      "Choose Connect Google. Each site is matched to the property that covers its address.",
      "For the repository, sign in with GitHub and pick the site's repository from a list. No token to copy.",
      "If a connection stops working, a banner says so on every page, with a Reconnect link.",
    ],
    never: "Connections are read-only for Google. GitHub access is used only to open pull requests.",
  },
  {
    id: "crawl",
    title: "Crawl the site",
    where: {label: "Dashboard → Evidence", href: "/pilot"},
    summary:
      "Trigger a bounded crawl. It reads the pages a visitor sees, renders JavaScript when a page needs it, and reports progress live.",
    actions: [
      "Press Trigger new crawl. Only one crawl runs per site at a time.",
      "Watch the progress bar. A finished crawl reports how many pages it read and how long it took.",
      "If most pages come back identical, such as a loading screen, the crawl is marked failed and no findings are drawn from it.",
    ],
    never: "A crawl is read-only. It respects robots rules and rate limits and changes nothing on the site.",
  },
  {
    id: "review",
    title: "Read the advisory queue",
    where: {label: "Dashboard → Advisory queue", href: "/pilot"},
    summary:
      "Findings become ranked opportunities: the top 20, each with a score, a confidence, a risk level and the page it concerns.",
    actions: [
      "Open Correction and validation to see what fixing it involves and how the fix will be checked.",
      "Freeze a 20-item human review set to measure how often the ranking is right before you trust it.",
    ],
    never: "The queue ranks candidates. It makes no automatic changes, and the count of those is always shown: zero.",
  },
  {
    id: "propose",
    title: "Draft a proposal",
    where: {label: "Dashboard → Proposals", href: "/pilot"},
    summary:
      "Draft proposal turns an opportunity into a reviewable change: an exact diff against the file, a preview of how it reads in search, and the policy result.",
    actions: [
      "Read the diff. It is the whole change; nothing else is written.",
      "Withdraw a draft you do not want. It stops counting toward approvals and can never deploy.",
    ],
    never: "Drafting is not approving. A proposal writes nothing to the site.",
  },
  {
    id: "approve",
    title: "Approve and deploy",
    where: {label: "Dashboard → Proposals", href: "/pilot"},
    summary:
      "Another person approves. Riskier changes, such as canonical, robots or redirect edits, need more approvers. Deploy then opens a pull request on your repository.",
    actions: [
      "An author can never approve their own change. Invite reviewers under Settings → Members.",
      "Deploy opens the pull request. The change reaches the live site only when a person merges it.",
      "Request revert opens a revert pull request if a deployed change needs undoing.",
    ],
    never: "Deployments stop at the daily change budget, during a freeze window, and site-wide under the emergency freeze.",
  },
  {
    id: "measure",
    title: "Measure the outcome",
    where: {label: "Dashboard → 28-day outcomes", href: "/pilot"},
    summary:
      "After a verified deployment, clicks and position are compared across a 28-day window before and after the change.",
    actions: [
      "Check evidence completeness. Sparse data is labelled as sparse, not rounded up.",
      "Read the annotations. They explain anything that weakens the comparison.",
    ],
    never: "A before-and-after comparison is an association. It does not prove the change caused the difference.",
  },
];

const modes = [
  {name: "Observe", body: "Measures only. The default. Every deployment is refused."},
  {name: "Recommend", body: "Drafts proposals and deploys each one only after approval."},
  {name: "Autopilot", body: "Deploys unattended within the budget and policy. Chosen deliberately, with a reason on record."},
];

export default function TutorialPage() {
  return (
    <div className="bg-paper text-ink">
      <SiteHeader />

      <main>
        <section className="dot-field border-b border-rule">
          <div className="mx-auto max-w-6xl px-6 pt-20 pb-16 lg:pt-24">
            <p className="eyebrow flex items-center gap-3">
              <span aria-hidden="true" className="size-1.5 rounded-full bg-signal" />
              Tutorial
            </p>
            <h1 className="mt-6 max-w-4xl font-display text-[42px] leading-[1.04] font-light tracking-[-0.035em] text-balance text-ink sm:text-[62px]">
              From sign&#8209;in to a <span className="text-accent">measured change</span>, in eight steps.
            </h1>
            <p className="mt-8 max-w-2xl text-[16px] leading-7 text-pretty text-ink-soft">
              Each step says where it happens in the app and the line it never crosses. Nothing reaches your
              site until a person approves it and a person merges it.
            </p>
            <ol className="mt-12 flex flex-wrap gap-2" aria-label="Steps">
              {steps.map((step, index) => (
                <li key={step.id}>
                  <a
                    href={`#${step.id}`}
                    className="inline-flex items-center gap-2.5 rounded-full border border-rule-strong bg-surface py-1.5 pr-4 pl-3 text-[13px] text-ink-soft transition-colors hover:border-ink-faint hover:text-ink"
                  >
                    <span className="font-mono text-[11px] text-accent">{String(index + 1).padStart(2, "0")}</span>
                    {step.title}
                  </a>
                </li>
              ))}
            </ol>
          </div>
        </section>

        <div className="mx-auto grid max-w-6xl gap-12 px-6 py-20 lg:grid-cols-[14rem_minmax(0,1fr)]">
          {/* A side rail of the same steps, so the reader always knows where
              they are in a long page. */}
          <nav aria-label="On this page" className="hidden lg:block">
            <ol className="sticky top-24 flex flex-col border-l border-rule">
              {steps.map((step, index) => (
                <li key={step.id}>
                  <a
                    href={`#${step.id}`}
                    className="-ml-px flex gap-3 border-l border-transparent py-1.5 pl-4 text-[13px] text-ink-faint transition-colors hover:border-accent hover:text-ink"
                  >
                    <span className="font-mono text-[11px] leading-5">{String(index + 1).padStart(2, "0")}</span>
                    <span className="leading-5">{step.title}</span>
                  </a>
                </li>
              ))}
            </ol>
          </nav>

          <div className="flex min-w-0 flex-col gap-6">
            {steps.map((step, index) => (
              <article
                key={step.id}
                id={step.id}
                aria-labelledby={`${step.id}-title`}
                className="scroll-mt-24 overflow-hidden rounded-2xl border border-rule bg-surface"
              >
                <div className="grid gap-6 p-6 sm:grid-cols-[4.5rem_minmax(0,1fr)] sm:p-8">
                  <span className="figure text-[44px] text-ink-faint">{String(index + 1).padStart(2, "0")}</span>
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      {step.where.href ? (
                        <Link
                          href={step.where.href}
                          className="inline-flex items-center rounded-full border border-accent-rule bg-accent-soft px-2.5 py-0.5 font-mono text-[11px] font-medium tracking-wider text-accent uppercase transition-colors hover:border-accent"
                        >
                          {step.where.label}
                        </Link>
                      ) : (
                        <Badge tone="accent">{step.where.label}</Badge>
                      )}
                    </div>
                    <h2
                      id={`${step.id}-title`}
                      className="mt-4 font-display text-[24px] leading-tight font-light tracking-tight text-balance text-ink"
                    >
                      {step.title}
                    </h2>
                    <p className="mt-3 max-w-2xl text-[14px] leading-6 text-pretty text-ink-soft">{step.summary}</p>
                    <ul className="mt-5 flex flex-col gap-2.5">
                      {step.actions.map((action) => (
                        <li key={action} className="flex gap-3 text-[14px] leading-6 text-pretty text-ink">
                          <span aria-hidden="true" className="mt-[9px] size-1.5 shrink-0 rounded-full bg-accent" />
                          {action}
                        </li>
                      ))}
                    </ul>
                  </div>
                </div>
                <div className="flex gap-3 border-t border-rule bg-sunk/60 px-6 py-4 sm:px-8">
                  <span className="eyebrow shrink-0 pt-0.5 text-good">Boundary</span>
                  <p className="text-[13px] leading-6 text-pretty text-ink-soft">{step.never}</p>
                </div>
              </article>
            ))}

            <section aria-labelledby="modes-heading" className="mt-10">
              <p className="eyebrow">Good to know</p>
              <h2 id="modes-heading" className="mt-4 font-display text-[30px] leading-tight font-light tracking-tight text-ink">
                How far a site may go on its own
              </h2>
              <p className="mt-3 max-w-2xl text-[14px] leading-6 text-pretty text-ink-soft">
                Each site has an operation mode, set under Adjust governance on the dashboard. Changing it needs a
                written reason, which is recorded against your name.
              </p>
              <dl className="mt-8 grid gap-px overflow-hidden rounded-2xl border border-rule bg-rule sm:grid-cols-3">
                {modes.map((mode) => (
                  <div key={mode.name} className="bg-surface p-6">
                    <dt className="font-display text-[17px] font-medium tracking-tight text-ink">{mode.name}</dt>
                    <dd className="mt-2 text-[13px] leading-6 text-pretty text-ink-soft">{mode.body}</dd>
                  </div>
                ))}
              </dl>
            </section>

            <section aria-labelledby="workspace-heading" className="mt-10 rounded-2xl border border-rule bg-surface p-6 sm:p-8">
              <h2 id="workspace-heading" className="font-display text-[20px] font-medium tracking-tight text-ink">
                The agent workspace
              </h2>
              <p className="mt-3 max-w-2xl text-[14px] leading-6 text-pretty text-ink-soft">
                From the dashboard, Agent workspace opens a chat about the selected site. The agent answers from stored
                evidence and can queue analysis, keyword clusters, content briefs and reports. It cannot publish a
                change: everything it suggests still goes through a reviewed proposal.
              </p>
            </section>

            <div className="mt-10 flex flex-col items-start gap-6 rounded-2xl border border-rule bg-surface p-8 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <p className="font-display text-[22px] font-light tracking-tight text-ink">Ready to start?</p>
                <p className="mt-1 text-[13px] text-ink-soft">Step one takes about a minute.</p>
              </div>
              <Link href="/login" className={`${button.primary} px-6 py-2.5 text-[14px]`}>
                Sign in with Google
              </Link>
            </div>
          </div>
        </div>
      </main>

      <SiteFooter />
    </div>
  );
}
