import {Masthead} from "@/app/components/ui";
import {ConnectionsBanner} from "@/app/components/connections-banner";
import {SessionBar} from "@/app/components/session-bar";
import {TipDescriptions} from "@/app/components/tips";

export default function PilotLayout({children}: {children: React.ReactNode}) {
  return (
    <div className="min-h-dvh bg-paper">
      <Masthead section="Control plane">
        <SessionBar />
      </Masthead>
      <ConnectionsBanner />
      {children}
      <TipDescriptions />
    </div>
  );
}
