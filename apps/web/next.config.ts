import type {NextConfig} from "next";
// images.unoptimized: nothing here uses next/image, and the optimizer endpoint is
// where Next's unauthenticated RCE and SSRF advisories of 2026 lived. Off is
// one less public route to keep patched.
const config: NextConfig = {output:"standalone",poweredByHeader:false,reactStrictMode:true,images:{unoptimized:true}};
export default config;
