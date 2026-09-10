import {Masthead, Tabs} from "@/app/components/ui";
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
    <div className="min-h-dvh bg-paper">
      <Masthead section="Settings">
        <SessionBar />
      </Masthead>
      <Tabs items={TABS} label="Settings" />
      {children}
    </div>
  );
}
