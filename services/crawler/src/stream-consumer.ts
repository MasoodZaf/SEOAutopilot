import {hostname} from "node:os";

import {Pool} from "pg";
import {createClient} from "redis";

import {crawlSite, newProgress} from "./crawl-engine.js";
import {claimCrawl,completeCrawl,heartbeatCrawl,persistObservation,releaseFailedCrawl} from "./database.js";
import {createAdaptiveFetcher} from "./adaptive-fetcher.js";

const STREAM="seo-autopilot:events";const GROUP="crawler";
type StreamMessage={id:string;fields:Record<string,string>};

export async function claimMessage<T>(
  message: StreamMessage,
  claim: (tenantId: string, crawlId: string) => Promise<{kind:"claimed";crawl:T}|{kind:"retry"}|{kind:"discard"}>,
  acknowledge: () => Promise<void>,
): Promise<T | null> {
  const crawlId=message.fields.aggregate_id;const tenantId=message.fields.tenant_id;
  if(!crawlId||!tenantId){await acknowledge();return null}
  const result=await claim(tenantId,crawlId);
  if(result.kind==="discard")await acknowledge();
  return result.kind==="claimed"?result.crawl:null;
}

function fields(values:unknown):Record<string,string>{const result:Record<string,string>={};if(!Array.isArray(values))return result;for(let i=0;i<values.length;i+=2){if(typeof values[i]==="string"&&typeof values[i+1]==="string")result[values[i]]=values[i+1]}return result}
export function parseReadReply(reply:unknown):StreamMessage[]{if(!Array.isArray(reply)||!Array.isArray(reply[0]))return[];const messages=(reply[0] as unknown[])[1];if(!Array.isArray(messages))return[];return messages.flatMap(item=>Array.isArray(item)&&typeof item[0]==="string"?[{id:item[0],fields:fields(item[1])}]:[])}
export function parseClaimReply(reply:unknown):StreamMessage[]{if(!Array.isArray(reply)||!Array.isArray(reply[1]))return[];return (reply[1] as unknown[]).flatMap(item=>Array.isArray(item)&&typeof item[0]==="string"?[{id:item[0],fields:fields(item[1])}]:[])}

/** Every service reads one DATABASE_URL. The Python services need the
 * `+asyncpg` driver suffix that node-postgres cannot parse, so stripping it
 * here lets all four share a single credential from env_file instead of
 * keeping a second copy of the same secret under a different key. */
export function normalizeDatabaseUrl(value:string|undefined):string|undefined{
  return value?.replace("postgresql+asyncpg://","postgresql://");
}

export async function runConsumer():Promise<void>{
  const redis=createClient({url:process.env.REDIS_URL});await redis.connect();
  const pool=new Pool({connectionString:normalizeDatabaseUrl(process.env.DATABASE_URL),max:4});const consumer=`${hostname()}-${process.pid}`;
  try{await redis.sendCommand(["XGROUP","CREATE",STREAM,GROUP,"0","MKSTREAM"])}catch(error){if(!(error instanceof Error)||!error.message.includes("BUSYGROUP"))throw error}
  for(;;){
    const claimed=parseClaimReply(await redis.sendCommand(["XAUTOCLAIM",STREAM,GROUP,consumer,"60000","0-0","COUNT","1"]));
    const messages=claimed.length?claimed:parseReadReply(await redis.sendCommand(["XREADGROUP","GROUP",GROUP,consumer,"BLOCK","5000","COUNT","1","STREAMS",STREAM,">"]));
    for(const message of messages){
      if(message.fields.type!=="crawl.requested"){await redis.sendCommand(["XACK",STREAM,GROUP,message.id]);continue}
      const crawl=await claimMessage(
        message,
        (tenantId,crawlId)=>claimCrawl(pool,tenantId,crawlId),
        async()=>{await redis.sendCommand(["XACK",STREAM,GROUP,message.id])},
      );
      if(!crawl)continue;
      const host=new URL(crawl.origin).hostname.toLowerCase();const managedFetcher=createAdaptiveFetcher(new Set([host]),crawl.renderPolicy);
      let heartbeatError:Error|null=null;let heartbeatBusy=false;
      // Shared with crawlSite, which updates it as it goes; the heartbeat
      // writes a snapshot so the dashboard can show a live bar.
      const progress=newProgress(crawl.maxPages);
      const heartbeat=async()=>{
        if(heartbeatBusy||heartbeatError)return;
        heartbeatBusy=true;
        try{
          await heartbeatCrawl(pool,crawl,progress);
          await redis.sendCommand(["XCLAIM",STREAM,GROUP,consumer,"0",message.id,"JUSTID"]);
        }catch(error){heartbeatError=error instanceof Error?error:new Error("crawl_heartbeat_failed")}
        finally{heartbeatBusy=false}
      };
      // Every 10s rather than 30s: it is now what a person watching the bar
      // sees move, as well as the lease.
      const heartbeatTimer=setInterval(()=>{void heartbeat()},10_000);
      void heartbeat();
      try{
        const result=await crawlSite(crawl.origin,crawl.maxPages,managedFetcher.fetch,crawl.maxDepth,progress);
        if(heartbeatError)throw heartbeatError;
        progress.phase="saving";
        for(const observation of result.observations){await persistObservation(pool,crawl,observation);progress.saved+=1}
        await completeCrawl(pool,crawl,result);
        await redis.sendCommand(["XACK",STREAM,GROUP,message.id]);
      }catch(error){await releaseFailedCrawl(pool,crawl,error instanceof Error?error.message:"crawl_failed")}
      finally{clearInterval(heartbeatTimer);await managedFetcher.close()}
    }
  }
}
