import {randomUUID} from "node:crypto";
import Link from "next/link";
import {notFound} from "next/navigation";

import {ApiError, apiJson} from "@/lib/server-api";

import {submitCalibrationReview} from "../../actions";
import type {CalibrationItem} from "../../model";

type PageProps = {
  params: Promise<{itemId: string}>;
  searchParams: Promise<{error?: string}>;
};

const errorMessages: Record<string, string> = {
  idempotency_key_reused: "This form was already used with different values. Reload and try again.",
  calibration_item_not_found: "This review item is no longer available.",
};

export default async function CalibrationReviewPage({params, searchParams}: PageProps) {
  const {itemId} = await params;
  const query = await searchParams;
  let item: CalibrationItem;
  try {
    item = (await apiJson<{data: CalibrationItem}>(`/v1/calibration-items/${itemId}`)).data;
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) notFound();
    throw error;
  }
  const {opportunity, page, finding, observation} = item.evidence_snapshot;
  const error = query.error ? (errorMessages[query.error] ?? "The review could not be saved.") : null;

  return (
    <main className="min-h-dvh bg-slate-50">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-4xl items-center justify-between px-6 py-4">
          <div><Link href="/pilot" className="text-sm font-semibold text-emerald-700">SEO Autopilot</Link><p className="text-sm text-slate-500">CodeArc accuracy calibration</p></div>
          <span className="rounded-full border border-amber-300 bg-amber-50 px-3 py-1 text-sm font-medium text-amber-900">Observe mode</span>
        </div>
      </header>
      <div className="mx-auto max-w-4xl px-6 py-10">
        <p className="text-sm font-semibold text-emerald-700">Review item <span className="tabular-nums">{item.ordinal}</span></p>
        <h1 className="mt-2 text-balance text-3xl font-semibold text-slate-950">{opportunity.title}</h1>
        <p className="mt-3 text-pretty text-sm leading-6 text-slate-600">Label the frozen observation below. This records calibration evidence only and cannot approve or modify the website.</p>

        {error ? <p role="alert" className="mt-6 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-900">{error}</p> : null}

        <section className="mt-8 rounded-xl border border-slate-200 bg-white p-6 shadow-sm" aria-labelledby="evidence-heading">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div><h2 id="evidence-heading" className="text-balance text-xl font-semibold text-slate-950">Frozen crawl evidence</h2><a href={page.url} target="_blank" rel="noreferrer" className="mt-2 block break-all text-sm font-medium text-emerald-700 underline-offset-4 hover:underline">{page.url}</a></div>
            <span className="rounded-full border border-slate-300 px-3 py-1 text-sm font-medium capitalize text-slate-700">{finding.severity} severity</span>
          </div>
          <dl className="mt-6 grid gap-5 border-t border-slate-200 pt-5 text-sm sm:grid-cols-2">
            <div><dt className="text-slate-500">Rule</dt><dd className="mt-1 font-mono text-slate-900">{finding.rule_key}</dd></div>
            <div><dt className="text-slate-500">Observed</dt><dd className="mt-1 tabular-nums text-slate-900">{new Date(observation.observed_at).toLocaleString()}</dd></div>
            <div><dt className="text-slate-500">HTTP status</dt><dd className="mt-1 tabular-nums text-slate-900">{observation.http_status ?? "Unavailable"}</dd></div>
            <div><dt className="text-slate-500">Rendering path</dt><dd className="mt-1 text-slate-900">{observation.rendered ? "Browser rendered" : "HTTP response"}</dd></div>
            <div><dt className="text-slate-500">Stored H1 values</dt><dd className="mt-1 text-pretty text-slate-900">{observation.h1.length ? observation.h1.join(" · ") : "None observed"}</dd></div>
            <div><dt className="text-slate-500">Visible word count</dt><dd className="mt-1 tabular-nums text-slate-900">{observation.word_count}</dd></div>
            <div className="sm:col-span-2"><dt className="text-slate-500">Stored title</dt><dd className="mt-1 text-pretty text-slate-900">{observation.title ?? "None observed"}</dd></div>
            <div className="sm:col-span-2"><dt className="text-slate-500">Stored meta description</dt><dd className="mt-1 text-pretty text-slate-900">{observation.meta_description ?? "None observed"}</dd></div>
          </dl>
        </section>

        <section className="mt-8 rounded-xl border border-slate-200 bg-white p-6 shadow-sm" aria-labelledby="review-heading">
          <h2 id="review-heading" className="text-balance text-xl font-semibold text-slate-950">Your assessment</h2>
          {item.current_review ? <p className="mt-2 text-pretty text-sm text-slate-600">Submitting again creates a new append-only review; the latest one becomes your current assessment.</p> : null}
          <form action={submitCalibrationReview} className="mt-6 grid gap-5">
            <input type="hidden" name="item_id" value={item.id} />
            <input type="hidden" name="idempotency_key" value={`review-${randomUUID()}`} />
            <label className="grid gap-2 text-sm font-medium text-slate-800">Accuracy
              <select name="accuracy_label" required defaultValue={item.current_review?.accuracy_label ?? ""} className="rounded-lg border border-slate-300 bg-white px-3 py-2.5 text-slate-950 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-emerald-700">
                <option value="" disabled>Select accuracy</option><option value="true_positive">True positive</option><option value="false_positive">False positive</option><option value="uncertain">Uncertain</option>
              </select>
            </label>
            <label className="grid gap-2 text-sm font-medium text-slate-800">Actionability
              <select name="actionability" required defaultValue={item.current_review?.actionability ?? ""} className="rounded-lg border border-slate-300 bg-white px-3 py-2.5 text-slate-950 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-emerald-700">
                <option value="" disabled>Select actionability</option><option value="accept">Accept as useful</option><option value="edit">Useful after editing</option><option value="dismiss">Dismiss</option><option value="defer">Defer</option>
              </select>
            </label>
            <label className="grid gap-2 text-sm font-medium text-slate-800">Severity fit
              <select name="severity_fit" required defaultValue={item.current_review?.severity_fit ?? ""} className="rounded-lg border border-slate-300 bg-white px-3 py-2.5 text-slate-950 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-emerald-700">
                <option value="" disabled>Select severity fit</option><option value="appropriate">Appropriate</option><option value="overstated">Overstated</option><option value="understated">Understated</option><option value="uncertain">Uncertain</option>
              </select>
            </label>
            <label className="grid gap-2 text-sm font-medium text-slate-800">Notes <span className="font-normal text-slate-500">Optional, maximum 1,000 characters</span>
              <textarea name="notes" maxLength={1000} rows={4} defaultValue={item.current_review?.notes ?? ""} className="resize-y rounded-lg border border-slate-300 bg-white px-3 py-2.5 text-slate-950 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-emerald-700" />
            </label>
            <div className="flex flex-wrap gap-3"><button className="rounded-lg bg-emerald-700 px-4 py-2.5 text-sm font-semibold text-white shadow-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-emerald-700">Record review</button><Link href="/pilot" className="rounded-lg border border-slate-300 bg-white px-4 py-2.5 text-sm font-semibold text-slate-800 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-600">Back to pilot</Link></div>
          </form>
        </section>
      </div>
    </main>
  );
}
