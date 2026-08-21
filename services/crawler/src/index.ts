import {runConsumer} from "./stream-consumer.js";

runConsumer().catch(error => {
  console.error(JSON.stringify({error: error instanceof Error ? error.message : "crawler_failed"}));
  process.exitCode = 1;
});
