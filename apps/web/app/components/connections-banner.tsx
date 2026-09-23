import Link from "next/link";

import {apiJson} from "@/lib/server-api";

type Connections = {
  data: {site_name: string; type: string; status: string}[];
  meta: {needs_attention: number};
};

const SERVICE: Record<string, string> = {
  google_search_console: "Search Console",
  google_analytics: "Analytics",
  github_repository: "GitHub",
  dns_provider: "DNS",
};

/**
 * A dead connection, said on every signed-in page.
 *
 * On 2026-09-18 every Google connector in production had been dead for three
 * to five days. The only place that said so was the settings page, as a grey
 * status word, and a journal nobody reads. A connection that has stopped
 * feeding data makes every number downstream of it stale, so it is reported
 * where people actually are.
 *
 * Renders nothing on any failure: this is a notice, and a page must never
 * fail because the notice could not be drawn.
 */
export async function ConnectionsBanner() {
  let body: Connections;
  try {
    body = await apiJson<Connections>("/v1/connections");
  } catch {
    return null;
  }
  const broken = body.data.filter(
    (item) => item.status === "reauthorization_required" || item.status === "error",
  );
  if (broken.length === 0) return null;

  const names = broken
    .slice(0, 3)
    .map((item) => `${item.site_name} ${SERVICE[item.type] ?? item.type}`)
    .join(", ");
  const more = broken.length > 3 ? ` and ${broken.length - 3} more` : "";

  return (
    <aside aria-label="Connection problems" className="border-b border-stop-rule bg-stop-soft">
      <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-x-6 gap-y-2 px-6 py-2.5 text-[13px] text-ink">
        <p className="text-pretty">
          <strong className="font-semibold text-stop">
            {broken.length === 1
              ? "1 connection has stopped working."
              : `${broken.length} connections have stopped working.`}
          </strong>{" "}
          No new data is arriving from {names}
          {more}.
        </p>
        <Link
          href="/settings/connectors"
          className="font-medium text-stop underline decoration-stop-rule underline-offset-4 hover:decoration-stop"
        >
          Reconnect
        </Link>
      </div>
    </aside>
  );
}
