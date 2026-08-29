import React from "react";

type SerpPreviewProps = {
  title: string;
  url: string;
  description: string;
  isModified?: boolean;
};

export function SerpPreview({title, url, description, isModified = false}: SerpPreviewProps) {
  return (
    <div className={`rounded-lg border p-4 font-sans ${isModified ? "border-emerald-800/80 bg-emerald-950/20" : "border-zinc-800 bg-zinc-950"}`}>
      <div className="flex items-center gap-2">
        <div className="flex h-5 w-5 items-center justify-center rounded-full bg-zinc-800 text-[10px] text-zinc-300">
          <span aria-hidden="true">URL</span>
        </div>
        <div className="flex flex-col">
          <span className="text-xs text-zinc-400 font-mono leading-tight">{url}</span>
        </div>
      </div>
      <h3 className="mt-1 line-clamp-1 text-base font-medium text-sky-400">
        {title}
      </h3>
      <p className="mt-1 text-xs text-zinc-300 line-clamp-2 leading-relaxed">
        {description}
      </p>
      {isModified && (
        <span className="mt-2 inline-block rounded bg-emerald-900/60 border border-emerald-700 px-1.5 py-0.5 text-[10px] font-medium text-emerald-300">
          Proposed preview
        </span>
      )}
    </div>
  );
}
