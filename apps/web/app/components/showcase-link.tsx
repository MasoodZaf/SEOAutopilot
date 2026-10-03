"use client";

import {useSyncExternalStore} from "react";

import {SHOWCASE_FLAG, cameFromShowcase} from "./showcase.mjs";

/* Inlined at build time. Empty in development and in any deployment that is
   not the OryxenLabs one, so the link never appears there. */
const SHOWCASE_HOME = process.env.NEXT_PUBLIC_SHOWCASE_HOME_URL ?? "";

/** True when this tab's visit started on the showcase site, remembered for the rest of the visit. */
function readFromShowcase(): boolean {
  if (!SHOWCASE_HOME) return false;
  try {
    if (cameFromShowcase(document.referrer, SHOWCASE_HOME)) {
      sessionStorage.setItem(SHOWCASE_FLAG, "1");
    }
    return sessionStorage.getItem(SHOWCASE_FLAG) === "1";
  } catch {
    // Storage blocked: no link rather than a guess.
    return false;
  }
}

// Nothing to subscribe to: the answer is fixed for the life of the page.
const subscribe = () => () => {};

/**
 * "← OryxenLabs", for visitors who came from the showcase site's product card.
 *
 * Public pages only (landing, tutorial, sign-in). The control plane never
 * renders it, so someone working in the app does not see it even in a tab
 * that started on the showcase.
 */
export function ShowcaseLink({className = ""}: {className?: string}) {
  // The server never knows the referrer, so it renders no link; the browser decides.
  const show = useSyncExternalStore(subscribe, readFromShowcase, () => false);
  if (!show) return null;
  return (
    <a
      href={SHOWCASE_HOME}
      className={`inline-flex items-center gap-1 rounded-full px-3 py-1.5 text-[12px] text-ink-faint transition-colors hover:text-ink ${className}`}
    >
      <span aria-hidden="true">&larr;</span> OryxenLabs
    </a>
  );
}
