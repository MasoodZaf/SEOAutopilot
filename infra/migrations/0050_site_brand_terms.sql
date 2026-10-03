-- The names a site's own brand is searched by, for the zero-click scorecard.
--
-- The scorecard splits Search Console demand into branded (people asking for
-- the site by name) and non-branded. Search terms stay sealed in
-- search_query; the API opens them only to test them against these terms and
-- returns weekly totals, never a term.
--
-- Empty means "derive them": the site's name and its bare domain label, the
-- same names the citation scan recognises the site by. A person can replace
-- them when the brand is searched another way.
--
-- Additive. Rollback: drop the column.
BEGIN;

ALTER TABLE site
  ADD COLUMN brand_terms jsonb NOT NULL DEFAULT '[]'::jsonb
    CHECK(jsonb_typeof(brand_terms) = 'array' AND jsonb_array_length(brand_terms) <= 10);

COMMIT;
