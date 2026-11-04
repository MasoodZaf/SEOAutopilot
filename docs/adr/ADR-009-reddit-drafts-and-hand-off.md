# ADR-009 — Reddit: draft, approve, and let the person post

- **Status:** accepted; implemented and tested locally, not yet used on a real thread
- **Date:** 2026-11-04

## Context

The user asked for Reddit posting: posts that share our own content, and replies in threads where
a site answers the question. Answer engines (ChatGPT, Perplexity) cite Reddit threads heavily, so a
good reply is read by people and by the engines that quote them.

Reddit stopped accepting new Data API access requests on 2026-10-31 and is retiring the public API
(access for unregistered apps closing from January 2027, everything by March 2027). An app that
posts from a connected Reddit account is therefore not available to us, and would be the most
ban-prone design even if it were.

## Decision

No Reddit credential and no call to Reddit.

1. **Find.** The citation scan keeps the Reddit thread URLs an answer cited
   (`ai_citation_observation.thread_urls`). The Reddit tab lists those threads with the tracked
   questions they were cited for. Any other thread can be pasted by hand.
2. **Draft.** The workspace's own Claude or OpenAI key writes the reply or post from the question,
   the thread text the person pastes (we cannot read Reddit), and the crawl's summary of the page
   that answers it. The prompt asks for an answer that stands on its own and an honest mention of
   the site. A disclosure line is appended in code whenever the text does not already disclose.
3. **Approve.** A person with an editor role or above reads it and approves it. Any edit sends it
   back for approval.
4. **Hand off.** The person presses a button that copies the text and opens Reddit: the thread for
   a reply, or the subreddit's submit page with what fits prefilled for a post. They press Post on
   Reddit. Afterwards they can paste the link to record where it went.

Limits, enforced at hand-off: one per subreddit per site per 7 days, three per site per day.
Spend counts against the same monthly budget as drafting and chat.

## Consequences

- Nothing can post, vote or comment by itself, and there is no second account to post from.
- We cannot read thread content or subreddit rules; the person pastes what matters, and the tab
  tells them to read the rules.
- The submit-page prefill (`?type=TEXT&title=&text=`) is unverified on current Reddit. The text
  is always copied too, so a page that ignores the fields still works.
- API: additive `/v1/sites/{id}/reddit/threads`, `/v1/sites/{id}/reddit/drafts`,
  `/v1/reddit-drafts/{id}` and its `approve`, `hand-off`, `posted`, `dismiss` actions.
  Migration 0054. Rollback: redeploy; drop `reddit_draft` and `thread_urls`.
