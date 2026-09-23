import Link from "next/link";

import {button, Glyph} from "./ui";

/** The public pages' header: the landing page and the tutorial. */
export function SiteHeader() {
  return (
    <header className="sticky top-0 z-20 border-b border-rule bg-paper/85 backdrop-blur-md">
      <div className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-6 py-3">
        <Link href="/" className="flex items-center gap-2.5 text-ink">
          <Glyph />
          <span className="font-display text-[15px] font-medium tracking-tight">SEO Autopilot</span>
        </Link>
        <nav aria-label="Main" className="flex items-center gap-2">
          <Link
            href="/tutorial"
            className="hidden rounded-full px-4 py-2 text-[13px] font-medium text-ink-soft transition-colors hover:text-ink sm:inline-flex"
          >
            Tutorial
          </Link>
          <Link href="/login" className={`${button.secondary} hidden sm:inline-flex`}>
            Sign in
          </Link>
          <Link href="/pilot" className={button.primary}>
            Control plane
          </Link>
        </nav>
      </div>
    </header>
  );
}

/** The public pages' footer. Privacy and terms must stay reachable: Google checks. */
export function SiteFooter() {
  return (
    <footer className="border-t border-rule">
      <div className="mx-auto flex max-w-6xl flex-col gap-4 px-6 py-10 text-[13px] text-ink-faint sm:flex-row sm:items-center sm:justify-between">
        <p className="flex items-center gap-2.5">
          <Glyph className="size-4 text-ink-soft" />
          SEO Autopilot &middot; Oryxen Labs
        </p>
        <nav aria-label="Footer" className="flex gap-6">
          <Link href="/tutorial" className="transition-colors hover:text-ink">
            Tutorial
          </Link>
          <Link href="/privacy" className="transition-colors hover:text-ink">
            Privacy Policy
          </Link>
          <Link href="/terms" className="transition-colors hover:text-ink">
            Terms of Service
          </Link>
        </nav>
      </div>
    </footer>
  );
}
