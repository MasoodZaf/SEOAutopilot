import {SessionBar} from "@/app/components/session-bar";

export default function PilotLayout({children}: {children: React.ReactNode}) {
  return (
    <div className="min-h-dvh bg-zinc-950">
      <SessionBar />
      {children}
    </div>
  );
}
