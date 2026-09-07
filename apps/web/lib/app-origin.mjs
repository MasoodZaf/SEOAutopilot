/**
 * The origin this deployment is actually reached at.
 *
 * A route handler behind a reverse proxy sees the request the proxy made, not
 * the one the browser made. `new URL(request.url).origin` is therefore the
 * container's own hostname and internal port — `https://627fe7041447:3000` —
 * and any redirect built on it sends the browser to a name that resolves
 * nowhere outside the Docker network.
 *
 * That is not a theoretical concern: it is how the first real sign-in failed.
 * The login route was immune because it builds its redirect URI from
 * `APP_BASE_URL`; the callback was not, so a completed OAuth exchange ended on
 * "Safari can't find the server" and the API was never called, which meant the
 * identity was never resolved and the invitation never claimed.
 *
 * Three sources, most trustworthy first. `APP_BASE_URL` is configuration and
 * cannot be influenced by a request. The forwarding headers are the proxy's
 * account of the original request, used only when configuration is silent.
 * The request's own origin is last, and is correct exactly when there is no
 * proxy — a local `next dev`, or the test suite.
 *
 * @param {Request} request
 * @returns {string}
 */
export function appOrigin(request) {
  const configured = process.env.APP_BASE_URL;
  if (configured) {
    try {
      return new URL(configured).origin;
    } catch {
      // A malformed APP_BASE_URL should not take sign-in down; fall through to
      // the headers, which are very likely right.
    }
  }
  const host = request.headers.get("x-forwarded-host");
  if (host) {
    const proto = request.headers.get("x-forwarded-proto") ?? "https";
    try {
      // Built by the parser rather than concatenated, so a header carrying a
      // path, a port, or credentials cannot smuggle anything past `.origin`.
      return new URL(`${proto.split(",")[0].trim()}://${host.split(",")[0].trim()}`).origin;
    } catch {
      // Same reasoning as above.
    }
  }
  return new URL(request.url).origin;
}
