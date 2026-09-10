import Link from "next/link";

import {cn} from "@/lib/cn";

/*
 * The shared vocabulary of the control plane.
 *
 * Every page used to draw its own card, its own button, its own notice, in
 * whichever neutral ramp that file happened to start with. The result was not
 * ugly so much as unplaced: nothing told you that /settings and /pilot were the
 * same product. These are the pieces that do.
 *
 * Radii are small and shadows are absent on purpose. Structure here comes from
 * hairlines and space, the way it does on a printed instrument panel -- which
 * also means a lifted, filled, or coloured element genuinely means something
 * when one appears.
 */

export const tones = ["neutral", "accent", "good", "warn", "stop"] as const;
export type Tone = (typeof tones)[number];

const toneRing: Record<Tone, string> = {
 neutral: "border-rule bg-sunk text-ink-soft",
 accent: "border-accent-rule bg-accent-soft text-accent",
 good: "border-good-rule bg-good-soft text-good",
 warn: "border-warn-rule bg-warn-soft text-warn",
 stop: "border-stop-rule bg-stop-soft text-stop",
};

/** The small uppercase mono label that names a region. */
export function Eyebrow({children, className}: {children: React.ReactNode; className?: string}) {
 return <p className={cn("eyebrow", className)}>{children}</p>;
}

/** A measured value: mono, tabular, selectable. */
export function Data({children, className}: {children: React.ReactNode; className?: string}) {
 return (
    <span className={cn("font-mono text-[13px] tabular break-all text-ink", className)}>
      {children}
    </span>
  );
}

/** A surface. Not every group needs one -- reach for space first. */
export function Panel({
 children,
 className,
 as: Tag = "section",
  ...rest
}: {
 children: React.ReactNode;
 className?: string;
 as?: "section" | "div" | "article";
} & React.HTMLAttributes<HTMLElement>) {
 return (
    <Tag
      {...rest}
 className={cn("rounded-[4px] border border-rule bg-surface", className)}
    >
      {children}
    </Tag>
  );
}

export function PanelHead({
 title,
 id,
 eyebrow,
 description,
 aside,
}: {
 title: string;
 id?: string;
 eyebrow?: string;
 description?: React.ReactNode;
 aside?: React.ReactNode;
}) {
 return (
    <div className="flex items-start justify-between gap-4 border-b border-rule px-5 py-4">
      <div className="min-w-0">
        {eyebrow ? <Eyebrow className="mb-1.5">{eyebrow}</Eyebrow> : null}
        <h2 id={id} className="text-[15px] font-semibold tracking-tight text-ink">
          {title}
        </h2>
        {description ? (
          <div className="mt-1.5 max-w-prose text-pretty text-[13px] leading-6 text-ink-soft">
            {description}
          </div>
        ) : null}
      </div>
      {aside ? <div className="shrink-0">{aside}</div> : null}
    </div>
  );
}

/**
 * A notice.
 *
 * A left rule rather than a full box: these appear in runs, and four stacked
 * filled boxes read as four alarms rather than as one page with some state.
 */
export function Note({
 tone = "neutral",
 label,
 children,
 role,
}: {
 tone?: Tone;
 label?: string;
 children: React.ReactNode;
 role?: "alert" | "status";
}) {
 const bar: Record<Tone, string> = {
 neutral: "border-l-rule-strong bg-sunk",
 accent: "border-l-accent bg-accent-soft",
 good: "border-l-good bg-good-soft",
 warn: "border-l-warn bg-warn-soft",
 stop: "border-l-stop bg-stop-soft",
  };
 const ink: Record<Tone, string> = {
 neutral: "text-ink-soft",
 accent: "text-ink",
 good: "text-ink",
 warn: "text-ink",
 stop: "text-ink",
  };
 return (
    <div role={role} className={cn("border-l-2 px-4 py-3", bar[tone])}>
      {label ? <Eyebrow className="mb-1">{label}</Eyebrow> : null}
      <div className={cn("text-pretty text-[13px] leading-6", ink[tone])}>{children}</div>
    </div>
  );
}

export function Badge({tone = "neutral", children}: {tone?: Tone; children: React.ReactNode}) {
 return (
    <span
 className={cn(
        "inline-flex items-center rounded-[3px] border px-2 py-0.5 font-mono text-[11px] font-medium tracking-wide uppercase",
 toneRing[tone],
      )}
    >
      {children}
    </span>
  );
}

/* Controls are class strings rather than components: every one of these sits
 inside a server-action <form>, and a string keeps that plain. */

export const button = {
 primary:
    "inline-flex items-center justify-center gap-2 rounded-[4px] bg-accent px-4 py-2 text-[13px] font-medium text-accent-ink transition-colors hover:bg-accent-hover disabled:opacity-50",
 secondary:
    "inline-flex items-center justify-center gap-2 rounded-[4px] border border-rule-strong bg-surface px-3.5 py-2 text-[13px] font-medium text-ink transition-colors hover:bg-sunk disabled:opacity-50",
 quiet:
    "inline-flex items-center gap-1.5 text-[13px] font-medium text-accent underline decoration-accent-rule underline-offset-4 transition-colors hover:decoration-accent",
 danger:
    "inline-flex items-center gap-1.5 text-[13px] font-medium text-stop underline decoration-stop-rule underline-offset-4 transition-colors hover:decoration-stop",
} as const;

export const input =
  "w-full rounded-[4px] border border-rule-strong bg-surface px-3 py-2 text-[13px] text-ink placeholder:text-ink-faint";

export const inputMono = cn(input, "font-mono");

export function Field({
 label,
 hint,
 children,
}: {
 label: string;
 hint?: React.ReactNode;
 children: React.ReactNode;
}) {
 return (
    <label className="flex flex-col gap-1.5">
      <span className="text-[13px] font-medium text-ink">{label}</span>
      {children}
      {hint ? <span className="text-[12px] leading-5 text-ink-faint">{hint}</span> : null}
    </label>
  );
}

/**
 * A value the reader has to copy somewhere exactly -- a redirect URI, a TXT
 * record, a client id.
 *
 * Shown rather than described, in mono, with room to wrap: one character of
 * drift in any of these fails at a provider with a message that names nothing
 * the person can connect to what they typed.
 */
export function Copyable({label, value}: {label: string; value: string}) {
 return (
    <div className="rounded-[4px] border border-rule bg-sunk px-3 py-2.5">
      <Eyebrow>{label}</Eyebrow>
      <p className="mt-1.5 font-mono text-[12.5px] leading-5 break-all text-ink select-all">
        {value}
      </p>
    </div>
  );
}

/** The masthead every control-plane page carries. */
export function Masthead({
 section,
 children,
}: {
 section?: string;
 children?: React.ReactNode;
}) {
 return (
    <div className="border-b border-rule bg-surface">
      <div className="mx-auto flex max-w-5xl items-center justify-between gap-4 px-6 py-3">
        <div className="flex items-baseline gap-3">
          <Link
 href="/pilot"
 className="font-display text-[15px] font-semibold tracking-tight text-ink"
          >
            SEO Autopilot
          </Link>
          {section ? (
            <>
              <span aria-hidden="true" className="text-rule-strong">
                /
              </span>
              <span className="eyebrow">{section}</span>
            </>
          ) : null}
        </div>
        {children}
      </div>
    </div>
  );
}

export function Tabs({
 items,
 label,
}: {
 items: {href: string; label: string}[];
 label: string;
}) {
 return (
    <nav aria-label={label} className="border-b border-rule bg-surface">
      <ul className="mx-auto flex max-w-5xl gap-6 px-6">
        {items.map((item) => (
          <li key={item.href}>
            <Link
 href={item.href}
 className="-mb-px inline-block border-b-2 border-transparent py-2.5 text-[13px] font-medium text-ink-soft transition-colors hover:border-rule-strong hover:text-ink"
            >
              {item.label}
            </Link>
          </li>
        ))}
      </ul>
    </nav>
  );
}
