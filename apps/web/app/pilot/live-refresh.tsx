"use client";

import {useRouter} from "next/navigation";
import {useEffect} from "react";

/**
 * Re-render the page every few seconds while something is still moving.
 *
 * Rendered only while a crawl is queued or running, so a finished page does
 * no polling at all: the server component that owns the data decides whether
 * this exists. Paused while the tab is hidden.
 */
export function LiveRefresh({seconds = 5}: {seconds?: number}) {
  const router = useRouter();
  useEffect(() => {
    const timer = setInterval(() => {
      if (document.visibilityState === "visible") router.refresh();
    }, seconds * 1000);
    return () => clearInterval(timer);
  }, [router, seconds]);
  return null;
}
