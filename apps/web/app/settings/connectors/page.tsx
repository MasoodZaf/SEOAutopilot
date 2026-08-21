import Link from "next/link";

import type {Site} from "@/app/pilot/model";
import {apiJson} from "@/lib/server-api";

type PageProps = {searchParams: Promise<{google?: string}>};

export default async function ConnectorsPage({searchParams}: PageProps) {
  const query = await searchParams;
  const sites = await apiJson<{data: Site[]}>("/v1/sites");
  const site = sites.data.find((item) => item.normalized_host === "codearc.net");
  const connectors = site
    ? await apiJson<{data: {id: string; status: string; external_account_ref: string | null}[]}>(`/v1/sites/${site.id}/connectors`)
    : {data: []};
  const connector = connectors.data[0];

  return (
    <main className="min-h-dvh bg-slate-50">
      <div className="mx-auto max-w-3xl px-6 py-12">
        <p className="text-sm font-semibold text-emerald-700">SEO Autopilot</p>
        <h1 className="mt-2 text-balance text-3xl font-semibold">Connector status</h1>
        {query.google === "connected" ? <p role="status" className="mt-6 rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-900">Google Search Console authorization completed.</p> : null}
        <section className="mt-6 rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
          <h2 className="text-balance text-xl font-semibold">Google Search Console</h2>
          <dl className="mt-5 grid gap-3 text-sm sm:grid-cols-[9rem_1fr]">
            <dt className="text-slate-500">Status</dt><dd className="font-medium capitalize">{connector?.status.replaceAll("_", " ") ?? "Not connected"}</dd>
            <dt className="text-slate-500">Property</dt><dd className="break-all font-mono">{connector?.external_account_ref ?? "—"}</dd>
          </dl>
          <Link href="/pilot" className="mt-6 inline-flex rounded-lg border border-slate-300 bg-white px-4 py-2.5 text-sm font-semibold text-slate-800 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-600">Return to pilot</Link>
        </section>
      </div>
    </main>
  );
}
