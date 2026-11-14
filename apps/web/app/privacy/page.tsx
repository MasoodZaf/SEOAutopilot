import type {Metadata} from "next";

import {Legal, Section} from "../legal/shell";

export const metadata: Metadata = {
  title: "Privacy Policy",
  description:
    "What SEO Autopilot stores, why, who can reach it, and how to have it deleted.",
};

const CONTACT = "support@oryxenlabs.com";

export default function PrivacyPage() {
  return (
    <Legal title="Privacy Policy" updated="7 November 2026">
      <Section heading="Who this covers">
        <p>
          SEO Autopilot is an SEO operations tool. You connect sites you control, it gathers
          evidence about them, and it proposes changes you review before anything is applied.
          This policy describes the deployment at <code>seo.oryxenlabs.com</code>, operated by
          Oryxen Labs. Questions go to <a className="underline" href={`mailto:${CONTACT}`}>{CONTACT}</a>.
        </p>
      </Section>

      <Section heading="What is stored">
        <p>Five kinds of thing, and nothing else:</p>
        <ul className="ml-5 list-disc space-y-2">
          <li>
            <b>Your identity.</b> When you sign in with Google we store the account identifier
            Google gives us, your email address and your display name. We use them to know who
            you are across visits and to show your name to other members of your own workspace.
          </li>
          <li>
            <b>Credentials you enter.</b> Your own Google OAuth client and GitHub App details if
            you add them, the AI provider keys you add (Anthropic or OpenAI), and the tokens that
            authorising Search Console, Analytics or a repository produces. These are encrypted
            before they are written down (see below).
          </li>
          <li>
            <b>Evidence about your sites.</b> Pages fetched from sites you have added and proved
            you control, and the metrics Google returns for them — Search Console impressions,
            clicks and positions, Analytics figures, and mobile performance samples.
          </li>
          <li>
            <b>Work produced from that.</b> Findings, ranked opportunities, proposed changes, the
            decisions you take on them and the outcomes measured afterwards.
          </li>
          <li>
            <b>What you write to the assistant.</b> Your conversations with the site assistant,
            and the blog and Reddit drafts it prepares, kept in your workspace so you can review
            and return to them.
          </li>
        </ul>
        <p>
          We do not use advertising trackers, we do not run third-party analytics on this
          service, and we do not sell anything described here. The only party we pass any of it
          to is the AI provider you choose, as described next.
        </p>
      </Section>

      <Section heading="AI providers">
        <p>
          The assistant, blog drafting and Reddit drafting run on an AI provider key that you add
          — Anthropic or OpenAI — under your own account with that provider. When you use one of
          those features, the evidence the request needs is sent to that provider to produce the
          answer: page text and findings from your site and, when the question calls for it, the
          Search Console and Analytics figures described above. Nothing is sent to a provider
          whose key you have not added, and nothing is sent unless you use one of these features.
        </p>
        <p>
          What the provider does with it is governed by its API terms, which you accept with it
          directly. Model output is never published on its own: every change it suggests becomes a
          proposal a person reviews, and Reddit drafts are handed to you to post yourself.
        </p>
      </Section>

      <Section heading="Payments">
        <p>
          If you subscribe, Stripe collects and holds your card details under its own privacy
          policy. We never see or store them. We keep only Stripe&rsquo;s reference for your
          workspace, the subscription status and its dates.
        </p>
      </Section>

      <Section heading="Google user data, specifically">
        <p>
          Signing in requests only the <code>openid</code>, <code>email</code> and{" "}
          <code>profile</code> scopes — enough to establish who you are and nothing more.
        </p>
        <p>
          Connecting Search Console or Analytics is a separate, later, optional step. It runs
          through Oryxen Labs&rsquo; Google client, or through your own Google Cloud OAuth client
          if you add one. The data those grants return is used for one purpose: producing and
          measuring SEO findings inside your own workspace, and answering your own questions about
          them. We never use it to train models, never use it for advertising, and never sell it.
          It is transferred only to the AI provider whose key you added, only to answer a request
          you made in the app.
        </p>
        <p>
          SEO Autopilot&rsquo;s use of information received from Google APIs adheres to the{" "}
          <a
            className="underline"
            href="https://developers.google.com/terms/api-services-user-data-policy"
            rel="noopener noreferrer"
            target="_blank"
          >
            Google API Services User Data Policy
          </a>
          , including the Limited Use requirements. You can revoke our access at any time from
          your{" "}
          <a
            className="underline"
            href="https://myaccount.google.com/permissions"
            rel="noopener noreferrer"
            target="_blank"
          >
            Google account permissions
          </a>{" "}
          page, or by removing the connector inside the app.
        </p>
      </Section>

      <Section heading="How it is separated and protected">
        <p>
          Every workspace is isolated in the database itself, by row-level security policies that
          the application cannot bypass — it connects as a role that has no permission to. A
          query that forgets to name a workspace returns nothing rather than someone else&rsquo;s
          rows.
        </p>
        <p>
          Credentials and tokens are encrypted with AES-GCM before storage, bound to the
          workspace they belong to, so a row lifted from one workspace cannot be decrypted in
          another. Traffic is served over HTTPS only.
        </p>
        <p>
          Being straightforward about the limit of that: the server holds the encryption key,
          because the server is what exchanges your credentials with Google and GitHub. This
          protects your data from anyone who reads the database — a dump, a backup, a replica —
          but not from whoever operates this deployment. If that matters for the account you are
          connecting, connect one scoped to only the sites and repositories you intend to use
          here.
        </p>
      </Section>

      <Section heading="Where it lives, and for how long">
        <p>
          Data is stored on a server in Helsinki, Finland, operated by Hetzner, and reached
          through Cloudflare. It is kept while your workspace exists. Removing a credential inside the app
          deletes it immediately. Ask us to delete your workspace and we delete everything
          belonging to it — evidence, findings, proposals, conversations, drafts and credentials
          — within 30 days, and your sign-in identity too if you ask.
        </p>
        <p>
          Ask us at <a className="underline" href={`mailto:${CONTACT}`}>{CONTACT}</a> for a copy
          of what is held about you, a correction, or deletion, and we will act on it. Nightly
          backups are kept for 14 days and then deleted on their own, so a deletion reaches the
          backups within 14 days of being carried out.
        </p>
      </Section>

      <Section heading="Changes">
        <p>
          If this policy changes in a way that affects what we do with your data, the date above
          changes and the change is described here. This service is in testing; it is not
          intended for, and is not knowingly used by, anyone under 16.
        </p>
      </Section>
    </Legal>
  );
}
