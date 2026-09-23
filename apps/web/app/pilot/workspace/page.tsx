import type {Metadata} from "next";
import Link from "next/link";

import {redirect} from "next/navigation";

import {ApiError, apiJson, isMissingTenant} from "@/lib/server-api";

import {pilotPath, selectSite} from "../site-selection.mjs";
import {runRoutineNow, scheduleRoutine, sendMessage, startConversation} from "./actions";
import {resolveTab, workspacePath, workspaceTabs, type WorkspaceTab} from "./paths";
import {
  type AgentMessage,
  type AgentSession,
  type AgentTask,
  type AiVisibility,
  type ContentBrief,
  type KeywordCluster,
  type ReportSummary,
  type Routine,
  type RoutineRun,
  type Skill,
} from "./model";

export const metadata: Metadata = {title: "Agent workspace — SEO Autopilot"};

type Search = {[key: string]: string | string[] | undefined};

function one(value: string | string[] | undefined): string {
  return Array.isArray(value) ? (value[0] ?? "") : (value ?? "");
}

async function maybe<T>(path: string): Promise<T | null> {
  try {
    return await apiJson<T>(path);
  } catch (error) {
    // Checked before the blanket ApiError branch below, which would otherwise
    // absorb it: having no workspace is not a missing read model, and rendering
    // it as one shows a new account a full set of empty panels instead of the
    // page that gives them a workspace.
    if (isMissingTenant(error)) redirect("/onboarding");
    // A missing read model is an expected state before the first routine runs.
    if (error instanceof ApiError) return null;
    throw error;
  }
}

const panel = "rounded-2xl border border-rule bg-surface p-6";
const heading = "font-display text-[17px] font-medium tracking-tight text-ink";
const muted = "text-xs text-ink-faint";
const chip = "rounded-full border px-2.5 py-0.5 font-mono text-[11px] font-medium tracking-wider uppercase";

function Empty({children}: {children: React.ReactNode}) {
  return <p className="rounded-xl border border-dashed border-rule p-4 text-sm text-ink-faint">{children}</p>;
}

function statusChip(status: string): string {
  if (status === "completed" || status === "delivered") return `${chip} border-good-rule bg-good-soft text-good`;
  if (status === "failed" || status === "blocked") return `${chip} border-stop-rule bg-stop-soft text-stop`;
  if (status === "skipped") return `${chip} border-warn-rule bg-warn-soft text-warn`;
  return `${chip} border-rule-strong bg-surface text-ink-soft`;
}

/** Renders the agent's bounded markup: paragraphs, bullets, and **bold**. */
function AgentBody({body}: {body: string}) {
  return (
    <div className="space-y-1.5 text-sm text-ink">
      {body.split("\n").map((line, index) => {
        const trimmed = line.trim();
        if (!trimmed) return null;
        const bullet = trimmed.startsWith("- ");
        const text = bullet ? trimmed.slice(2) : trimmed;
        const parts = text.split(/\*\*(.+?)\*\*/g);
        const rendered = parts.map((part, partIndex) =>
          partIndex % 2 === 1 ? (
            <strong key={partIndex} className="font-semibold text-ink">{part}</strong>
          ) : (
            <span key={partIndex}>{part}</span>
          ),
        );
        return bullet ? (
          <div key={index} className="flex gap-2 pl-1">
            <span className="text-ink-soft">•</span>
            <span>{rendered}</span>
          </div>
        ) : (
          <p key={index}>{rendered}</p>
        );
      })}
    </div>
  );
}

export default async function WorkspacePage({searchParams}: {searchParams: Promise<Search>}) {
  const params = await searchParams;
  const tab: WorkspaceTab = resolveTab(one(params.tab));
  const errorCode = one(params.error);

  const sites = await maybe<{data: Array<{id: string; normalized_host: string; name: string}>}>("/v1/sites");
  const owned = sites?.data ?? [];
  // The site is chosen from what this workspace actually has, so a host it does
  // not own selects its own first site rather than somebody else's.
  const site = selectSite(owned, one(params.site));
  const target = site
    ? {host: site.normalized_host, name: site.name}
    : {host: "", name: "This workspace"};
  const skills = (await maybe<{data: Skill[]}>("/v1/skills"))?.data ?? [];

  const sessions = site ? ((await maybe<{data: AgentSession[]}>(`/v1/sites/${site.id}/agent-sessions`))?.data ?? []) : [];
  const requestedSession = one(params.session);
  const activeSession = sessions.find((item) => item.id === requestedSession) ?? sessions[0];
  const messages = activeSession
    ? ((await maybe<{data: AgentMessage[]}>(`/v1/agent-sessions/${activeSession.id}/messages`))?.data ?? [])
    : [];

  const tasks = site ? ((await maybe<{data: AgentTask[]}>(`/v1/sites/${site.id}/agent-tasks`))?.data ?? []) : [];
  const routines = site ? ((await maybe<{data: Routine[]}>(`/v1/sites/${site.id}/routines`))?.data ?? []) : [];
  const runs = site ? ((await maybe<{data: RoutineRun[]}>(`/v1/sites/${site.id}/routine-runs`))?.data ?? []) : [];
  const reports = site ? ((await maybe<{data: ReportSummary[]}>(`/v1/sites/${site.id}/reports`))?.data ?? []) : [];
  const clusters = site ? ((await maybe<{data: KeywordCluster[]}>(`/v1/sites/${site.id}/keyword-clusters?limit=10`))?.data ?? []) : [];
  const briefs = site ? ((await maybe<{data: ContentBrief[]}>(`/v1/sites/${site.id}/content-briefs?limit=10`))?.data ?? []) : [];
  const visibility = site ? ((await maybe<{data: AiVisibility[]}>(`/v1/sites/${site.id}/ai-visibility?limit=2`))?.data ?? []) : [];

  const routineByKind = new Map(routines.map((item) => [item.kind, item]));

  return (
    <main className="bg-paper text-ink">
      <header className="dot-field border-b border-rule">
        <div className="mx-auto max-w-6xl px-6 pt-12 pb-8">
          <div className="flex flex-col gap-6 lg:flex-row lg:items-end lg:justify-between">
            <div className="min-w-0">
              <p className="eyebrow flex items-center gap-3">
                Agent workspace
                <span className={`${chip} border-accent-rule bg-accent-soft text-accent`}>Internal alpha</span>
              </p>
              <h1 className="mt-3 font-display text-[40px] leading-none font-light tracking-tight break-words text-ink sm:text-[56px]">
                {target.host || target.name}
              </h1>
              <p className="mt-5 max-w-2xl text-[13px] leading-6 text-pretty text-ink-soft">
                The agent answers from stored evidence and can queue analysis. It cannot publish a change: every change still goes through a reviewed proposal.
              </p>
            </div>
            <Link href={pilotPath(target.host)} className="inline-flex items-center gap-2 self-start rounded-full border border-rule-strong px-4 py-2 text-[13px] font-medium text-ink transition-colors hover:border-ink-faint hover:bg-sunk lg:self-auto">
              <span aria-hidden="true">&larr;</span> Back to operations
            </Link>
          </div>

          <nav aria-label="Sites in this workspace" className="mt-10 flex flex-wrap gap-2">
            {owned.map((portfolioSite) => {
              const selected = portfolioSite.normalized_host === target.host;
              return (
                <Link
                  key={portfolioSite.id}
                  href={workspacePath(portfolioSite.normalized_host, {tab})}
                  aria-current={selected ? "page" : undefined}
                  className={`rounded-full border px-4 py-1.5 text-[13px] font-medium transition-colors ${
                    selected ? "border-ink bg-ink text-ink-inverse" : "border-rule-strong bg-surface text-ink-soft hover:border-ink-faint hover:text-ink"
                  }`}
                >
                  {portfolioSite.name}
                </Link>
              );
            })}
          </nav>
        </div>
      </header>

      <div className="mx-auto flex w-full max-w-6xl flex-col gap-6 px-6 py-10">
        {errorCode && (
          <p role="alert" className="rounded-xl border border-stop-rule bg-stop-soft px-4 py-3 text-sm text-stop">
            The last action did not complete: <code>{errorCode}</code>
          </p>
        )}

        {!site && (
          <Empty>
            This workspace has no verified site yet. Add one under Settings &rarr; Sites and
            prove the domain with a DNS record first.
          </Empty>
        )}

        <nav aria-label="Workspace sections">
          <ul className="inline-flex flex-wrap gap-1 rounded-full border border-rule bg-surface p-1">
            {workspaceTabs.map((name) => {
              const selected = name === tab;
              return (
                <li key={name}>
                  <Link
                    href={workspacePath(target.host, {tab: name, session: activeSession?.id})}
                    aria-current={selected ? "page" : undefined}
                    className={`block rounded-full px-4 py-1.5 text-[13px] font-medium capitalize transition-colors ${
                      selected ? "bg-ink text-ink-inverse" : "text-ink-soft hover:bg-sunk hover:text-ink"
                    }`}
                  >
                    {name}
                  </Link>
                </li>
              );
            })}
          </ul>
        </nav>

        {tab === "chat" && (
          <section aria-labelledby="chat-heading" className="grid grid-cols-1 gap-4 lg:grid-cols-[240px_1fr]">
            <div className={panel}>
              <h2 id="chat-heading" className={heading}>Conversations</h2>
              <form action={startConversation} className="mt-3 flex flex-col gap-2">
                <input type="hidden" name="site_host" value={target.host} />
                <label className="sr-only" htmlFor="conversation-title">Conversation title</label>
                <input
                  id="conversation-title"
                  name="title"
                  maxLength={200}
                  placeholder="New conversation"
                  className="rounded-xl border border-rule-strong bg-surface px-2 py-1.5 text-sm text-ink placeholder:text-ink-soft"
                />
                <button type="submit" disabled={!site} className="rounded-full border border-rule-strong bg-surface px-3 py-1.5 text-xs font-medium text-ink hover:bg-sunk disabled:opacity-40">
                  Start
                </button>
              </form>
              <ul className="mt-4 flex flex-col gap-1">
                {sessions.map((item) => (
                  <li key={item.id}>
                    <Link
                      href={workspacePath(target.host, {tab: "chat", session: item.id})}
                      className={`block truncate rounded px-2 py-1.5 text-xs ${
                        item.id === activeSession?.id ? "bg-sunk text-ink" : "text-ink-faint hover:bg-surface"
                      }`}
                    >
                      {item.title}
                    </Link>
                  </li>
                ))}
                {sessions.length === 0 && <li className={muted}>No conversations yet.</li>}
              </ul>
            </div>

            <div className={panel}>
              {!activeSession && <Empty>Start a conversation to ask the agent for an audit, keyword clusters, briefs, or a report.</Empty>}
              {activeSession && (
                <>
                  <div className="flex items-baseline justify-between">
                    <h3 className={heading}>{activeSession.title}</h3>
                    <span className={muted}>{messages.length} messages</span>
                  </div>
                  <ol className="mt-4 flex flex-col gap-3">
                    {messages.map((message) => (
                      <li
                        key={message.id}
                        className={`rounded border p-3 ${
                          message.role === "user" ? "border-rule bg-surface/60" : "border-rule bg-surface"
                        }`}
                      >
                        <div className="mb-2 flex items-center gap-2">
                          <span className={`${chip} ${message.role === "user" ? "border-rule-strong bg-sunk text-ink-soft" : "border-good-rule bg-good-soft text-good"}`}>
                            {message.role === "user" ? "You" : "Agent"}
                          </span>
                          {message.skill_key && <span className={`${chip} border-rule-strong bg-paper text-ink-faint`}>{message.skill_key}</span>}
                          {message.evidence_json.length > 0 && (
                            <span className={muted}>{message.evidence_json.length} evidence references</span>
                          )}
                        </div>
                        {message.role === "agent" ? <AgentBody body={message.body} /> : <p className="text-sm text-ink-soft">{message.body}</p>}
                      </li>
                    ))}
                    {messages.length === 0 && <li className={muted}>No messages yet. Try one of the examples on the Skills tab.</li>}
                  </ol>

                  <form action={sendMessage} className="mt-5 flex flex-col gap-2 border-t border-rule pt-4">
                    <input type="hidden" name="site_host" value={target.host} />
                    <input type="hidden" name="session_id" value={activeSession.id} />
                    <label className="sr-only" htmlFor="message-body">Message</label>
                    <textarea
                      id="message-body"
                      name="body"
                      rows={3}
                      required
                      maxLength={8000}
                      placeholder="Ask for an audit, keyword clusters, sitemap coverage, competitors, or the weekly report…"
                      className="rounded-xl border border-rule-strong bg-surface px-3 py-2 text-sm text-ink placeholder:text-ink-soft"
                    />
                    <div className="flex items-center justify-between gap-3">
                      <p className={muted}>The agent reads stored evidence and can queue analysis. It never publishes a change.</p>
                      <button type="submit" className="rounded-full border border-good-rule bg-good-soft px-4 py-1.5 text-xs font-medium text-good hover:bg-good-soft">
                        Send
                      </button>
                    </div>
                  </form>
                </>
              )}
            </div>
          </section>
        )}

        {tab === "tasks" && (
          <section aria-labelledby="tasks-heading" className="flex flex-col gap-4">
            <div className={panel}>
              <h2 id="tasks-heading" className={heading}>Agent tasks</h2>
              <p className={muted}>Every skill invocation, whether it answered from evidence or queued work.</p>
              {tasks.length === 0 ? (
                <div className="mt-3"><Empty>No tasks yet.</Empty></div>
              ) : (
                <ul className="mt-3 flex flex-col gap-2">
                  {tasks.map((task) => (
                    <li key={task.id} className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-rule bg-surface px-3 py-2">
                      <span className="text-sm text-ink">{task.skill_key}</span>
                      <span className="flex items-center gap-2">
                        {task.error_code && <span className={muted}>{task.error_code}</span>}
                        <span className={statusChip(task.status)}>{task.status}</span>
                        <span className={muted}>{new Date(task.created_at).toISOString().slice(0, 16).replace("T", " ")} UTC</span>
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </div>

            <div className={panel}>
              <h2 className={heading}>Routine runs</h2>
              <p className={muted}>A skipped run states why. Nothing here can deploy.</p>
              {runs.length === 0 ? (
                <div className="mt-3"><Empty>No routine has run yet.</Empty></div>
              ) : (
                <ul className="mt-3 flex flex-col gap-2">
                  {runs.map((run) => (
                    <li key={run.id} className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-rule bg-surface px-3 py-2">
                      <span className="text-sm text-ink">
                        {run.kind} <span className={muted}>({run.trigger})</span>
                      </span>
                      <span className="flex items-center gap-2">
                        {(run.skip_reason || run.error_code) && (
                          <span className={muted}>{run.skip_reason ?? run.error_code}</span>
                        )}
                        <span className={statusChip(run.status)}>{run.status}</span>
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </section>
        )}

        {tab === "skills" && (
          <section aria-labelledby="skills-heading" className="flex flex-col gap-4">
            <div className={panel}>
              <h2 id="skills-heading" className={heading}>Skills available to you</h2>
              <p className={muted}>
                This list is the agent&apos;s entire surface. A skill you cannot see is one your role cannot invoke, in chat or anywhere else.
              </p>
              <ul className="mt-4 grid grid-cols-1 gap-3 md:grid-cols-2">
                {skills.map((skill) => (
                  <li key={skill.key} className="rounded-xl border border-rule bg-surface p-3">
                    <div className="flex items-start justify-between gap-2">
                      <h3 className="text-sm font-semibold text-ink">{skill.name}</h3>
                      <span className={`${chip} ${skill.schedules_work ? "border-warn-rule bg-warn-soft text-warn" : "border-rule-strong bg-paper text-ink-faint"}`}>
                        {skill.schedules_work ? "queues work" : "reads evidence"}
                      </span>
                    </div>
                    <p className="mt-1 text-xs text-ink-faint">{skill.description}</p>
                    {activeSession && (
                      <form action={sendMessage} className="mt-3">
                        <input type="hidden" name="site_host" value={target.host} />
                        <input type="hidden" name="session_id" value={activeSession.id} />
                        <input type="hidden" name="body" value={skill.example} />
                        <input type="hidden" name="skill_key" value={skill.key} />
                        <button type="submit" className="rounded-full border border-rule-strong bg-paper px-2.5 py-1 text-[11px] font-medium text-ink-soft hover:bg-sunk">
                          Run: {skill.example}
                        </button>
                      </form>
                    )}
                  </li>
                ))}
              </ul>
            </div>

            <div className={panel}>
              <h2 className={heading}>Schedules</h2>
              <p className={muted}>
                A routine gathers evidence and produces reports. It is skipped while the site is unverified or frozen, and it holds no deployment authority.
              </p>
              <ul className="mt-4 flex flex-col gap-2">
                {["search_console_sync", "analytics_sync", "site_audit", "keyword_refresh", "sitemap_coverage", "content_briefs", "competitor_scan", "ai_visibility_scan", "weekly_report"].map((kind) => {
                  const routine = routineByKind.get(kind);
                  return (
                    <li key={kind} className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-rule bg-surface px-3 py-2">
                      <span className="flex items-center gap-2">
                        <span className="text-sm text-ink">{kind}</span>
                        {routine ? (
                          <span className={statusChip(routine.enabled ? "completed" : "queued")}>
                            {routine.enabled ? `${routine.cadence}, next ${new Date(routine.next_run_at).toISOString().slice(0, 16).replace("T", " ")} UTC` : "not scheduled"}
                          </span>
                        ) : (
                          <span className={muted}>not created</span>
                        )}
                      </span>
                      <span className="flex flex-wrap items-center gap-2">
                        <form action={scheduleRoutine}>
                          <input type="hidden" name="site_host" value={target.host} />
                          <input type="hidden" name="tab" value="skills" />
                          <input type="hidden" name="kind" value={kind} />
                          <input type="hidden" name="cadence" value={kind === "weekly_report" ? "weekly" : "daily"} />
                          <input type="hidden" name="enabled" value={routine?.enabled ? "false" : "true"} />
                          <button type="submit" disabled={!site} className="rounded-full border border-rule-strong bg-paper px-2.5 py-1 text-[11px] font-medium text-ink-soft hover:bg-sunk disabled:opacity-40">
                            {routine?.enabled ? "Disable" : "Enable"}
                          </button>
                        </form>
                        {routine && (
                          <form action={runRoutineNow}>
                            <input type="hidden" name="site_host" value={target.host} />
                            <input type="hidden" name="tab" value="skills" />
                            <input type="hidden" name="routine_id" value={routine.id} />
                            <button type="submit" className="rounded-full border border-rule-strong bg-paper px-2.5 py-1 text-[11px] font-medium text-ink-soft hover:bg-sunk">
                              Run once
                            </button>
                          </form>
                        )}
                      </span>
                    </li>
                  );
                })}
              </ul>
            </div>
          </section>
        )}

        {tab === "reports" && (
          <section aria-labelledby="reports-heading" className="flex flex-col gap-4">
            <div className={panel}>
              <h2 id="reports-heading" className={heading}>Reports</h2>
              {reports.length === 0 ? (
                <div className="mt-3"><Empty>No report has been generated yet. Enable the weekly report or sitemap coverage routine.</Empty></div>
              ) : (
                <ul className="mt-3 flex flex-col gap-2">
                  {reports.map((report) => (
                    <li key={report.id} className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-rule bg-surface px-3 py-2">
                      <span className="text-sm text-ink">{report.kind.replace(/_/g, " ")}</span>
                      <span className="flex items-center gap-3">
                        <span className={muted}>{report.period_start} → {report.period_end}</span>
                        <code className="text-[11px] text-ink-soft">{report.content_hash.slice(0, 12)}</code>
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </div>

            <div className={panel}>
              <h2 className={heading}>Keyword clusters</h2>
              {clusters.length === 0 ? (
                <div className="mt-3"><Empty>No keyword analysis yet. Connect Search Console, then run the keyword refresh routine.</Empty></div>
              ) : (
                <div className="mt-3 overflow-x-auto">
                  <table className="w-full min-w-[640px] text-left text-sm">
                    <thead className="text-xs uppercase tracking-wide text-ink-faint">
                      <tr>
                        <th scope="col" className="py-2 pr-3">Cluster</th>
                        <th scope="col" className="py-2 pr-3">Intent</th>
                        <th scope="col" className="py-2 pr-3">Queries</th>
                        <th scope="col" className="py-2 pr-3">Impressions</th>
                        <th scope="col" className="py-2 pr-3">Avg position</th>
                        <th scope="col" className="py-2 pr-3">Score</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-rule">
                      {clusters.map((cluster) => (
                        <tr key={cluster.id}>
                          <td className="py-2 pr-3 text-ink">
                            {cluster.label}
                            {cluster.answer_engine_candidate && (
                              <span className={`${chip} ml-2 border-accent-rule bg-accent-soft text-accent`}>AEO</span>
                            )}
                          </td>
                          <td className="py-2 pr-3 text-ink-faint">{cluster.intent}</td>
                          <td className="py-2 pr-3 text-ink-faint">{cluster.member_count}</td>
                          <td className="py-2 pr-3 text-ink-faint">{Math.round(cluster.impressions)}</td>
                          <td className="py-2 pr-3 text-ink-faint">{cluster.average_position?.toFixed(1) ?? "—"}</td>
                          <td className="py-2 pr-3 text-ink">{cluster.opportunity_score.toFixed(0)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>

            <div className={panel}>
              <h2 className={heading}>Content briefs</h2>
              <p className={`${muted} mt-1`}>
                Topics from this site&apos;s own searches: new posts to write, and existing pages to refresh.
                Open one to read its plan and decide.
              </p>
              {briefs.length === 0 ? (
                <div className="mt-3"><Empty>No briefs yet. Run the content briefs routine after a keyword refresh.</Empty></div>
              ) : (
                <ul className="mt-4 flex flex-col divide-y divide-rule overflow-hidden rounded-xl border border-rule">
                  {briefs.map((brief) => (
                    <li key={brief.id}>
                      <Link
                        href={`/pilot/workspace/briefs/${brief.id}`}
                        className="flex flex-wrap items-center justify-between gap-3 bg-surface px-4 py-3 transition-colors hover:bg-sunk"
                      >
                        <span className="flex min-w-0 items-center gap-3">
                          <span className={`${chip} ${brief.kind === "new_page" ? "border-accent-rule bg-accent-soft text-accent" : "border-rule-strong bg-paper text-ink-faint"}`}>
                            {brief.kind === "new_page" ? "New post" : "Refresh"}
                          </span>
                          <span className="truncate text-sm text-ink">{brief.cluster_label}</span>
                        </span>
                        <span className="flex items-center gap-3">
                          <span className={`${muted} tabular`}>priority {brief.priority_score.toFixed(0)}</span>
                          <span className={statusChip(brief.status)}>{brief.status.replace("_", " ")}</span>
                          <span aria-hidden="true" className="text-accent">&rsaquo;</span>
                        </span>
                      </Link>
                    </li>
                  ))}
                </ul>
              )}
            </div>

            <div className={panel}>
              <h2 className={heading}>AI search visibility readiness</h2>
              <p className={muted}>
                Measured from this site&apos;s own crawl and search evidence. It does not observe what any answer engine said;
                that needs a certified provider, which is not connected.
              </p>
              {visibility.length === 0 ? (
                <div className="mt-3"><Empty>No readiness snapshot yet. Run the AI visibility routine after a crawl.</Empty></div>
              ) : (
                <p className="mt-3 text-3xl font-semibold text-ink">
                  {visibility[0]?.readiness_score.toFixed(0)}
                  <span className="ml-1 text-base font-normal text-ink-faint">/ 100</span>
                  <span className="ml-3 text-xs font-normal text-ink-faint">as of {visibility[0]?.captured_on}</span>
                </p>
              )}
            </div>
          </section>
        )}
      </div>
    </main>
  );
}
