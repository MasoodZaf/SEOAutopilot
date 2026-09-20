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
          <span className="mt-1.5 block rounded-[4px] border border-rule bg-surface px-2 py-1.5 font-mono text-xs break-all">
            {redirectUri}
          </span>
        </li>
        <li>
          Press <strong className="font-medium">Save</strong>. No trailing slash, no{" "}
          <code className="font-mono text-xs">http://</code>, no capital letters — Google
          compares it character for character.
        </li>
        <li>
          Come back and save your client ID and secret here again. We re-check it with Google
          on the way in, so a save that succeeds is one that will work.
        </li>
      </ol>
      <p className="mt-2">
        Changes can take a minute to take effect at Google. If it still refuses, the simplest
        way forward is to remove your own client below and use this deployment&rsquo;s shared
        one, which needs no setup.
      </p>
    </Note>
  );
}
