import assert from "node:assert/strict";
import test from "node:test";
import {claimMessage,parseClaimReply,parseReadReply} from "./stream-consumer.js";

const message=["1-0",["type","crawl.requested","tenant_id","tenant","aggregate_id","crawl"]];
test("parses new stream messages",()=>assert.deepEqual(parseReadReply([["stream",[message]]]),[{id:"1-0",fields:{type:"crawl.requested",tenant_id:"tenant",aggregate_id:"crawl"}}]));
test("parses reclaimed pending messages",()=>assert.deepEqual(parseClaimReply(["0-0",[message],[]]),[{id:"1-0",fields:{type:"crawl.requested",tenant_id:"tenant",aggregate_id:"crawl"}}]));

test("acknowledges a permanently unclaimable crawl event so it cannot starve newer work",async()=>{
  let acknowledgements=0;
  const result=await claimMessage(
    {id:"1-0",fields:{type:"crawl.requested",tenant_id:"tenant",aggregate_id:"missing"}},
    async()=>({kind:"discard" as const}),
    async()=>{acknowledgements+=1},
  );
  assert.equal(result,null);
  assert.equal(acknowledgements,1);
});

test("keeps a temporarily unclaimable crawl event pending for lease recovery",async()=>{
  let acknowledgements=0;
  const result=await claimMessage(
    {id:"1-0",fields:{type:"crawl.requested",tenant_id:"tenant",aggregate_id:"running"}},
    async()=>({kind:"retry" as const}),
    async()=>{acknowledgements+=1},
  );
  assert.equal(result,null);
  assert.equal(acknowledgements,0);
});

test("does not acknowledge a valid claimed crawl before terminal persistence",async()=>{
  let acknowledgements=0;
  const claimed={id:"crawl"};
  const result=await claimMessage(
    {id:"1-0",fields:{type:"crawl.requested",tenant_id:"tenant",aggregate_id:"crawl"}},
    async()=>({kind:"claimed" as const,crawl:claimed}),
    async()=>{acknowledgements+=1},
  );
  assert.equal(result,claimed);
  assert.equal(acknowledgements,0);
});
