import Link from "next/link";

import {button, Masthead} from "@/app/components/ui";
import {ConnectionsBanner} from "@/app/components/connections-banner";
import {SessionBar} from "@/app/components/session-bar";
import {TipDescriptions} from "@/app/components/tips";

export default function PilotLayout({children}: {children: React.ReactNode}) {
  return (
    <div className="min-h-dvh bg-paper">
      <Masthead section="Control plane">
        <nav aria-label="Main" className="flex items-center gap-4">
          <Link href="/tutorial" className={button.quiet}>
            Tutorial
          </Link>
          <Link href="/settings" className={button.quiet}>
            Settings
          </Link>
          <SessionBar />
        </nav>
      </Masthead>
      <ConnectionsBanner />
      {children}
      <TipDescriptions />
    </div>
  );
}
