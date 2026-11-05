import {
  approveRedditDraft,
  dismissRedditDraft,
  draftRedditReply,
  markRedditPosted,
  saveRedditDraft,
} from "./actions";
import {RedditHandOff} from "./reddit-hand-off";

export type RedditThread = {
  url: string;
  subreddit: string;
  title: string;
  questions: string[];
  providers: string[];
  citations: number;
  site_also_cited: boolean;
  last_seen_at: string;
  drafted: boolean;
};

export type RedditDraft = {
  id: string;
  kind: "post" | "reply";
  subreddit: string;
  thread_url: string | null;
  question: string;
  page_url: string | null;
  title: string;
  body: string;
  status: "draft" | "approved" | "handed_off" | "posted" | "dismissed";
  model: string;
  cost_micros: number;
  posted_url: string | null;
  created_at: string;
};

const heading = "font-display text-[17px] font-medium tracking-tight text-balance text-ink";
const muted = "text-xs text-pretty text-ink-faint";
const chip = "rounded-full border px-2.5 py-0.5 font-mono text-[11px] font-medium tracking-wider uppercase";
const field = "rounded-xl border border-rule-strong bg-surface px-3 py-2 text-sm text-ink placeholder:text-ink-soft";
const primary = "rounded-full border border-good-rule bg-good-soft px-3 py-1 text-[12px] font-medium text-good hover:bg-good-soft";
const secondary = "rounded-full border border-rule-strong bg-paper px-3 py-1 text-[12px] font-medium text-ink-soft hover:bg-sunk";

const statusLabel: Record<RedditDraft["status"], string> = {
  draft: "Needs review",
  approved: "Approved",
  handed_off: "Opened on Reddit",
  posted: "Posted",
  dismissed: "Dismissed",
};

function HostField({host}: {host: string}) {
  return <input type="hidden" name="site_host" value={host} />;
}

export function RedditTab({
  host,
  siteName,
  threads,
  drafts,
}: {
  host: string;
  siteName: string;
  threads: RedditThread[];
  drafts: RedditDraft[];
}) {
  const live = drafts.filter((item) => item.status !== "dismissed");
  return (
    <div className="flex flex-col gap-6">
      <section className="rounded-xl border border-rule bg-surface p-4">
        <h3 className={heading}>Reddit</h3>
        <p className={`${muted} mt-1 max-w-3xl`}>
          ChatGPT and Perplexity quote Reddit threads when they answer questions. A helpful reply there, from you and
          saying who you are, can be read by people and by those answer engines. SEO Autopilot finds the threads,
          drafts the reply with your own AI key, and you approve it. Then you post it on Reddit yourself: nothing is
          posted for you, and there is no Reddit login to connect. At most one hand-off per subreddit a week, and three
          a day.
        </p>
      </section>

      <section>
        <h3 className={heading}>Threads answer engines cite for your questions</h3>
        {threads.length === 0 ? (
          <p className={`${muted} mt-2`}>
            None yet. Threads appear here after the tracked questions on the Reports tab are asked, when an answer cites
            Reddit. You can still reply to any thread below.
          </p>
        ) : (
          <ul className="mt-3 flex flex-col gap-3">
            {threads.map((thread) => (
              <li key={thread.url} className="rounded-xl border border-rule bg-surface p-3">
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <a href={thread.url} target="_blank" rel="noreferrer noopener" className="text-sm font-medium text-ink underline-offset-2 hover:underline">
                    r/{thread.subreddit} · {thread.title || "thread"}
                  </a>
                  <span className={muted}>
                    cited <span className="tabular-nums">{thread.citations}</span>× by {thread.providers.join(", ")}
                    {thread.site_also_cited ? " · your site was cited too" : ""}
                  </span>
                </div>
                <p className={`${muted} mt-1`}>For: {thread.questions.join(" · ")}</p>
                {thread.drafted ? (
                  <p className={`${muted} mt-2`}>A draft for this thread is below.</p>
                ) : (
                  <ReplyForm host={host} threadUrl={thread.url} question={thread.questions[0] ?? ""} />
                )}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <div className="rounded-xl border border-rule bg-surface p-3">
          <h3 className="text-sm font-semibold text-ink">Reply to another thread</h3>
          <ReplyForm host={host} question="" />
        </div>
        <div className="rounded-xl border border-rule bg-surface p-3">
          <h3 className="text-sm font-semibold text-ink">Write a new post</h3>
          <form action={draftRedditReply} className="mt-2 flex flex-col gap-2">
            <HostField host={host} />
            <label className={muted} htmlFor="reddit-subreddit">Subreddit</label>
            <input id="reddit-subreddit" name="subreddit" required placeholder="personalfinance" className={field} />
            <label className={muted} htmlFor="reddit-topic">What it answers</label>
            <input id="reddit-topic" name="question" required maxLength={500} placeholder="How is a loan EMI actually calculated?" className={field} />
            <label className={muted} htmlFor="reddit-page">Page on {siteName} it draws on (optional)</label>
            <input id="reddit-page" name="page_url" placeholder={`https://${host}/emi-calculator`} className={field} />
            <p className={muted}>Read the subreddit&apos;s rules first; many allow no links at all.</p>
            <button type="submit" className={`${primary} self-start`}>Draft post</button>
          </form>
        </div>
      </section>

      <section>
        <h3 className={heading}>Drafts</h3>
        {live.length === 0 ? (
          <p className={`${muted} mt-2`}>No drafts yet. Draft a reply to one of the threads above.</p>
        ) : (
          <ul className="mt-3 flex flex-col gap-3">
            {live.map((draft) => (
              <li key={draft.id} className="rounded-xl border border-rule bg-surface p-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="text-sm font-medium text-ink">
                    {draft.kind === "reply" ? "Reply in" : "Post to"} r/{draft.subreddit}
                  </span>
                  <span className={`${chip} ${draft.status === "posted" ? "border-good-rule bg-good-soft text-good" : "border-rule-strong bg-paper text-ink-faint"}`}>
                    {statusLabel[draft.status]}
                  </span>
                </div>
                <p className={`${muted} mt-1`}>
                  {draft.question} · written by {draft.model} · <span className="tabular-nums">${(draft.cost_micros / 1_000_000).toFixed(3)}</span>
                </p>
                {draft.status === "draft" || draft.status === "approved" ? (
                  <form action={saveRedditDraft} className="mt-2 flex flex-col gap-2">
                    <HostField host={host} />
                    <input type="hidden" name="draft_id" value={draft.id} />
                    {draft.kind === "post" && (
                      <input name="title" defaultValue={draft.title} maxLength={300} aria-label="Post title" className={field} />
                    )}
                    <textarea name="body" defaultValue={draft.body} rows={7} maxLength={10000} aria-label="Text to post" className={field} />
                    <button type="submit" className={`${secondary} self-start`}>Save edits</button>
                  </form>
                ) : (
                  <pre tabIndex={0} aria-label="Draft text" className="mt-2 max-h-56 overflow-auto rounded-xl border border-rule bg-sunk p-3 text-[13px] whitespace-pre-wrap text-ink-soft">
                    {draft.kind === "post" ? `${draft.title}\n\n${draft.body}` : draft.body}
                  </pre>
                )}
                <div className="mt-3 flex flex-wrap items-start gap-2">
                  {draft.status === "draft" && (
                    <form action={approveRedditDraft}>
                      <HostField host={host} />
                      <input type="hidden" name="draft_id" value={draft.id} />
                      <button type="submit" className={primary}>I read it, approve</button>
                    </form>
                  )}
                  {(draft.status === "approved" || draft.status === "handed_off") && (
                    <RedditHandOff draftId={draft.id} kind={draft.kind} />
                  )}
                  {draft.status === "handed_off" && (
                    <form action={markRedditPosted} className="flex flex-wrap items-center gap-2">
                      <HostField host={host} />
                      <input type="hidden" name="draft_id" value={draft.id} />
                      <input name="posted_url" required placeholder="Paste the link to what you posted" aria-label="Link to the post" className={`${field} py-1 text-[12px]`} />
                      <button type="submit" className={secondary}>Record it</button>
                    </form>
                  )}
                  {draft.status === "posted" && draft.posted_url && (
                    <a href={draft.posted_url} target="_blank" rel="noreferrer noopener" className="text-[12px] text-good underline-offset-2 hover:underline">
                      View on Reddit
                    </a>
                  )}
                  {draft.status !== "posted" && (
                    <form action={dismissRedditDraft}>
                      <HostField host={host} />
                      <input type="hidden" name="draft_id" value={draft.id} />
                      <button type="submit" className={secondary}>Dismiss</button>
                    </form>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

function ReplyForm({host, threadUrl, question}: {host: string; threadUrl?: string; question: string}) {
  return (
    <details className="mt-2">
      <summary className="cursor-pointer text-[12px] font-medium text-good">Draft a reply</summary>
      <form action={draftRedditReply} className="mt-2 flex flex-col gap-2">
        <HostField host={host} />
        {threadUrl ? (
          <input type="hidden" name="thread_url" value={threadUrl} />
        ) : (
          <input name="thread_url" required placeholder="https://www.reddit.com/r/…/comments/…" aria-label="Thread link" className={field} />
        )}
        <input name="question" required maxLength={500} defaultValue={question} placeholder="The question the thread asks" aria-label="Question" className={field} />
        <textarea
          name="context"
          rows={4}
          maxLength={6000}
          placeholder="Optional: paste the thread's post and top comments, so the reply answers what was actually asked."
          aria-label="Thread text"
          className={field}
        />
        <input name="page_url" placeholder="Optional: the page on your site that answers it" aria-label="Page URL" className={field} />
        <button type="submit" className={`${primary} self-start`}>Draft reply</button>
      </form>
    </details>
  );
}
