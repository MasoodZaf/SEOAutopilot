"use client";

import Link from "next/link";
import {usePathname} from "next/navigation";

import {cn} from "@/lib/cn";

/**
 * Section tabs: a segmented pill on narrow screens, a side rail on wide ones.
 *
 * A client component for one reason: the current tab has to be marked, and
 * only the router knows the path. It used to render every tab identically, so
 * nothing on /settings/connectors said you were on Connectors.
 */
export function Tabs({items, label}: {items: {href: string; label: string}[]; label: string}) {
  const pathname = usePathname();
  return (
    <nav aria-label={label} className="px-6 pt-6 lg:pt-10 lg:pr-0">
      <ul className="inline-flex flex-wrap gap-1 rounded-full border border-rule bg-surface p-1 lg:sticky lg:top-24 lg:flex lg:flex-col lg:rounded-2xl lg:p-1.5">
        {items.map((item) => {
          const current = pathname === item.href || pathname.startsWith(`${item.href}/`);
          return (
            <li key={item.href}>
              <Link
                href={item.href}
                aria-current={current ? "page" : undefined}
                className={cn(
                  "block rounded-full px-4 py-1.5 text-[13px] font-medium transition-colors lg:rounded-xl lg:py-2",
                  current ? "bg-ink text-ink-inverse" : "text-ink-soft hover:bg-sunk hover:text-ink",
                )}
              >
                {item.label}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
