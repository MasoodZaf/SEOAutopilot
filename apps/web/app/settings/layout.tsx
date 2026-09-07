import {SessionBar} from "@/app/components/session-bar";

export default function SettingsLayout({children}: {children: React.ReactNode}) {
  return (
    <div className="min-h-dvh bg-slate-50">
      <SessionBar tone="light" />
      {children}
    </div>
  );
}
