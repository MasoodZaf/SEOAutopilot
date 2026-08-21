# Hostile 500-page crawl fixture

This fixture is generated in memory by
`services/crawler/src/hostile-fixture.ts`; it contains no customer content and makes no external
network requests.

The acceptance case includes 500 deterministic HTML pages plus:

- robots exclusions and excessive sitemap entries;
- loops, deep traversal, query traps, credential-bearing URLs, external origins, loopback and cloud
  metadata addresses;
- malformed and cross-origin canonical evidence;
- malformed JSON-LD and a `noindex` page;
- page text that attempts to instruct the crawler to change policy or deploy content.

Passing this fixture proves only deterministic local traversal and extraction behavior for the
tested build. It does not prove production crawl throughput, provider approval, search ranking, or
safe deployment.
