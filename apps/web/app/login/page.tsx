import Link from "next/link";

import {Note, button} from "@/app/components/ui";
import {isConfigured} from "@/lib/oidc";
import {safeNext} from "@/lib/safe-next.mjs";

const MESSAGES: Record<string, string> = {
  provider_refused: "Your identity provider did not complete the sign-in.",
  callback_incomplete: "That sign-in link was missing something. Start again.",
  state_mismatch: "That sign-in did not start in this browser. Start again here.",
  nonce_mismatch: "That sign-in could not be matched to this request. Start again.",
  code_exchange_failed: "The identity provider could not be reached. Try again shortly.",
  session_expired: "Your session ended. Sign in again to continue.",
};

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{error?: string; next?: string}>;
}) {
  const {error, next} = await searchParams;
  const configured = isConfigured();
  const target = safeNext(next, process.env.APP_BASE_URL ?? "http://localhost:3000");

  return (
    <main className="mx-auto flex min-h-dvh max-w-md flex-col justify-center gap-8 px-6 py-20">
      <div className="flex flex-col gap-3">
        <p className="eyebrow">SEO Autopilot</p>
        <h1 className="font-display text-[32px] leading-[1.15] font-semibold tracking-tight text-balance text-ink">
          Sign in
        </h1>
        {/* This used to say access was "limited to people who have been invited
            to a tenant". That stopped being true the day sign-up became
            self-service, and it is the first sentence a new tester reads --
            telling them they are probably not allowed in. */}
        <p className="text-pretty text-[13px] leading-6 text-ink-soft">
          Sign in with Google. If this is your first visit you will be asked to name a
          workspace &mdash; it holds your sites, your keys and your data, and nobody
          else&rsquo;s.
        </p>
      </div>

      {error ? (
        <Note tone="stop" role="alert" label="Sign-in did not complete">
          {MESSAGES[error] ?? "Sign-in did not complete. Try again."}
        </Note>
      ) : null}

      {configured ? (
        <div className="flex flex-col gap-3">
          <Link
            href={`/auth/login?next=${encodeURIComponent(target)}`}
            className={button.primary}
            prefetch={false}
           data-tip="Sign in with your Google account. We ask only for your name and email address.">
            Continue with Google
          </Link>
          <p className="text-[12px] leading-5 text-ink-faint">
            We ask Google only for your name and email address. Search Console and
            Analytics are connected later, from your own account, and only if you want
            them.
          </p>
        </div>
      ) : (
        <Note tone="warn" label="Not configured">
          No identity provider is configured on this deployment, so there is nothing to
          sign in to. An operator sets <code className="font-mono">OIDC_ISSUER_URL</code>,{" "}
          <code className="font-mono">OIDC_CLIENT_ID</code> and{" "}
          <code className="font-mono">OIDC_CLIENT_SECRET</code>.
        </Note>
      )}

      <p className="mt-2 border-t border-rule pt-4 text-[12px] text-ink-faint">
        <Link href="/privacy" className="underline underline-offset-4 hover:text-ink-soft">
          Privacy
        </Link>
        <span aria-hidden="true" className="px-2">
          &middot;
        </span>
        <Link href="/terms" className="underline underline-offset-4 hover:text-ink-soft">
          Terms
        </Link>
      </p>
    </main>
  );
}
