import type {Metadata} from "next";
import {randomUUID} from "node:crypto";
import Link from "next/link";
import {notFound, redirect} from "next/navigation";

import {Badge, button, Field, input, Note, Panel, type Tone} from "@/app/components/ui";
import {ApiError, apiJson, isMissingTenant} from "@/lib/server-api";

import {requestDraft, updateBriefStatus} from "../../actions";
import type {BriefSection, ContentBriefDetail, ContentDraftSummary, KeywordMember} from "../../model";
import {workspacePath} from "../../paths";

export const metadata: Metadata = {
  title: "Content brief",
  robots: {index: false, follow: false},
};

/*
 * One content brief, readable and actionable.
 *
 * Until this page existed a brief was a row in a read-only list: its plan
 * could not be opened, and nobody could accept or dismiss it from the app.
 * The brief itself is built by fixed rules from Search Console evidence; this
 * page adds nothing to it except the searches the topic is made of, and an
 * outline assembled only from those searches -- no heading here is invented.
 *
 * Moving a brief through its queue records a decision. It never writes to the
 * site; turning a brief into a change is still a reviewed proposal.
 */

const STATUS_LABEL: Record<string, string> = {
  queued: "Suggested",
  in_progress: "In progress",
  done: "Done",
  dismissed: "Dismissed",
};

const STATUS_TONE: Record<string, Tone> = {
  queued: "accent",
  in_progress: "warn",
  done: "good",
  dismissed: "neutral",
};

const ERRORS: Record<string, string> = {
  dismiss_reason_required: "Say why you are dismissing this topic. The reason is kept with the decision.",
  content_brief_transition_not_allowed: "That change is not allowed from the brief's current state. Reload and try again.",
  insufficient_permissions_for_brief: "Your role cannot change briefs. An owner, admin, SEO manager or editor can.",
  content_brief_not_found: "This brief no longer exists. The briefs may have been regenerated.",
  anthropic_key_not_configured: "This workspace has no Anthropic key. Add one under Settings → Keys.",
  openai_key_not_configured: "This workspace has no OpenAI key. Add one under Settings → Keys.",
  content_draft_budget_exhausted: "This month's drafting budget is spent. It resets on the 1st.",
  content_draft_already_live: "A draft of this brief is already being written or reviewed.",
  content_brief_dismissed: "Restore the brief before drafting it.",
  insufficient_permissions_for_content_draft: "Your role cannot request drafts. An owner, admin, SEO manager or editor can.",
};

const AI_PROVIDERS = [
  {id: "anthropic", credential: "anthropic_api_key", label: "Claude (Anthropic)"},
  {id: "openai", credential: "openai_api_key", label: "OpenAI"},
] as const;

/** The moves a person can make from each state, in the order they are offered. */
const MOVES: Record<string, {status: string; label: string; primary?: boolean}[]> = {
  queued: [
    {status: "in_progress", label: "Start this", primary: true},
    {status: "done", label: "Mark done"},
  ],
  in_progress: [
    {status: "done", label: "Mark done", primary: true},
    {status: "queued", label: "Back to suggested"},
  ],
  done: [{status: "in_progress", label: "Reopen"}],
  dismissed: [{status: "queued", label: "Restore"}],
};

type PageProps = {
  params: Promise<{briefId: string}>;
  searchParams: Promise<{error?: string; updated?: string}>;
};

async function load(briefId: string) {
  if (!/^[0-9a-f-]{36}$/i.test(briefId)) notFound();
  try {
    const brief = (await apiJson<{data: ContentBriefDetail}>(`/v1/content-briefs/${briefId}`)).data;
    const sites = (await apiJson<{data: {id: string; normalized_host: string}[]}>("/v1/sites")).data;
    const host = sites.find((site) => site.id === brief.site_id)?.normalized_host ?? "";
    let members: KeywordMember[] = [];
    try {
      members = (
        await apiJson<{data: KeywordMember[]}>(`/v1/keyword-clusters/${brief.keyword_cluster_id}/members?limit=50`)
      ).data;
    } catch {
      // The searches are supporting detail. A brief whose cluster was
      // rebuilt since still has a readable plan.
      members = [];
    }
    // Drafting is bring-your-own-key: offer only the providers this
    // workspace has stored a key for.
    let aiProviders: string[] = [];
    let drafts: ContentDraftSummary[] = [];
    if (brief.kind === "new_page") {
      try {
        const credentials = (
          await apiJson<{data: {provider: string; source: string}[]}>("/v1/tenant/credentials")
        ).data;
        aiProviders = AI_PROVIDERS.filter((item) =>
          credentials.some((row) => row.provider === item.credential && row.source === "tenant"),
        ).map((item) => item.id);
      } catch {
        aiProviders = [];
      }
      try {
        drafts = (
          await apiJson<{data: ContentDraftSummary[]}>(`/v1/sites/${brief.site_id}/content-drafts?limit=50`)
        ).data.filter((draft) => draft.content_brief_id === brief.id);
      } catch {
        drafts = [];
      }
    }
    return {brief, host, members, aiProviders, drafts};
  } catch (error) {
    if (isMissingTenant(error)) redirect("/onboarding");
    if (error instanceof ApiError && error.status === 404) notFound();
    throw error;
  }
}

function sentenceCase(text: string): string {
  const trimmed = text.trim();
  return trimmed ? trimmed[0].toUpperCase() + trimmed.slice(1) : trimmed;
}

function evidenceValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "number") return Number.isInteger(value) ? String(value) : value.toFixed(2);
  if (Array.isArray(value)) return value.length ? value.join(", ") : "none";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function Section({section}: {section: BriefSection}) {
  const evidence = Object.entries(section.evidence ?? {});
  return (
    <li className="grid gap-3 px-6 py-5">
      <h3 className="font-display text-[16px] font-medium tracking-tight text-ink">{section.title}</h3>
      <p className="text-[14px] leading-6 text-pretty text-ink-soft">{section.finding}</p>
      <p className="flex gap-3 text-[14px] leading-6 text-pretty text-ink">
        <span aria-hidden="true" className="mt-[9px] size-1.5 shrink-0 rounded-full bg-accent" />
        {section.recommendation}
      </p>
      {evidence.length ? (
        <dl className="mt-1 grid gap-x-6 gap-y-1 rounded-xl bg-sunk/70 px-4 py-3 font-mono text-[12px] sm:grid-cols-2">
          {evidence.map(([key, value]) => (
            <div key={key} className="flex min-w-0 justify-between gap-3">
              <dt className="text-ink-faint">{key.replaceAll("_", " ")}</dt>
              <dd className="truncate text-right text-ink tabular">{evidenceValue(value)}</dd>
            </div>
          ))}
        </dl>
      ) : null}
    </li>
  );
}

export default async function BriefPage({params, searchParams}: PageProps) {
  const {briefId} = await params;
  const {error, updated} = await searchParams;
  const {brief, host, members, aiProviders, drafts} = await load(briefId);
  const liveDraft = drafts.find((draft) => ["queued", "running", "ready"].includes(draft.status));

  const isNewPost = brief.kind === "new_page";
  const questions = members.filter((member) => member.is_question);
  const others = members.filter((member) => !member.is_question);
  const exploratory = brief.evidence_json?.selection_basis === "exploratory";
  const moves = MOVES[brief.status] ?? [];

  return (
    <main className="bg-paper text-ink">
      <header className="dot-field border-b border-rule">
        <div className="mx-auto max-w-6xl px-6 pt-10 pb-8">
          <Link
            href={workspacePath(host, {tab: "reports"})}
            className="inline-flex items-center gap-2 text-[13px] font-medium text-ink-soft transition-colors hover:text-ink"
          >
            <span aria-hidden="true">&larr;</span> Content briefs
          </Link>
          <p className="eyebrow mt-8">{isNewPost ? "New post brief" : "Page refresh brief"}{host ? ` · ${host}` : ""}</p>
          <h1 className="mt-3 font-display text-[34px] leading-tight font-light tracking-tight text-balance text-ink sm:text-[46px]">
            {sentenceCase(brief.cluster_label)}
          </h1>
          <div className="mt-5 flex flex-wrap items-center gap-2">
            <Badge tone={STATUS_TONE[brief.status] ?? "neutral"}>{STATUS_LABEL[brief.status] ?? brief.status}</Badge>
            <Badge tone="neutral">{brief.intent} intent</Badge>
            {brief.answer_engine_candidate ? <Badge tone="accent">Question-led</Badge> : null}
            <span className="font-mono text-[12px] text-ink-faint tabular">priority {brief.priority_score.toFixed(0)}</span>
          </div>
        </div>
      </header>

      <div className="mx-auto grid max-w-6xl gap-8 px-6 py-10 lg:grid-cols-[minmax(0,1fr)_20rem]">
        <div className="flex min-w-0 flex-col gap-8">
          {error ? (
            <Note tone="stop" role="alert">{ERRORS[error] ?? "That did not work. Try again."}</Note>
          ) : null}
          {updated ? (
            <Note tone="good" role="status">Marked as {(STATUS_LABEL[updated] ?? updated).toLowerCase()}.</Note>
          ) : null}
          {exploratory ? (
            <Note tone="warn" label="Low demand">
              No topic on this site has enough search demand yet to clear the usual bar, so this one was
              suggested as the strongest available. Treat it as a starting point, not a proven opportunity.
            </Note>
          ) : null}

          <section aria-labelledby="plan-heading">
            <h2 id="plan-heading" className="font-display text-[22px] font-light tracking-tight text-ink">The plan</h2>
            <Panel as="div" className="mt-4 overflow-hidden">
              <ol className="divide-y divide-rule">
                {brief.sections_json.map((section) => (
                  <Section key={section.key} section={section} />
                ))}
              </ol>
            </Panel>
          </section>

          {isNewPost && members.length ? (
            <section aria-labelledby="outline-heading">
              <h2 id="outline-heading" className="font-display text-[22px] font-light tracking-tight text-ink">
                Outline from real searches
              </h2>
              <p className="mt-2 max-w-2xl text-[13px] leading-6 text-pretty text-ink-soft">
                Every heading below is a search people made. Questions become sections the post answers
                directly; the other searches are terms the post should cover.
              </p>
              <Panel as="div" className="mt-4 p-6">
                <p className="eyebrow">Heading 1</p>
                <p className="mt-1 font-display text-[18px] font-medium tracking-tight text-ink">
                  {sentenceCase(brief.cluster_label)}
                </p>
                {questions.length ? (
                  <>
                    <p className="eyebrow mt-6">Sections to answer</p>
                    <ol className="mt-2 grid gap-2">
                      {questions.slice(0, 10).map((member) => (
                        <li key={member.query_hash} className="flex gap-3 text-[14px] leading-6 text-ink">
                          <span className="font-mono text-[12px] leading-6 text-accent">H2</span>
                          {sentenceCase(member.term)}
                          {member.term.trim().endsWith("?") ? "" : "?"}
                        </li>
                      ))}
                    </ol>
                  </>
                ) : null}
                {others.length ? (
                  <>
                    <p className="eyebrow mt-6">Also cover</p>
                    <ul className="mt-2 flex flex-wrap gap-2">
                      {others.slice(0, 16).map((member) => (
                        <li
                          key={member.query_hash}
                          className="rounded-full border border-rule-strong px-3 py-1 text-[13px] text-ink-soft"
                        >
                          {member.term}
                        </li>
                      ))}
                    </ul>
                  </>
                ) : null}
              </Panel>
            </section>
          ) : null}

          <section aria-labelledby="searches-heading">
            <h2 id="searches-heading" className="font-display text-[22px] font-light tracking-tight text-ink">
              Searches behind this topic
            </h2>
            <p className="mt-2 max-w-2xl text-[13px] leading-6 text-pretty text-ink-soft">
              From this site&rsquo;s Search Console. Search terms are stored encrypted, and each time they are
              shown the view is recorded in the audit trail.
            </p>
            {members.length ? (
              <Panel as="div" className="mt-4 overflow-x-auto">
                <table className="w-full min-w-[520px] text-left text-[13px]">
                  <thead className="text-ink-faint">
                    <tr>
                      <th scope="col" className="px-6 py-3 font-normal">Search</th>
                      <th scope="col" className="px-3 py-3 text-right font-normal">Impressions</th>
                      <th scope="col" className="px-3 py-3 text-right font-normal">Clicks</th>
                      <th scope="col" className="px-6 py-3 text-right font-normal">Position</th>
                    </tr>
                  </thead>
                  <tbody>
                    {members.map((member) => (
                      <tr key={member.query_hash} className="border-t border-rule">
                        <td className="px-6 py-2.5 text-ink">
                          {member.term}
                          {member.is_question ? (
                            <span className="ml-2 font-mono text-[10px] tracking-wider text-accent uppercase">question</span>
                          ) : null}
                        </td>
                        <td className="px-3 py-2.5 text-right font-mono text-ink tabular">{Math.round(member.impressions)}</td>
                        <td className="px-3 py-2.5 text-right font-mono text-ink-soft tabular">{Math.round(member.clicks)}</td>
                        <td className="px-6 py-2.5 text-right font-mono text-ink-soft tabular">{member.position.toFixed(1)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </Panel>
            ) : (
              <p className="mt-4 text-[13px] text-ink-faint">
                The searches for this topic are not available. The keyword analysis may have been rebuilt since
                this brief was written.
              </p>
            )}
          </section>
        </div>

        <aside aria-labelledby="decide-heading" className="flex flex-col gap-4 lg:sticky lg:top-24 lg:self-start">
          {isNewPost && brief.status !== "dismissed" ? (
            <Panel className="p-6">
              <h2 className="font-display text-[17px] font-medium tracking-tight text-ink">Write a draft</h2>
              {liveDraft ? (
                <>
                  <p className="mt-2 text-[13px] leading-6 text-pretty text-ink-soft">
                    A draft of this brief is {liveDraft.status === "ready" ? "waiting for review" : "being written"}.
                  </p>
                  <Link href={`/pilot/workspace/drafts/${liveDraft.id}`} className={`${button.primary} mt-4 w-full`}>
                    Open the draft
                  </Link>
                </>
              ) : aiProviders.length ? (
                <form action={requestDraft} className="mt-3 flex flex-col gap-3">
                  <p className="text-[13px] leading-6 text-pretty text-ink-soft">
                    An AI writes a first draft from this plan, using your workspace&rsquo;s own key. You review
                    and fact-check it before anything goes further.
                  </p>
                  <input type="hidden" name="brief_id" value={brief.id} />
                  <input type="hidden" name="idempotency_key" value={randomUUID()} />
                  <fieldset className="flex flex-col gap-2">
                    <legend className="mb-1 text-[13px] font-medium text-ink">Write it with</legend>
                    {AI_PROVIDERS.filter((item) => aiProviders.includes(item.id)).map((item, index) => (
                      <label key={item.id} className="flex items-center gap-2 text-[13px] text-ink">
                        <input
                          type="radio"
                          name="provider"
                          value={item.id}
                          defaultChecked={index === 0}
                          className="accent-[var(--accent)]"
                        />
                        {item.label}
                      </label>
                    ))}
                  </fieldset>
                  <Field label="Author" hint="The person the post will be published under.">
                    <input id="draft-author-name" name="author_name" maxLength={120} placeholder="Your name" className={input} />
                  </Field>
                  <button type="submit" className={`${button.primary} w-full`}>Write a draft</button>
                </form>
              ) : (
                <p className="mt-2 text-[13px] leading-6 text-pretty text-ink-soft">
                  Drafting uses your workspace&rsquo;s own AI key. Add an Anthropic or OpenAI key under{" "}
                  <Link href="/settings/keys" className="text-accent underline underline-offset-4">Settings → Keys</Link>{" "}
                  to turn it on.
                </p>
              )}
              {drafts.filter((draft) => draft.id !== liveDraft?.id).length ? (
                <ul className="mt-4 flex flex-col gap-1 border-t border-rule pt-3 text-[12px]">
                  {drafts
                    .filter((draft) => draft.id !== liveDraft?.id)
                    .map((draft) => (
                      <li key={draft.id} className="flex justify-between gap-2">
                        <Link href={`/pilot/workspace/drafts/${draft.id}`} className="truncate text-ink-soft underline-offset-4 hover:underline">
                          {draft.title ?? "Untitled draft"}
                        </Link>
                        <span className="shrink-0 font-mono text-ink-faint">{draft.status}</span>
                      </li>
                    ))}
                </ul>
              ) : null}
            </Panel>
          ) : null}
          <Panel className="p-6">
            <h2 id="decide-heading" className="font-display text-[17px] font-medium tracking-tight text-ink">
              Your decision
            </h2>
            <p className="mt-2 text-[13px] leading-6 text-pretty text-ink-soft">
              This records what you decide about the topic. Nothing is written to the site.
            </p>
            {brief.status === "dismissed" && brief.dismissed_reason ? (
              <p className="mt-4 rounded-xl bg-sunk px-4 py-3 text-[13px] leading-6 text-ink-soft">
                Dismissed: {brief.dismissed_reason}
              </p>
            ) : null}
            <div className="mt-5 flex flex-col gap-2">
              {moves.map((move) => (
                <form key={move.status} action={updateBriefStatus}>
                  <input type="hidden" name="brief_id" value={brief.id} />
                  <input type="hidden" name="status" value={move.status} />
                  <button type="submit" className={`${move.primary ? button.primary : button.secondary} w-full`}>
                    {move.label}
                  </button>
                </form>
              ))}
            </div>
            {brief.status === "queued" || brief.status === "in_progress" ? (
              <form action={updateBriefStatus} className="mt-6 flex flex-col gap-3 border-t border-rule pt-5">
                <input type="hidden" name="brief_id" value={brief.id} />
                <input type="hidden" name="status" value="dismissed" />
                <Field label="Dismiss this topic" hint="Kept with the decision, for whoever looks next.">
                  <input
                    id="dismiss-reason"
                    name="reason"
                    required
                    maxLength={200}
                    placeholder="Why it is not worth writing"
                    className={input}
                  />
                </Field>
                <button type="submit" className={`${button.caution} w-full`}>
                  Dismiss
                </button>
              </form>
            ) : null}
          </Panel>
        </aside>
      </div>
    </main>
  );
}
