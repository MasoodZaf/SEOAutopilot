import {createHash} from "node:crypto";

import {Pool, type PoolClient} from "pg";

import type {PageObservation} from "./types.js";
import type {CrawlResult} from "./crawl-engine.js";

export type ClaimedCrawl = {
  id: string;
  tenantId: string;
  siteId: string;
  origin: string;
  maxPages: number;
  maxDepth: number;
  renderPolicy: "auto" | "always" | "never";
  attempts: number;
};

export type CrawlClaimResult =
  | {kind: "claimed"; crawl: ClaimedCrawl}
  | {kind: "retry"}
  | {kind: "discard"};

async function tenantTransaction<T>(pool: Pool, tenantId: string, work: (client: PoolClient) => Promise<T>): Promise<T> {
  const client = await pool.connect();
  try {
    await client.query("BEGIN");
    await client.query("SELECT set_config('app.tenant_id',$1,true)", [tenantId]);
    const result = await work(client);
    await client.query("COMMIT");
    return result;
  } catch (error) {
    await client.query("ROLLBACK");
    throw error;
  } finally { client.release(); }
}

export async function claimCrawl(pool: Pool, tenantId: string, crawlId: string): Promise<CrawlClaimResult> {
  return tenantTransaction(pool, tenantId, async client => {
    const result = await client.query<{
      id:string; tenant_id:string; site_id:string; config_snapshot:{max_pages?:number;max_depth?:number;render_policy?:string}; attempts:number; canonical_origin:string;
    }>(`UPDATE crawl_job c SET status='running',lease_until=now()+interval '2 minutes',last_heartbeat_at=now(),started_at=coalesce(started_at,now()),attempts=attempts+1,error_code=null
       FROM site s WHERE c.id=$1 AND c.tenant_id=$2 AND s.id=c.site_id AND s.tenant_id=c.tenant_id
       AND c.attempts<3 AND (c.status='queued' OR (c.status='running' AND c.lease_until<now()))
       RETURNING c.id,c.tenant_id,c.site_id,c.config_snapshot,c.attempts,s.canonical_origin`, [crawlId, tenantId]);
    const row = result.rows[0];
    if (!row) {
      const existing = await client.query<{status:string;attempts:number}>(
        "SELECT status,attempts FROM crawl_job WHERE id=$1 AND tenant_id=$2",
        [crawlId, tenantId],
      );
      const current = existing.rows[0];
      if (!current || ["completed", "partial", "failed", "cancelled"].includes(current.status)) {
        return {kind:"discard"};
      }
      return current.attempts < 3 ? {kind:"retry"} : {kind:"discard"};
    }
    const requestedPolicy = row.config_snapshot.render_policy;
    const renderPolicy = requestedPolicy === "always" || requestedPolicy === "never" ? requestedPolicy : "auto";
    return {kind:"claimed",crawl:{id:row.id,tenantId:row.tenant_id,siteId:row.site_id,origin:row.canonical_origin,maxPages:Math.min(row.config_snapshot.max_pages ?? 500,10_000),maxDepth:Math.min(Math.max(row.config_snapshot.max_depth ?? 10,1),50),renderPolicy,attempts:row.attempts}};
  });
}

export async function heartbeatCrawl(pool: Pool, crawl: ClaimedCrawl): Promise<void> {
  await tenantTransaction(pool,crawl.tenantId,async client=>{
    const result=await client.query(
      "UPDATE crawl_job SET lease_until=now()+interval '2 minutes',last_heartbeat_at=now() WHERE id=$1 AND tenant_id=$2 AND status='running'",
      [crawl.id,crawl.tenantId],
    );
    if(result.rowCount!==1)throw new Error("crawl_lease_lost");
  });
}

export async function persistObservation(pool: Pool, crawl: ClaimedCrawl, observation: PageObservation): Promise<void> {
  await tenantTransaction(pool, crawl.tenantId, async client => {
    const urlHash = createHash("sha256").update(observation.normalizedUrl).digest("hex");
    const page = await client.query<{id:string}>(`INSERT INTO page(tenant_id,site_id,normalized_url,url_hash) VALUES($1,$2,$3,$4)
      ON CONFLICT(site_id,url_hash) DO UPDATE SET normalized_url=excluded.normalized_url,last_seen_at=now(),lifecycle_status='active' RETURNING id`,
      [crawl.tenantId,crawl.siteId,observation.normalizedUrl,urlHash]);
    const pageId = page.rows[0]?.id;
    if (!pageId) throw new Error("page_upsert_failed");
    await client.query(`INSERT INTO page_observation(tenant_id,page_id,crawl_job_id,http_status,final_url,title,meta_description,h1_json,word_count,content_hash,rendered,canonical_url,robots_directives,structured_data_json,link_count_total,links_truncated)
      VALUES($1,$2,$3,$4,$5,$6,$7,$8::jsonb,$9,$10,$11,$12,$13,$14::jsonb,$15,$16)
      ON CONFLICT(page_id,crawl_job_id) DO UPDATE SET observed_at=now(),http_status=excluded.http_status,final_url=excluded.final_url,title=excluded.title,meta_description=excluded.meta_description,h1_json=excluded.h1_json,word_count=excluded.word_count,content_hash=excluded.content_hash,rendered=excluded.rendered,canonical_url=excluded.canonical_url,robots_directives=excluded.robots_directives,structured_data_json=excluded.structured_data_json,link_count_total=excluded.link_count_total,links_truncated=excluded.links_truncated`,[crawl.tenantId,pageId,crawl.id,observation.status,observation.finalUrl,observation.title,observation.metaDescription,JSON.stringify(observation.h1),observation.wordCount,observation.contentHash,observation.rendered,observation.canonicalUrl,observation.robotsDirectives,JSON.stringify(observation.structuredData),observation.linkCountTotal,observation.linksTruncated]);
    await client.query("DELETE FROM link_edge WHERE tenant_id=$1 AND crawl_job_id=$2 AND source_page_id=$3",[crawl.tenantId,crawl.id,pageId]);
    const uniqueLinks = new Map(observation.links.map(link => [`${link.targetUrl}\0${link.anchorText}\0${link.relValues.join(" ")}`, link]));
    for (const link of uniqueLinks.values()) {
      const targetHash = createHash("sha256").update(link.targetUrl).digest("hex");
      await client.query(`INSERT INTO link_edge(tenant_id,crawl_job_id,source_page_id,target_url,target_url_hash,anchor_text,rel_values)
        VALUES($1,$2,$3,$4,$5,$6,$7)`,[crawl.tenantId,crawl.id,pageId,link.targetUrl,targetHash,link.anchorText,link.relValues]);
    }
    await client.query("UPDATE crawl_job SET lease_until=now()+interval '2 minutes',last_heartbeat_at=now() WHERE id=$1 AND tenant_id=$2",[crawl.id,crawl.tenantId]);
  });
}

export async function completeCrawl(pool: Pool, crawl: ClaimedCrawl, result: CrawlResult): Promise<void> {
  await tenantTransaction(pool,crawl.tenantId,async client=>{
    const partial=result.observations.length>=crawl.maxPages||result.discoveryTruncated||result.fetchErrors>0;
    const finalStatus=partial?"partial":"completed";
    const summary={pages_observed:result.observations.length,skipped_by_robots:result.skippedByRobots,fetch_errors:result.fetchErrors,discovery_truncated:result.discoveryTruncated,truncated_link_pages:result.observations.filter(item=>item.linksTruncated).length,max_pages:crawl.maxPages,max_depth:crawl.maxDepth};
    await client.query("UPDATE crawl_job SET status=$3,finished_at=now(),lease_until=null,error_code=null,result_summary=$4::jsonb WHERE id=$1 AND tenant_id=$2",[crawl.id,crawl.tenantId,finalStatus,JSON.stringify(summary)]);
    await client.query(`INSERT INTO outbox_event(tenant_id,event_type,event_version,aggregate_type,aggregate_id,payload)
      VALUES($1,'crawl.completed',1,'crawl_job',$2,$3::jsonb)`,[crawl.tenantId,crawl.id,JSON.stringify({crawl_id:crawl.id,site_id:crawl.siteId,status:finalStatus,summary})]);
  });
}

export async function releaseFailedCrawl(pool: Pool, crawl: ClaimedCrawl, code: string): Promise<void> {
  await tenantTransaction(pool,crawl.tenantId,async client=>{await client.query("UPDATE crawl_job SET status=CASE WHEN attempts>=3 THEN 'failed' ELSE 'queued' END,lease_until=null,error_code=$3,finished_at=CASE WHEN attempts>=3 THEN now() ELSE null END WHERE id=$1 AND tenant_id=$2",[crawl.id,crawl.tenantId,code.slice(0,80)])});
}
