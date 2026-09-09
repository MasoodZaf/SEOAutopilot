import type {Metadata} from "next";

import {Legal, Section} from "../legal/shell";

export const metadata: Metadata = {
  title: "Terms of Service",
  description: "The terms for using the SEO Autopilot deployment at seo.oryxenlabs.com.",
};

const CONTACT = "support@oryxenlabs.com";

export default function TermsPage() {
  return (
    <Legal title="Terms of Service" updated="9 September 2026">
      <Section heading="What this is">
        <p>
          These terms cover the SEO Autopilot deployment at <code>seo.oryxenlabs.com</code>,
          operated by Oryxen Labs. Using it means accepting them. The service is currently in
          testing and provided free of charge; it may change, and it may be withdrawn.
        </p>
      </Section>

      <Section heading="Your account and your workspace">
        <p>
          You sign in with a Google account and create your own workspace. You are responsible
          for what happens under your account, and for the accounts you invite into your
          workspace — anyone you invite can see everything in it.
        </p>
        <p>
          You may create a small number of workspaces. The service is intended for people
          managing their own sites, not for resale or automated bulk registration.
        </p>
      </Section>

      <Section heading="Sites you connect">
        <p>
          Only add sites you own or are authorised to manage. Adding one requires proving control
          by publishing a DNS record, which is a check, not a formality — do not attempt to work
          around it for a domain that is not yours.
        </p>
        <p>
          The credentials you provide are yours: your Google OAuth client, your GitHub App, your
          API quota. You are responsible for keeping them within the terms of the providers that
          issued them.
        </p>
      </Section>

      <Section heading="What the service does, and what it will not do on its own">
        <p>
          SEO Autopilot gathers evidence, ranks opportunities and drafts changes. A proposal
          becomes a pull request on your repository only after a person approves it, and the
          author of a change cannot approve their own. Change budgets, freeze windows and an
          emergency stop exist and are enforced.
        </p>
        <p>
          None of that makes the output correct. Findings are generated from automated crawls and
          third-party metrics, both of which can be wrong or stale. <b>Review every change before
          you approve it.</b> You remain responsible for what is deployed to your site.
        </p>
      </Section>

      <Section heading="What we do not promise">
        <p>
          The service is provided as is, without warranty of any kind. We do not promise that it
          will be available, that its findings will be accurate, or that acting on them will
          improve your search performance — search ranking depends on systems no one here
          controls.
        </p>
        <p>
          To the extent the law allows, Oryxen Labs is not liable for lost rankings, lost revenue,
          lost data, or any indirect or consequential loss arising from use of the service. This
          is testing software; keep your own backups and your own version control, which the
          pull-request workflow is deliberately built around.
        </p>
      </Section>

      <Section heading="Acceptable use">
        <p>
          Do not use the service to attack, overload or scrape sites you do not control, to
          break any provider&rsquo;s terms, or to attempt to reach another workspace&rsquo;s data.
          We may suspend an account that does, or that puts the deployment at risk.
        </p>
      </Section>

      <Section heading="Ending it">
        <p>
          You may stop at any time: remove your credentials and delete your workspace. We may
          suspend or end access, and because this is a testing deployment we may shut it down —
          with reasonable notice where we can give it. Contact{" "}
          <a className="underline" href={`mailto:${CONTACT}`}>{CONTACT}</a> for anything here.
        </p>
      </Section>
    </Legal>
  );
}
