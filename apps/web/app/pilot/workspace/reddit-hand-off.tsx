"use client";

import {useState} from "react";

import {handOffRedditDraft} from "./actions";

const messages: Record<string, string> = {
  subreddit_cooldown: "This site already handed a draft to that subreddit in the last 7 days. Posting more often is how accounts get banned.",
  daily_reddit_limit: "Three Reddit hand-offs today already. Leave the rest for tomorrow.",
  reddit_draft_not_approved: "Approve the draft first.",
};

/**
 * Copies the text, then opens Reddit in a new tab. The person reads it there
 * and presses Post themselves; nothing is sent to Reddit from here.
 */
export function RedditHandOff({draftId, kind}: {draftId: string; kind: string}) {
  const [state, setState] = useState<"idle" | "busy" | "done">("idle");
  const [error, setError] = useState<string | null>(null);

  async function go() {
    setState("busy");
    setError(null);
    // Opened before the await so the browser treats it as the click's own window.
    const opened = window.open("about:blank", "_blank");
    const result = await handOffRedditDraft(draftId);
    if ("error" in result) {
      opened?.close();
      setError(messages[result.error] ?? `Could not hand off: ${result.error}`);
      setState("idle");
      return;
    }
    try {
      await navigator.clipboard.writeText(result.copyText);
    } catch {
      // Clipboard can be refused; the text is still on the page to copy by hand.
    }
    if (opened) {
      opened.opener = null;
      opened.location.href = result.openUrl;
    } else {
      window.location.href = result.openUrl;
    }
    setState("done");
  }

  return (
    <div className="flex flex-col gap-1">
      <button
        type="button"
        onClick={go}
        disabled={state === "busy"}
        className="rounded-full border border-good-rule bg-good-soft px-3 py-1 text-[12px] font-medium text-good hover:bg-good-soft disabled:opacity-60"
      >
        {state === "busy" ? "Opening…" : kind === "reply" ? "Copy reply and open thread" : "Copy post and open Reddit"}
      </button>
      {state === "done" && (
        <p className="text-[12px] text-ink-soft">
          Copied. On Reddit, paste it {kind === "reply" ? "into a comment" : "into the body if it is not already filled in"}, read it once more, and press Post.
        </p>
      )}
      {error && <p className="text-[12px] text-stop">{error}</p>}
    </div>
  );
}
