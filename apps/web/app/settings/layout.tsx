import Link from "next/link";

import {button, Masthead, Tabs} from "@/app/components/ui";
import {ConnectionsBanner} from "@/app/components/connections-banner";
import {SessionBar} from "@/app/components/session-bar";
import {TipDescriptions} from "@/app/components/tips";

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
    <div className="min-h-dvh bg-paper">
      <Masthead section="Settings">
        <nav aria-label="Main" className="flex items-center gap-3">
          <Link href="/pilot" className={button.quiet}>
            Dashboard
          </Link>
          <SessionBar />
        </nav>
      </Masthead>
      <ConnectionsBanner />
      <Tabs items={TABS} label="Settings" />
      {children}
      <TipDescriptions />
    </div>
  );
}
