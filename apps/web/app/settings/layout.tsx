import Link from "next/link";

import {SessionBar} from "@/app/components/session-bar";

const TABS = [
  // Ordered the way a new workspace is actually set up: your keys, then a site
  // to point them at, then the connectors that need both, then the people you
  // let in. A tab order that does not match the sequence is a tab order people
  // have to be told about.
  {href: "/settings/keys", label: "Keys"},
  {href: "/settings/sites", label: "Sites"},
  {href: "/settings/connectors", label: "Connectors"},
  {href: "/settings/members", label: "Members"},
];

export default function SettingsLayout({children}: {children: React.ReactNode}) {
  return (
    <div className="min-h-dvh bg-slate-50">
      <SessionBar tone="light" />
      <nav aria-label="Settings" className="border-b border-slate-200 bg-white">
        <ul className="mx-auto flex max-w-3xl gap-1 px-6">
          {TABS.map((tab) => (
            <li key={tab.href}>
              <Link
                href={tab.href}
                className="inline-block border-b-2 border-transparent px-3 py-3 text-sm font-medium text-slate-600 hover:border-slate-300 hover:text-slate-900 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-900"
              >
                {tab.label}
              </Link>
            </li>
          ))}
        </ul>
      </nav>
      {children}
    </div>
  );
}
