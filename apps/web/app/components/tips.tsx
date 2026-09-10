/*
 * The tooltips, for assistive technology.
 *
 * The balloons are drawn with CSS pseudo-elements, which are decoration: they
 * are not in the accessibility tree, so a screen reader user got each control's
 * label and none of the explanation. That is help withheld from the people most
 * likely to want it.
 *
 * Every tipped control carries `aria-describedby` pointing at one of the spans
 * below, so the same sentence a mouse user sees on hover is announced after the
 * control's name. The ids are derived from the text itself, so a description is
 * declared exactly once no matter how many controls share it, and adding a
 * tooltip cannot collide with an existing id.
 *
 * Rendered once in the root layout rather than beside each control: a
 * description placed *inside* a button becomes part of its accessible name and
 * gets read twice, and placing one beside every control means threading a
 * wrapper through forms that are otherwise plain server-action markup.
 *
 * Generated from the `data-tip` attributes in app/. If you add one, add its
 * text here too -- `pnpm --filter web test` fails when the two disagree.
 */
export function TipDescriptions() {
  return (
    <div className="sr-only" aria-hidden={false}>
      <span id="tip-26b5b277ea">{"Add a domain you control and prove it with a DNS record."}</span>
      <span id="tip-6f07753cce">{"Apply the selected mode. Recorded with your reason; nothing publishes without approval unless you chose autopilot."}</span>
      <span id="tip-e68345520a">{"Authorise Google Analytics 4 for this site through your own Google OAuth client."}</span>
      <span id="tip-e734f40795">{"Authorise Search Console for this site through your own Google OAuth client."}</span>
      <span id="tip-01116296eb">{"Cancel this invitation. The address can no longer join with it."}</span>
      <span id="tip-93fc91618b">{"Check DNS now for the TXT record shown above. Safe to retry while it propagates."}</span>
      <span id="tip-4b1f1f81a7">{"Conversations, scheduled routines, keyword clusters and content briefs for this site."}</span>
      <span id="tip-2a1494aec0">{"Create the workspace and make you its owner. You can rename or invite people afterwards."}</span>
      <span id="tip-2d6f77dd1b">{"Dry run: reports what the current policy would allow or block on open proposals. Changes nothing."}</span>
      <span id="tip-aa7d586602">{"End this session on this browser. Your workspace, keys and sites are untouched."}</span>
      <span id="tip-ebe4f0342d">{"From your own Google Cloud OAuth client. Ends in .apps.googleusercontent.com — not the project number and not an API key."}</span>
      <span id="tip-8dcda9ad6f">{"How far this site may go on its own. Observe measures only; recommend drafts proposals for approval; autopilot deploys unattended."}</span>
      <span id="tip-a909e65d70">{"How many different people must approve a change before it can deploy. An author can never approve their own."}</span>
      <span id="tip-fdd41a3bf5">{"Install your GitHub App on the repository this site deploys from."}</span>
      <span id="tip-76c147b8df">{"Invite this address into this workspace. They join on their next sign-in and can see everything in it."}</span>
      <span id="tip-ad2bc41307">{"Issue a fresh verification token. The previous record stops working immediately."}</span>
      <span id="tip-e2ae7396fa">{"Issue a verification token for this domain and show the TXT record to publish."}</span>
      <span id="tip-9cb2174ecd">{"Kill switch. Blocks every automated deployment for this site until an owner lifts it after an incident review."}</span>
      <span id="tip-2efb708ac7">{"Look up DNS now. Safe to retry — records can take minutes to hours to appear."}</span>
      <span id="tip-638cd2f7c3">{"Open a revert pull request. The change is only undone once someone merges it."}</span>
      <span id="tip-8840e44e3f">{"Open the pull request that carries this change on your repository. A human still merges it."}</span>
      <span id="tip-ec0ccb8c77">{"Record your approval. You cannot approve a change you authored yourself."}</span>
      <span id="tip-b6ef7312e7">{"Register the domain and issue a one-time TXT record proving you control it."}</span>
      <span id="tip-c6f0c10545">{"Remove this person from the workspace. Their account survives; their access here does not."}</span>
      <span id="tip-386f83ca86">{"Replace the token with a fresh one. The old record stops working immediately."}</span>
      <span id="tip-bf1ec7e05e">{"Revoke your stored credential. Existing connectors keep working until they next renew a token, then fall back to this deployment's shared client."}</span>
      <span id="tip-728aeb6e5d">{"Run one mobile Lighthouse sample. Lab data, not real-user Core Web Vitals."}</span>
      <span id="tip-c08dbcd0f5">{"Save this approver count. It applies to proposals drafted from now on, not to ones already waiting."}</span>
      <span id="tip-da1dace285">{"Sign in with your Google account. We ask only for your name and email address."}</span>
      <span id="tip-e3ef53a7eb">{"Start a bounded crawl of this site now. Read-only: it gathers evidence and changes nothing on the site."}</span>
      <span id="tip-5be8fcba56">{"Take this proposal off the table. It stops counting toward approvals and cannot deploy."}</span>
      <span id="tip-c2cee2d7bc">{"The last part of your GitHub App's public URL."}</span>
      <span id="tip-09c4eb939d">{"The numeric ID on your GitHub App's settings page."}</span>
      <span id="tip-77738fa850">{"The secret beside that client ID in Google Cloud. Stored encrypted; no page ever reads it back to you."}</span>
      <span id="tip-2fee17e010">{"The whole .pem file your GitHub App issued, including the BEGIN and END lines."}</span>
      <span id="tip-6d09637632">{"Turn this opportunity into a reviewable proposal with an exact diff. Publishes nothing."}</span>
      <span id="tip-266be79a7f">{"Why you are changing the mode. Required by the API and recorded in the audit trail against your name."}</span>
    </div>
  );
}
