import {Masthead} from "@/app/components/ui";
import {SessionBar} from "@/app/components/session-bar";

export default function PilotLayout({children}: {children: React.ReactNode}) {
  return (
    <div className="min-h-dvh bg-paper">
      <Masthead section="Control plane">
        <SessionBar />
      </Masthead>
      {children}
    </div>
  );
}
