import type {NextConfig} from "next";

// images.unoptimized: nothing here uses next/image, and the optimizer endpoint is
// where Next's unauthenticated RCE and SSRF advisories of 2026 lived. Off is
// one less public route to keep patched.
//
// Framing is refused everywhere: approve, publish and revert are single clicks,
// and a page that can frame this one can trick a signed-in reviewer into them.
const config: NextConfig = {
  output: "standalone",
  poweredByHeader: false,
  reactStrictMode: true,
  images: {unoptimized: true},
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          {key: "X-Frame-Options", value: "DENY"},
          {key: "Content-Security-Policy", value: "frame-ancestors 'none'"},
          {key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=(), payment=()"},
        ],
      },
    ];
  },
};

export default config;
