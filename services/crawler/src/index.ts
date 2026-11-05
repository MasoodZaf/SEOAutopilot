import {runConsumer} from "./stream-consumer.js";

// Each slot is its own consumer in the group, with its own Redis connection
// (XREADGROUP BLOCK holds one). A slot that fails ends the process, which the
// restart policy brings back; leases return whatever it held.
const slots = Math.min(Math.max(Number.parseInt(process.env.CRAWLER_CONCURRENCY ?? "3", 10) || 1, 1), 8);

Promise.all(Array.from({length: slots}, (_, slot) => runConsumer(slot))).catch(error => {
  console.error(JSON.stringify({error: error instanceof Error ? error.message : "crawler_failed"}));
  process.exit(1);
});
