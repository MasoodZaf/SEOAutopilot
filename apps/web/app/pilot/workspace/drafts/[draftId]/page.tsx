import type {Metadata} from "next";
import Link from "next/link";
import {notFound, redirect} from "next/navigation";

import {Badge, button, Note, Panel, type Tone} from "@/app/components/ui";
import {lineDiff} from "@/lib/line-diff.mjs";
import {ApiError, apiJson, isMissingTenant} from "@/lib/server-api";

import {LiveRefresh} from "../../../live-refresh";
import {saveDraft, submitDraft, withdrawDraft} from "../../actions";
import type {ContentDraftDetail, DraftFlag} from "../../model";

export const metadata: Metadata = {
  title: "Blog draft",
  robots: {index: false, follow: false},
};

/*
 * One AI-written draft, and the review it needs.
 *
 * The model's output is the starting point, never the answer. Every claim it
 * declared and every figure it did not is listed as a flag; each must be ticked
 * with a note saying how it was checked. The changes against the model's
 * original are shown so an approver can see what a person actually did.
 */

const STATUS: Record<ContentDraftDetail["status"], {label: string; tone: Tone}> = {
  queued: {label: "Waiting to be written", tone: "neutral"},
  running: {label: "Being written", tone: "accent"},
  ready: {label: "Ready for review", tone: "warn"},
  failed: {label: "Failed", tone: "stop"},
  submitted: {label: "Submitted", tone: "good"},
  withdrawn: {label: "Withdrawn", tone: "neutral"},
};

const FAILURES: Record<string, string> = {
  anthropic_key_not_configured: "This workspace has no Anthropic key. Add one under Settings → Keys.",
  openai_key_not_configured: "This workspace has no OpenAI key. Add one under Settings → Keys.",
  anthropic_key_rejected: "Anthropic rejected the stored key. Replace it under Settings → Keys.",
  openai_key_rejected: "OpenAI rejected the stored key. Replace it under Settings → Keys.",
  anthropic_key_forbidden: "The Anthropic key is not allowed to use this model.",
  openai_key_forbidden: "The OpenAI key is not allowed to use this model.",
  content_draft_budget_exhausted: "This month's drafting budget is spent. It resets on the 1st.",
  model_refused: "The model declined to write this post.",
  draft_too_long: "The draft ran past the length limit before it finished.",
  draft_too_short: "The model's answer was too short to be a post.",
  draft_invalid: "The model's answer could not be read as a draft.",
  provider_request_rejected: "The provider rejected the request.",
  provider_rate_limited: "The provider was rate-limiting this key. It will be retried.",
  lease_expired: "The draft was abandoned after three attempts.",
  content_brief_not_new_post: "Only new-post briefs can be drafted.",
};

const ERRORS: Record<string, string> = {
  content_draft_stale: "Someone else saved this draft since you opened it. Reload to see their changes, then edit again.",
  content_draft_slug_invalid: "The slug may only contain lowercase letters, numbers and single hyphens.",
  content_draft_not_editable: "This draft can no longer be edited.",
  content_draft_not_withdrawable: "This draft can no longer be withdrawn.",
  insufficient_permissions_for_content_draft: "Your role cannot edit drafts. An owner, admin, SEO manager or editor can.",
  content_draft_flags_unresolved: "Resolve every flag before sending the post for approval.",
  content_draft_incomplete: "Add a title, slug, body and author before sending the post for approval.",
  github_not_connected: "Connect this site's GitHub repository under Settings → Connectors first. Posts arrive as a pull request there.",
  blog_path_template_invalid: "This site's blog path setting is not usable. It must contain {slug} and stay inside the repository.",
  content_draft_already_submitted: "This draft has already been sent for approval.",
  insufficient_permissions_to_create_proposal: "Your role cannot send changes for approval.",
};

const FLAG_LABEL: Record<string, string> = {
  claim: "Claim to verify",
  figure: "Undeclared figure",
  link: "Link",
  overlap: "Overlap",
};

const field =
  "w-full rounded-xl border border-rule-strong bg-paper px-3.5 py-2 text-[14px] text-ink placeholder:text-ink-faint";

type PageProps = {
  params: Promise<{draftId: string}>;
  searchParams: Promise<{error?: string; saved?: string; withdrawn?: string; submitted?: string}>;
};

async function load(draftId: string) {
  if (!/^[0-9a-f-]{36}$/i.test(draftId)) notFound();
  try {
    const body = await apiJson<{data: ContentDraftDetail; meta: Record<string, number | string>}>(
      `/v1/content-drafts/${draftId}`,
    );
    return body;
  } catch (error) {
    if (isMissingTenant(error)) redirect("/onboarding");
    if (error instanceof ApiError && error.status === 404) notFound();
    throw error;
  }
}

function dollars(micros: number): string {
  return `$${(micros / 1_000_000).toFixed(micros < 10_000 ? 4 : 2)}`;
}

function FlagRow({flag}: {flag: DraftFlag}) {
  return (
    <li className="grid gap-2 px-5 py-4">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={flag.resolved ? "good" : flag.kind === "claim" || flag.kind === "figure" ? "warn" : "neutral"}>
          {flag.resolved ? "Resolved" : (FLAG_LABEL[flag.kind] ?? flag.kind)}
        </Badge>
      </div>
      <p className="text-[14px] leading-6 text-pretty text-ink">{flag.text}</p>
      {flag.detail ? <p className="text-[13px] leading-5 text-pretty text-ink-soft">{flag.detail}</p> : null}
      {flag.resolved ? (
        <p className="text-[13px] text-good">Resolved: {flag.resolution}</p>
      ) : (
        <div className="grid gap-2 sm:grid-cols-[auto_1fr] sm:items-center">
          <label className="flex items-center gap-2 text-[13px] font-medium text-ink">
            <input type="checkbox" name={`resolve:${flag.id}`} className="size-4 accent-[var(--accent)]" />
            Resolved
          </label>
          <input
            id={`note-${flag.id}`}
            name={`note:${flag.id}`}
            maxLength={300}
            placeholder="How: verified against…, corrected to…, removed"
            aria-label={`How this was resolved: ${flag.text.slice(0, 60)}`}
            className={field}
          />
        </div>
      )}
    </li>
  );
}

export default async function DraftPage({params, searchParams}: PageProps) {
  const {draftId} = await params;
  const {error, saved, withdrawn, submitted} = await searchParams;
  const {data: draft, meta} = await load(draftId);
  const status = STATUS[draft.status] ?? {label: draft.status, tone: "neutral" as Tone};
  const working = draft.status === "queued" || draft.status === "running";
  const unresolved = draft.flags_json.filter((flag) => !flag.resolved);
  const diff =
    draft.generated_json?.body_markdown && draft.body_markdown
      ? lineDiff(draft.generated_json.body_markdown, draft.body_markdown)
      : [];
  const changed = diff.some((line) => line.kind !== "same");
  const provider = draft.provider === "openai" ? "OpenAI" : "Claude";

  return (
    <main className="bg-paper text-ink">
      {working ? <LiveRefresh seconds={4} /> : null}
      <header className="dot-field border-b border-rule">
        <div className="mx-auto max-w-6xl px-6 pt-10 pb-8">
          <Link
            href={`/pilot/workspace/briefs/${draft.content_brief_id}`}
            className="inline-flex items-center gap-2 text-[13px] font-medium text-ink-soft transition-colors hover:text-ink"
          >
            <span aria-hidden="true">&larr;</span> The brief
          </Link>
          <p className="eyebrow mt-8">Blog draft · written with {provider}</p>
          <h1 className="mt-3 font-display text-[32px] leading-tight font-light tracking-tight text-balance text-ink sm:text-[44px]">
            {draft.title ?? "Untitled draft"}
          </h1>
          <div className="mt-5 flex flex-wrap items-center gap-2">
            <Badge tone={status.tone}>{status.label}</Badge>
            {draft.status === "ready" ? (
              <Badge tone={unresolved.length ? "warn" : "good"}>
                {unresolved.length ? `${unresolved.length} to resolve` : "All flags resolved"}
              </Badge>
            ) : null}
          </div>
        </div>
      </header>

      <div className="mx-auto grid max-w-6xl gap-8 px-6 py-10 lg:grid-cols-[minmax(0,1fr)_20rem]">
        <div className="flex min-w-0 flex-col gap-6">
          {error ? <Note tone="stop" role="alert">{ERRORS[error] ?? "That did not work. Try again."}</Note> : null}
          {saved ? <Note tone="good" role="status">Saved.</Note> : null}
          {withdrawn ? <Note tone="neutral" role="status">Withdrawn. The brief can be drafted again.</Note> : null}
          {submitted || draft.status === "submitted" ? (
            <Note tone="good" role="status" label="Sent for approval">
              The post is now a proposal to add a new page. Two people other than you must approve it on the{" "}
              <Link href="/pilot" className="underline underline-offset-4">dashboard</Link>, under Proposals; then deploying
              it opens a pull request that a person merges to publish.
            </Note>
          ) : null}

          {working ? (
            <Panel className="px-6 py-14 text-center">
              <p className="font-display text-[20px] font-light text-ink">
                {draft.status === "queued" ? "Waiting for a writer…" : `${provider} is writing the draft…`}
              </p>
              <p className="mt-2 text-[13px] text-ink-faint">
                This usually takes one to three minutes. The page updates by itself.
              </p>
            </Panel>
          ) : null}

          {draft.status === "failed" ? (
            <Note tone="stop" label="The draft could not be written">
              {FAILURES[draft.error_code ?? ""] ?? "Something went wrong writing this draft."}{" "}
              <Link href={`/pilot/workspace/briefs/${draft.content_brief_id}`} className="underline underline-offset-4">
                Back to the brief
              </Link>{" "}
              to try again.
            </Note>
          ) : null}

          {draft.status === "ready" ? (
            <form action={saveDraft} className="flex flex-col gap-6">
              <input type="hidden" name="draft_id" value={draft.id} />
              <input type="hidden" name="version" value={draft.version} />

              {draft.flags_json.length ? (
                <section aria-labelledby="flags-heading">
                  <h2 id="flags-heading" className="font-display text-[22px] font-light tracking-tight text-ink">
                    Check these first
                  </h2>
                  <p className="mt-2 max-w-2xl text-[13px] leading-6 text-pretty text-ink-soft">
                    The model has no sources for your subject. Each claim and figure below must be verified,
                    corrected or removed in the text, then ticked with a note saying how. The draft cannot go
                    for approval until every item is resolved.
                  </p>
                  <Panel as="div" className="mt-4 overflow-hidden">
                    <ol className="divide-y divide-rule">
                      {draft.flags_json.map((flag) => (
                        <FlagRow key={flag.id} flag={flag} />
                      ))}
                    </ol>
                  </Panel>
                </section>
              ) : null}

              <section aria-labelledby="edit-heading" className="flex flex-col gap-4">
                <h2 id="edit-heading" className="font-display text-[22px] font-light tracking-tight text-ink">
                  The post
                </h2>
                <label className="flex flex-col gap-1.5 text-[13px] font-medium text-ink">
                  Title <span className="font-normal text-ink-faint">{(draft.title ?? "").length} characters; keep under 65</span>
                  <input id="draft-title" name="title" defaultValue={draft.title ?? ""} maxLength={200} className={field} />
                </label>
                <div className="grid gap-4 sm:grid-cols-2">
                  <label className="flex flex-col gap-1.5 text-[13px] font-medium text-ink">
                    URL slug
                    <input id="draft-slug" name="slug" defaultValue={draft.slug ?? ""} maxLength={80} className={`${field} font-mono`} />
                  </label>
                  <label className="flex flex-col gap-1.5 text-[13px] font-medium text-ink">
                    Author
                    <input id="draft-author" name="author_name" defaultValue={draft.author_name ?? ""} maxLength={120} placeholder="The person the post is published under" className={field} />
                  </label>
                </div>
                <label className="flex flex-col gap-1.5 text-[13px] font-medium text-ink">
                  Meta description <span className="font-normal text-ink-faint">{(draft.meta_description ?? "").length} characters; 120 to 160 reads best</span>
                  <textarea id="draft-description" name="meta_description" defaultValue={draft.meta_description ?? ""} maxLength={320} rows={2} className={field} />
                </label>
                <label className="flex flex-col gap-1.5 text-[13px] font-medium text-ink">
                  Body <span className="font-normal text-ink-faint">Markdown. ## for section headings.</span>
                  <textarea
                    id="draft-body"
                    name="body_markdown"
                    defaultValue={draft.body_markdown ?? ""}
                    rows={28}
                    className={`${field} font-mono text-[13px] leading-6`}
                  />
                </label>
              </section>

              <div className="flex flex-wrap items-center gap-3">
                <button type="submit" className={button.primary}>Save changes</button>
                <span className="text-[13px] text-ink-faint">Saving records your edits and resolved flags in the audit trail.</span>
              </div>
            </form>
          ) : null}

          {draft.status !== "queued" && draft.status !== "running" && draft.body_markdown && draft.status !== "ready" ? (
            <Panel className="p-6">
              <pre className="font-mono text-[13px] leading-6 whitespace-pre-wrap text-ink">{draft.body_markdown}</pre>
            </Panel>
          ) : null}

          {changed ? (
            <section aria-labelledby="diff-heading">
              <h2 id="diff-heading" className="font-display text-[22px] font-light tracking-tight text-ink">
                Changed from the AI draft
              </h2>
              <Panel as="div" className="mt-4 max-h-[32rem] overflow-auto">
                <pre className="p-4 font-mono text-[12px] leading-5">
                  {diff.map((line, index) => (
                    <span
                      // Positions are stable for a given render; lines repeat.
                      key={index}
                      className={
                        line.kind === "added"
                          ? "block bg-good-soft text-good"
                          : line.kind === "removed"
                            ? "block bg-stop-soft text-stop line-through"
                            : "block text-ink-faint"
                      }
                    >
                      {line.kind === "added" ? "+ " : line.kind === "removed" ? "− " : "  "}
                      {line.text || " "}
                    </span>
                  ))}
                </pre>
              </Panel>
            </section>
          ) : null}
        </div>

        <aside aria-labelledby="about-heading" className="flex flex-col gap-4 lg:sticky lg:top-24 lg:self-start">
          <Panel className="p-6">
            <h2 id="about-heading" className="font-display text-[17px] font-medium tracking-tight text-ink">
              About this draft
            </h2>
            <dl className="mt-4 grid gap-2 text-[13px]">
              <div className="flex justify-between gap-3"><dt className="text-ink-faint">Written by</dt><dd className="text-right font-mono text-ink">{draft.model ?? provider}</dd></div>
              <div className="flex justify-between gap-3"><dt className="text-ink-faint">Tokens</dt><dd className="font-mono text-ink tabular">{draft.input_tokens.toLocaleString()} in · {draft.output_tokens.toLocaleString()} out</dd></div>
              <div className="flex justify-between gap-3"><dt className="text-ink-faint">Estimated cost</dt><dd className="font-mono text-ink tabular">{dollars(draft.cost_micros)}</dd></div>
              <div className="flex justify-between gap-3"><dt className="text-ink-faint">This month</dt><dd className="font-mono text-ink tabular">{dollars(Number(meta.spent_this_month_micros ?? 0))} of {dollars(Number(meta.monthly_budget_micros ?? 0))}</dd></div>
            </dl>
            <p className="mt-4 text-[12px] leading-5 text-pretty text-ink-faint">
              Billed to your workspace&rsquo;s own {provider} key. Costs are estimated from list prices;
              your provider&rsquo;s invoice is the final figure.
            </p>
          </Panel>

          {draft.status === "ready" ? (
            <Panel className="p-6">
              <h2 className="font-display text-[17px] font-medium tracking-tight text-ink">Send for approval</h2>
              <p className="mt-2 text-[13px] leading-6 text-pretty text-ink-soft">
                {unresolved.length
                  ? `Resolve the ${unresolved.length} remaining ${unresolved.length === 1 ? "flag" : "flags"} and save first.`
                  : "Creates a proposal to add this post as a new page. Two people other than the author must approve it; deploying then opens a pull request a person merges."}
              </p>
              <form action={submitDraft} className="mt-4">
                <input type="hidden" name="draft_id" value={draft.id} />
                <button
                  type="submit"
                  disabled={unresolved.length > 0}
                  aria-disabled={unresolved.length > 0}
                  className={`${button.primary} w-full`}
                >
                  Send for approval
                </button>
              </form>
              <p className="mt-3 text-[12px] leading-5 text-ink-faint">Save your edits before sending: unsaved changes are not included.</p>
            </Panel>
          ) : null}

          {draft.status === "ready" || draft.status === "queued" || draft.status === "failed" ? (
            <details className="rounded-2xl border border-rule bg-surface p-6">
              <summary className="text-[13px] font-medium text-stop">Withdraw this draft</summary>
              <p className="mt-3 text-[13px] leading-6 text-pretty text-ink-soft">
                The draft is kept for the record but can no longer be edited or submitted. The brief can be drafted again.
              </p>
              <form action={withdrawDraft} className="mt-3">
                <input type="hidden" name="draft_id" value={draft.id} />
                <button type="submit" className={`${button.caution} w-full`}>Yes, withdraw it</button>
              </form>
            </details>
          ) : null}
        </aside>
      </div>
    </main>
  );
}
