import {Note} from "@/app/components/ui";

/**
 * What to do about a Google client that Google will not accept.
 *
 * Written out in full, in one place, because the person who needs it is by
 * definition stuck: they have been bounced off a Google error page that names
 * a problem in our vocabulary and gives them nothing to act on. Telling them
 * "redirect_uri_mismatch" again would be repeating the unhelpful thing more
 * loudly.
 *
 * The URI is passed in rather than written here. It is the deployment's own
 * value, and a copy of it in a component is a copy that goes stale the day
 * the deployment moves -- which is the failure this guide exists to fix.
 *
 * It covers the publishing status as well as the redirect URI, because those
 * are two walls in a row and we can only see the first. On 2026-09-21 a tester
 * followed the original version of this guide, registered the callback
 * correctly, clicked Connect -- and was refused again, with `access_denied`
 * and "can only be accessed by developer-approved testers", because his own
 * Cloud project was still in Testing. Our preflight cannot warn about that:
 * it probes unauthenticated, and Google answers with the account chooser long
 * before it evaluates who is on a test-user list. So the only place this can
 * be said is here, before they leave for the Console.
 *
 * It also now says the thing that makes the whole exercise avoidable. A
 * tenant's own client means the tenant's own Google verification -- because
 * `analytics.readonly` is a sensitive scope, and Google verifies the project
 * that asks for it. Nobody was telling them that, so they were signing up for
 * a review process to reach data they already own.
 */
export function GoogleClientGuide({
  redirectUri,
  tone = "stop",
  label = "How to fix this",
}: {
  redirectUri: string;
  tone?: "stop" | "warn";
  label?: string;
}) {
  return (
    <Note tone={tone} label={label}>
      <p>
        Google is refusing the sign-in before it shows a consent screen, because this
        workspace&rsquo;s own OAuth client does not list our callback as somewhere it is
        allowed to return to. Five steps, all inside your own Google Cloud project:
      </p>
      <ol className="mt-2 flex list-decimal flex-col gap-1.5 pl-5">
        <li>
          Open{" "}
          <a
            className="underline underline-offset-2"
            href="https://console.cloud.google.com/apis/credentials"
            target="_blank"
            rel="noreferrer"
          >
            Google Cloud Console → APIs &amp; Services → Credentials
          </a>
          , and check the project selector at the top is the project your client belongs to.
        </li>
        <li>
          Under <strong className="font-medium">OAuth 2.0 Client IDs</strong>, click the client
          whose ID you saved here. It must be of type{" "}
          <strong className="font-medium">Web application</strong>; a Desktop or TV client
          cannot accept a redirect URI at all.
        </li>
        <li>
          Under <strong className="font-medium">Authorised redirect URIs</strong>, press{" "}
          <strong className="font-medium">Add URI</strong> and paste exactly this — not the
          Authorised JavaScript origins box above it, which is a different list and the usual
          mistake:
          <span className="mt-1.5 block rounded-xl border border-rule bg-surface px-2 py-1.5 font-mono text-xs break-all">
            {redirectUri}
          </span>
        </li>
        <li>
          Press <strong className="font-medium">Save</strong>. No trailing slash, no{" "}
          <code className="font-mono text-xs">http://</code>, no capital letters — Google
          compares it character for character.
        </li>
        <li>
          In the same project, open{" "}
          <a
            className="underline underline-offset-2"
            href="https://console.cloud.google.com/auth/audience"
            target="_blank"
            rel="noreferrer"
          >
            Google Auth Platform → Audience
          </a>{" "}
          and look at <strong className="font-medium">Publishing status</strong>. If it says{" "}
          <strong className="font-medium">Testing</strong>, only accounts listed there as test
          users can approve anything — including your own. Either add yourself under{" "}
          <strong className="font-medium">Test users</strong>, or press{" "}
          <strong className="font-medium">Publish app</strong>.
          <span className="mt-1.5 block text-xs text-ink-soft">
            This is a separate wall from the one above, and we cannot check it for you: Google
            only applies it once it knows who is signing in, which is after the point our
            check can reach. Fix it now or you will be refused a second time, with{" "}
            <code className="font-mono">access_denied</code>.
          </span>
        </li>
        <li>
          Come back and save your client ID and secret here again. We re-check it with Google
          on the way in, so a save that succeeds is one that will get you to a consent screen.
        </li>
      </ol>
      <p className="mt-2">
        Changes can take a minute to take effect at Google.
      </p>
      <p className="mt-2">
        <strong className="font-medium">Worth knowing before you spend time on this.</strong>{" "}
        Your own client also means your own Google verification. Analytics access is a
        sensitive scope, and Google reviews the project that asks for it — so keeping your own
        client eventually means your own demo video, your own review, and a cap of 100 people
        until it passes, all to reach data you already own. Removing your client below and
        using this deployment&rsquo;s shared one needs no setup and inherits the verification
        we are already going through. Your connections are unaffected either way; each one
        just asks to be reconnected once.
      </p>
    </Note>
  );
}
