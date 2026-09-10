import Link from "next/link";

/**
 * The frame both legal pages share.
 *
 * They exist because Google will not let an External OAuth app leave Testing
 * without a reachable privacy policy and terms of service on an authorised
 * domain -- but they are written to be read, not to satisfy a form. Anything
 * here that describes what the system does with data is a statement about code
 * that exists in this repository, and is meant to stay that way: if a claim
 * below stops being true, the claim is the bug.
 */
export function Legal({
  title,
  updated,
  children,
}: {
  title: string;
  updated: string;
  children: React.ReactNode;
}) {
  return (
    <main className="min-h-dvh bg-paper text-ink">
      <header className="border-b border-rule bg-surface">
        <div className="mx-auto flex max-w-3xl items-center justify-between px-6 py-4">
          <Link href="/" className="font-display text-[15px] font-semibold tracking-tight text-ink">
            SEO Autopilot
          </Link>
          <nav className="flex gap-4 text-sm text-ink-soft">
            <Link href="/privacy" className="hover:text-ink">
              Privacy
            </Link>
            <Link href="/terms" className="hover:text-ink">
              Terms
            </Link>
          </nav>
        </div>
      </header>

      <div className="mx-auto max-w-3xl px-6 py-12">
        <h1 className="font-display text-balance text-[30px] font-semibold tracking-tight text-ink">{title}</h1>
        <p className="mt-2 text-sm text-ink-faint">Last updated {updated}</p>
        <div className="mt-8 flex flex-col gap-8">{children}</div>
      </div>
    </main>
  );
}

export function Section({heading, children}: {heading: string; children: React.ReactNode}) {
  return (
    <section className="flex flex-col gap-3">
      <h2 className="text-lg font-semibold text-ink">{heading}</h2>
      <div className="flex flex-col gap-3 text-pretty text-sm leading-6 text-ink-soft">
        {children}
      </div>
    </section>
  );
}
