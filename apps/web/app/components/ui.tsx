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
 * Structure comes from hairlines and space, not shadows, so a filled or
 * coloured element genuinely means something when one appears. Surfaces are
 * softly rounded; controls are pills. The one loud colour is the signal lime,
 * and it is spent on the primary action and on "live" -- nowhere else.
 */

export const tones = ["neutral", "accent", "good", "warn", "stop"] as const;
export type Tone = (typeof tones)[number];

const toneRing: Record<Tone, string> = {
 neutral: "border-rule-strong bg-transparent text-ink-soft",
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
 className={cn("rounded-2xl border border-rule bg-surface", className)}
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
    <div className="flex items-start justify-between gap-4 border-b border-rule px-6 py-5">
      <div className="min-w-0">
        {eyebrow ? <Eyebrow className="mb-1.5">{eyebrow}</Eyebrow> : null}
        <h2 id={id} className="font-display text-[17px] font-medium tracking-tight text-balance text-ink">
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
    <div role={role} className={cn("rounded-r-xl border-l-2 px-4 py-3", bar[tone])}>
      {label ? <Eyebrow className="mb-1">{label}</Eyebrow> : null}
      <div className={cn("text-pretty text-[13px] leading-6", ink[tone])}>{children}</div>
    </div>
  );
}

const toneDot: Record<Tone, string> = {
 neutral: "bg-ink-faint",
 accent: "bg-accent",
 good: "bg-good",
 warn: "bg-warn",
 stop: "bg-stop",
};

/** A status: a dot and a word, in a pill. */
export function Badge({tone = "neutral", children}: {tone?: Tone; children: React.ReactNode}) {
 return (
    <span
 className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 font-mono text-[11px] font-medium tracking-wider whitespace-nowrap uppercase",
 toneRing[tone],
      )}
    >
      <span aria-hidden="true" className={cn("size-1.5 rounded-full", toneDot[tone])} />
      {children}
    </span>
  );
}

/**
 * The mark. An aperture: a ring with one quadrant open and a point at its
 * centre -- a lens that is looking, and a loop that is not closed until a
 * person closes it.
 */
export function Glyph({className}: {className?: string}) {
 return (
    <svg viewBox="0 0 24 24" aria-hidden="true" className={cn("size-5", className)}>
      <path
 d="M12 3a9 9 0 1 0 9 9"
 fill="none"
 stroke="currentColor"
 strokeWidth="2"
 strokeLinecap="round"
      />
      <circle cx="12" cy="12" r="3" className="fill-accent" />
      <circle cx="19.5" cy="4.5" r="1.5" className="fill-accent" />
    </svg>
  );
}

/** A measured quantity at reading distance: a label over a large figure. */
export function Metric({
 label,
 value,
 unit,
 hint,
 className,
}: {
 label: string;
 value: React.ReactNode;
 unit?: string;
 hint?: React.ReactNode;
 className?: string;
}) {
 return (
    <div className={cn("flex min-w-0 flex-col gap-2", className)}>
      <dt className="eyebrow">{label}</dt>
      <dd className="flex items-baseline gap-1.5">
        <span className="figure text-[34px] text-ink">{value}</span>
        {unit ? <span className="font-mono text-[12px] text-ink-faint">{unit}</span> : null}
      </dd>
      {hint ? <dd className="text-[12px] leading-5 text-pretty text-ink-faint">{hint}</dd> : null}
    </div>
  );
}

/** A thin horizontal gauge. `value` is 0..1; it is clamped, never trusted. */
export function Meter({
 value,
 label,
 tone = "accent",
 className,
}: {
 value: number;
 label: string;
 tone?: Tone;
 className?: string;
}) {
 const pct = Math.round(Math.min(1, Math.max(0, Number.isFinite(value) ? value : 0)) * 100);
 const fill: Record<Tone, string> = {
 neutral: "bg-ink-faint",
 accent: "bg-accent",
 good: "bg-good",
 warn: "bg-warn",
 stop: "bg-stop",
  };
 return (
    <div
 role="meter"
 aria-label={label}
 aria-valuemin={0}
 aria-valuemax={100}
 aria-valuenow={pct}
 className={cn("h-1 w-full overflow-hidden rounded-full bg-rule", className)}
    >
      <div className={cn("h-full rounded-full", fill[tone])} style={{width: `${pct}%`}} />
    </div>
  );
}

/* Controls are class strings rather than components: every one of these sits
 inside a server-action <form>, and a string keeps that plain. */

export const button = {
 primary:
    "inline-flex items-center justify-center gap-2 rounded-full bg-signal px-5 py-2 text-[13px] font-semibold text-signal-ink transition-colors hover:bg-signal-hover disabled:opacity-50",
 secondary:
    "inline-flex items-center justify-center gap-2 rounded-full border border-rule-strong bg-transparent px-4 py-2 text-[13px] font-medium text-ink transition-colors hover:border-ink-faint hover:bg-sunk disabled:opacity-50",
 caution:
    "inline-flex items-center justify-center gap-2 rounded-full border border-stop-rule bg-transparent px-4 py-2 text-[13px] font-medium text-stop transition-colors hover:bg-stop-soft disabled:opacity-50",
 quiet:
    "inline-flex items-center gap-1.5 text-[13px] font-medium text-accent underline decoration-accent-rule underline-offset-4 transition-colors hover:decoration-accent",
 danger:
    "inline-flex items-center gap-1.5 text-[13px] font-medium text-stop underline decoration-stop-rule underline-offset-4 transition-colors hover:decoration-stop",
} as const;

export const input =
  "w-full rounded-xl border border-rule-strong bg-paper px-3.5 py-2 text-[13px] text-ink placeholder:text-ink-faint transition-colors hover:border-ink-faint";

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
    <div className="rounded-xl border border-rule bg-sunk px-3.5 py-3">
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
    <div className="sticky top-0 z-20 border-b border-rule bg-paper/85 backdrop-blur-md">
      <div className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-6 py-3">
        <div className="flex min-w-0 items-center gap-3">
          <Link href="/pilot" className="flex items-center gap-2.5 text-ink">
            <Glyph />
            <span className="font-display text-[15px] font-medium tracking-tight">SEO Autopilot</span>
          </Link>
          {section ? (
            <>
              <span aria-hidden="true" className="h-4 w-px bg-rule-strong" />
              <span className="eyebrow truncate">{section}</span>
            </>
          ) : null}
        </div>
        {children}
      </div>
    </div>
  );
}

export {Tabs} from "./nav-tabs";
