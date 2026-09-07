import Link from "next/link";

import {isConfigured} from "@/lib/oidc";

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
  const target = next && next.startsWith("/") && !next.startsWith("//") ? next : "/pilot";

  return (
    <main className="mx-auto flex max-w-md flex-col gap-6 px-6 py-24">
      <div className="flex flex-col gap-2">
        <h1 className="text-2xl font-semibold tracking-tight text-balance">Sign in</h1>
        <p className="text-pretty text-sm text-neutral-600">
          The control plane is limited to people who have been invited to a tenant.
        </p>
      </div>

      {error ? (
        <p className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
          {MESSAGES[error] ?? "Sign-in did not complete. Try again."}
        </p>
      ) : null}

      {configured ? (
        <Link
          href={`/api/auth/login?next=${encodeURIComponent(target)}`}
          className="inline-flex items-center justify-center rounded-md bg-neutral-900 px-4 py-2 text-sm font-medium text-white hover:bg-neutral-800 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-neutral-900"
          prefetch={false}
        >
          Continue with your identity provider
        </Link>
      ) : (
        <p className="rounded-md border border-neutral-200 bg-neutral-50 px-3 py-2 text-sm text-neutral-700">
          No identity provider is configured on this deployment, so there is nothing to sign in
          to. An operator sets <code className="font-mono text-xs">OIDC_ISSUER_URL</code>,{" "}
          <code className="font-mono text-xs">OIDC_CLIENT_ID</code> and{" "}
          <code className="font-mono text-xs">OIDC_CLIENT_SECRET</code>.
        </p>
      )}
    </main>
  );
}
