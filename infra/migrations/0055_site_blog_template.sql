-- Blog posts on plain-HTML sites: the page whose layout a post reuses.
--
-- A site made of hand-written HTML files serves no Markdown, so a post there
-- is published as a copy of one of the site's own pages with only its <main>
-- replaced. This names that page by URL path. Null keeps the existing
-- behaviour: a Markdown file for a static-site generator.
--
-- Additive. Rollback: alter table site drop column blog_template_path.
BEGIN;

ALTER TABLE site
  ADD COLUMN blog_template_path text CHECK(
    blog_template_path IS NULL
    OR (
      char_length(blog_template_path) <= 301
      AND blog_template_path ~ '^/[A-Za-z0-9._~/-]*$'
      AND blog_template_path !~ '(^|/)\.\.(/|$)'
    )
  );

COMMIT;
