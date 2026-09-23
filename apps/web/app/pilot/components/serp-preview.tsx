import React from "react";

type SerpPreviewProps = {
  title: string;
  url: string;
  description: string;
  isModified?: boolean;
};

/**
 * How the change would read on a results page. Drawn in the product's own
 * palette -- a copy of Google's blue would be the one borrowed colour on the
 * page and would claim a fidelity this preview does not have.
 */
export function SerpPreview({title, url, description, isModified = false}: SerpPreviewProps) {
  return (
    <div className="rounded-xl border border-rule bg-paper p-4 font-sans">
      <div className="flex items-center justify-between gap-3">
        <span className="min-w-0 truncate font-mono text-[11px] text-ink-faint">{url}</span>
        {isModified && (
          <span className="shrink-0 rounded-full border border-accent-rule bg-accent-soft px-2 py-0.5 font-mono text-[10px] tracking-wider text-accent uppercase">
            Proposed
          </span>
        )}
      </div>
      <h3 className="mt-2 line-clamp-1 text-[15px] font-medium text-accent">{title}</h3>
      <p className="mt-1 line-clamp-2 text-[12px] leading-5 text-ink-soft">{description}</p>
    </div>
  );
}
