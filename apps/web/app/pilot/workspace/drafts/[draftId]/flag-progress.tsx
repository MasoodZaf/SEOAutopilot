"use client";

import {useEffect, useRef, useState} from "react";

/**
 * How many flags are resolved, counting boxes ticked since the last save.
 *
 * A tick only counts once Save is pressed, and nothing on the page used to say
 * so: a reviewer could tick every box, press Send for approval, and see
 * nothing happen. This sits beside Save and says what is left.
 */
export function FlagProgress({total, saved, firstOpenId}: {total: number; saved: number; firstOpenId: string | null}) {
  const anchor = useRef<HTMLSpanElement>(null);
  const [ticked, setTicked] = useState(0);

  useEffect(() => {
    const form = anchor.current?.closest("form");
    if (!form) return;
    const count = () =>
      setTicked(form.querySelectorAll<HTMLInputElement>('input[type="checkbox"][name^="resolve:"]:checked').length);
    count();
    form.addEventListener("change", count);
    return () => form.removeEventListener("change", count);
  }, []);

  if (total === 0) return <span ref={anchor} />;
  const open = total - saved - ticked;
  return (
    <span ref={anchor} role="status" className="text-[13px] text-pretty text-ink-soft">
      <span className="font-medium text-ink tabular">
        {saved + ticked} of {total}
      </span>{" "}
      flags resolved
      {ticked ? <> ({ticked} ticked, not saved yet: press Save)</> : null}
      {open > 0 ? (
        <>
          {". "}
          {firstOpenId ? (
            <a href={`#flag-${firstOpenId}`} className="font-medium text-accent underline underline-offset-2">
              {open} still open
            </a>
          ) : (
            <>{open} still open</>
          )}
          .
        </>
      ) : ticked ? null : "."}
    </span>
  );
}
