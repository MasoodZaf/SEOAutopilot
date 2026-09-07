import type {Metadata} from "next";
import Link from "next/link";

import {ApiError, apiJson} from "@/lib/server-api";

import {pilotPath, portfolioSites, resolvePortfolioSite} from "../portfolio.mjs";
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
    // A missing read model is an expected state before the first routine runs.
    if (error instanceof ApiError) return null;
    throw error;
  }
}

const panel = "rounded-lg border border-zinc-800 bg-zinc-950 p-5";
const heading = "text-base font-semibold text-white";
const muted = "text-xs text-zinc-400";
const chip = "rounded border px-2 py-0.5 text-[11px] font-medium";

function Empty({children}: {children: React.ReactNode}) {
  return <p className="rounded border border-dashed border-zinc-800 p-4 text-sm text-zinc-400">{children}</p>;
}

function statusChip(status: string): string {
  if (status === "completed" || status === "delivered") return `${chip} border-emerald-800 bg-emerald-950/50 text-emerald-300`;
  if (status === "failed" || status === "blocked") return `${chip} border-red-800 bg-red-950/50 text-red-300`;
  if (status === "skipped") return `${chip} border-amber-800 bg-amber-950/50 text-amber-300`;
  return `${chip} border-zinc-700 bg-zinc-900 text-zinc-300`;
}

/** Renders the agent's bounded markup: paragraphs, bullets, and **bold**. */
function AgentBody({body}: {body: string}) {
  return (
    <div className="space-y-1.5 text-sm text-zinc-200">
      {body.split("\n").map((line, index) => {
        const trimmed = line.trim();
        if (!trimmed) return null;
        const bullet = trimmed.startsWith("- ");
        const text = bullet ? trimmed.slice(2) : trimmed;
        const parts = text.split(/\*\*(.+?)\*\*/g);
        const rendered = parts.map((part, partIndex) =>
          partIndex % 2 === 1 ? (
            <strong key={partIndex} className="font-semibold text-white">{part}</strong>
          ) : (
            <span key={partIndex}>{part}</span>
          ),
        );
        return bullet ? (
          <div key={index} className="flex gap-2 pl-1">
            <span className="text-zinc-600">•</span>
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
  const target = resolvePortfolioSite(one(params.site));
  const tab: WorkspaceTab = resolveTab(one(params.tab));
  const errorCode = one(params.error);

  const sites = await maybe<{data: Array<{id: string; normalized_host: string; name: string}>}>("/v1/sites");
  const site = sites?.data.find((item) => item.normalized_host === target.host);
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
    <main className="min-h-dvh bg-zinc-950 text-zinc-100">
      <div className="mx-auto flex w-full max-w-6xl flex-col gap-6 px-6 py-10">
        <header className="flex flex-col gap-2 border-b border-zinc-800 pb-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-3">
              <h1 className="text-2xl font-bold tracking-tight text-white">Agent workspace</h1>
              <span className={`${chip} border-emerald-800 bg-emerald-950 text-emerald-400`}>Internal Alpha</span>
            </div>
            <Link href={pilotPath(target.host)} className="rounded border border-zinc-700 bg-zinc-900 px-3 py-1.5 text-xs font-medium text-zinc-300 hover:bg-zinc-800">
              Back to operations
            </Link>
          </div>
          <p className="text-sm text-zinc-400">
            Ask for work on <span className="font-medium text-zinc-200">{target.host}</span>. The agent answers from stored
            evidence and can queue analysis. It cannot publish a change: every change still goes through a reviewed proposal.
          </p>
        </header>

        <nav aria-label="Portfolio sites" className="flex flex-wrap gap-2">
          {portfolioSites.map((portfolioSite) => {
            const selected = portfolioSite.host === target.host;
            return (
              <Link
                key={portfolioSite.host}
                href={workspacePath(portfolioSite.host, {tab})}
                aria-current={selected ? "page" : undefined}
                className={`rounded border px-3 py-1.5 text-xs font-medium ${
                  selected ? "border-zinc-500 bg-zinc-800 text-white" : "border-zinc-800 bg-zinc-900 text-zinc-400 hover:bg-zinc-800"
                }`}
              >
                {portfolioSite.name}
              </Link>
            );
          })}
        </nav>

        {errorCode && (
          <p role="alert" className="rounded border border-red-800 bg-red-950/60 px-3 py-2 text-sm text-red-300">
            The last action did not complete: <code>{errorCode}</code>
          </p>
        )}

        {!site && <Empty>{target.name} is not onboarded yet. Onboard and verify it from the operations page first.</Empty>}

        <nav aria-label="Workspace sections" className="flex gap-1 border-b border-zinc-800">
          {workspaceTabs.map((name) => {
            const selected = name === tab;
            return (
              <Link
                key={name}
                href={workspacePath(target.host, {tab: name, session: activeSession?.id})}
                aria-current={selected ? "page" : undefined}
                className={`rounded-t border-b-2 px-4 py-2 text-sm font-medium capitalize ${
                  selected ? "border-emerald-500 text-white" : "border-transparent text-zinc-400 hover:text-zinc-200"
                }`}
              >
                {name}
              </Link>
            );
          })}
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
                  className="rounded border border-zinc-700 bg-zinc-900 px-2 py-1.5 text-sm text-zinc-100 placeholder:text-zinc-600"
                />
                <button type="submit" disabled={!site} className="rounded border border-zinc-700 bg-zinc-900 px-3 py-1.5 text-xs font-medium text-zinc-200 hover:bg-zinc-800 disabled:opacity-40">
                  Start
                </button>
              </form>
              <ul className="mt-4 flex flex-col gap-1">
                {sessions.map((item) => (
                  <li key={item.id}>
                    <Link
                      href={workspacePath(target.host, {tab: "chat", session: item.id})}
                      className={`block truncate rounded px-2 py-1.5 text-xs ${
                        item.id === activeSession?.id ? "bg-zinc-800 text-white" : "text-zinc-400 hover:bg-zinc-900"
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
                          message.role === "user" ? "border-zinc-800 bg-zinc-900/60" : "border-zinc-800 bg-zinc-900"
                        }`}
                      >
                        <div className="mb-2 flex items-center gap-2">
                          <span className={`${chip} ${message.role === "user" ? "border-zinc-700 bg-zinc-800 text-zinc-300" : "border-emerald-800 bg-emerald-950/50 text-emerald-300"}`}>
                            {message.role === "user" ? "You" : "Agent"}
                          </span>
                          {message.skill_key && <span className={`${chip} border-zinc-700 bg-zinc-950 text-zinc-400`}>{message.skill_key}</span>}
                          {message.evidence_json.length > 0 && (
                            <span className={muted}>{message.evidence_json.length} evidence references</span>
                          )}
                        </div>
                        {message.role === "agent" ? <AgentBody body={message.body} /> : <p className="text-sm text-zinc-300">{message.body}</p>}
                      </li>
                    ))}
                    {messages.length === 0 && <li className={muted}>No messages yet. Try one of the examples on the Skills tab.</li>}
                  </ol>

                  <form action={sendMessage} className="mt-5 flex flex-col gap-2 border-t border-zinc-800 pt-4">
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
                      className="rounded border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-600"
                    />
                    <div className="flex items-center justify-between gap-3">
                      <p className={muted}>The agent reads stored evidence and can queue analysis. It never publishes a change.</p>
                      <button type="submit" className="rounded border border-emerald-700 bg-emerald-950/60 px-4 py-1.5 text-xs font-medium text-emerald-300 hover:bg-emerald-900/60">
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
                    <li key={task.id} className="flex flex-wrap items-center justify-between gap-2 rounded border border-zinc-800 bg-zinc-900 px-3 py-2">
                      <span className="text-sm text-zinc-200">{task.skill_key}</span>
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
                    <li key={run.id} className="flex flex-wrap items-center justify-between gap-2 rounded border border-zinc-800 bg-zinc-900 px-3 py-2">
                      <span className="text-sm text-zinc-200">
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
                  <li key={skill.key} className="rounded border border-zinc-800 bg-zinc-900 p-3">
                    <div className="flex items-start justify-between gap-2">
                      <h3 className="text-sm font-semibold text-white">{skill.name}</h3>
                      <span className={`${chip} ${skill.schedules_work ? "border-amber-800 bg-amber-950/50 text-amber-300" : "border-zinc-700 bg-zinc-950 text-zinc-400"}`}>
                        {skill.schedules_work ? "queues work" : "reads evidence"}
                      </span>
                    </div>
                    <p className="mt-1 text-xs text-zinc-400">{skill.description}</p>
                    {activeSession && (
                      <form action={sendMessage} className="mt-3">
                        <input type="hidden" name="site_host" value={target.host} />
                        <input type="hidden" name="session_id" value={activeSession.id} />
                        <input type="hidden" name="body" value={skill.example} />
                        <input type="hidden" name="skill_key" value={skill.key} />
                        <button type="submit" className="rounded border border-zinc-700 bg-zinc-950 px-2.5 py-1 text-[11px] font-medium text-zinc-300 hover:bg-zinc-800">
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
                    <li key={kind} className="flex flex-wrap items-center justify-between gap-2 rounded border border-zinc-800 bg-zinc-900 px-3 py-2">
                      <span className="flex items-center gap-2">
                        <span className="text-sm text-zinc-200">{kind}</span>
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
                          <button type="submit" disabled={!site} className="rounded border border-zinc-700 bg-zinc-950 px-2.5 py-1 text-[11px] font-medium text-zinc-300 hover:bg-zinc-800 disabled:opacity-40">
                            {routine?.enabled ? "Disable" : "Enable"}
                          </button>
                        </form>
                        {routine && (
                          <form action={runRoutineNow}>
                            <input type="hidden" name="site_host" value={target.host} />
                            <input type="hidden" name="tab" value="skills" />
                            <input type="hidden" name="routine_id" value={routine.id} />
                            <button type="submit" className="rounded border border-zinc-700 bg-zinc-950 px-2.5 py-1 text-[11px] font-medium text-zinc-300 hover:bg-zinc-800">
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
                    <li key={report.id} className="flex flex-wrap items-center justify-between gap-2 rounded border border-zinc-800 bg-zinc-900 px-3 py-2">
                      <span className="text-sm text-zinc-200">{report.kind.replace(/_/g, " ")}</span>
                      <span className="flex items-center gap-3">
                        <span className={muted}>{report.period_start} → {report.period_end}</span>
                        <code className="text-[11px] text-zinc-600">{report.content_hash.slice(0, 12)}</code>
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
                    <thead className="text-xs uppercase tracking-wide text-zinc-500">
                      <tr>
                        <th scope="col" className="py-2 pr-3">Cluster</th>
                        <th scope="col" className="py-2 pr-3">Intent</th>
                        <th scope="col" className="py-2 pr-3">Queries</th>
                        <th scope="col" className="py-2 pr-3">Impressions</th>
                        <th scope="col" className="py-2 pr-3">Avg position</th>
                        <th scope="col" className="py-2 pr-3">Score</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-zinc-900">
                      {clusters.map((cluster) => (
                        <tr key={cluster.id}>
                          <td className="py-2 pr-3 text-zinc-200">
                            {cluster.label}
                            {cluster.answer_engine_candidate && (
                              <span className={`${chip} ml-2 border-sky-800 bg-sky-950/50 text-sky-300`}>AEO</span>
                            )}
                          </td>
                          <td className="py-2 pr-3 text-zinc-400">{cluster.intent}</td>
                          <td className="py-2 pr-3 text-zinc-400">{cluster.member_count}</td>
                          <td className="py-2 pr-3 text-zinc-400">{Math.round(cluster.impressions)}</td>
                          <td className="py-2 pr-3 text-zinc-400">{cluster.average_position?.toFixed(1) ?? "—"}</td>
                          <td className="py-2 pr-3 text-zinc-200">{cluster.opportunity_score.toFixed(0)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>

            <div className={panel}>
              <h2 className={heading}>Content brief queue</h2>
              {briefs.length === 0 ? (
                <div className="mt-3"><Empty>No briefs queued. Run the content briefs routine after a keyword refresh.</Empty></div>
              ) : (
                <ul className="mt-3 flex flex-col gap-2">
                  {briefs.map((brief) => (
                    <li key={brief.id} className="flex flex-wrap items-center justify-between gap-2 rounded border border-zinc-800 bg-zinc-900 px-3 py-2">
                      <span className="text-sm text-zinc-200">
                        {brief.cluster_label}
                        <span className={`${chip} ml-2 border-zinc-700 bg-zinc-950 text-zinc-400`}>{brief.kind.replace("_", " ")}</span>
                      </span>
                      <span className="flex items-center gap-2">
                        <span className={muted}>priority {brief.priority_score.toFixed(0)}</span>
                        <span className={statusChip(brief.status)}>{brief.status}</span>
                      </span>
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
                <p className="mt-3 text-3xl font-semibold text-white">
                  {visibility[0]?.readiness_score.toFixed(0)}
                  <span className="ml-1 text-base font-normal text-zinc-500">/ 100</span>
                  <span className="ml-3 text-xs font-normal text-zinc-500">as of {visibility[0]?.captured_on}</span>
                </p>
              )}
            </div>
          </section>
        )}
      </div>
    </main>
  );
}
