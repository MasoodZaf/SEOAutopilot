import type {Metadata} from "next";
import {IBM_Plex_Mono, IBM_Plex_Sans, IBM_Plex_Serif} from "next/font/google";
import "./globals.css";

/*
 * Three voices from one family, which is why the page reads as engineered
 * rather than as three fonts sharing a page.
 *
 * Plex was drawn for a technology company, and it suits an instrument better
 * than Inter -- which was here before, and is the single most recognisable
 * "some interface" typeface there is.
 *
 * Mono is not decoration. This product is mostly identifiers and measurements
 * -- hostnames, client ids, TXT records, positions, impressions, commit shas --
 * and setting those in a proportional face makes a column of them unreadable.
 */
const sans = IBM_Plex_Sans({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  variable: "--font-plex-sans",
  display: "swap",
});

const mono = IBM_Plex_Mono({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  variable: "--font-plex-mono",
  display: "swap",
});

const serif = IBM_Plex_Serif({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  variable: "--font-plex-serif",
  display: "swap",
});

const siteUrl = process.env.NEXT_PUBLIC_APP_URL || "https://seoautopilot.dev";

export const metadata: Metadata = {
  metadataBase: new URL(siteUrl),
  title: {
    default: "SEO Autopilot — Auditable Multi-Tenant SEO Operations",
    template: "%s | SEO Autopilot",
  },
  description:
    "Auditable, multi-tenant SEO operations connecting crawl evidence, Search Console metrics, reviewable proposals, and outcome tracking.",
  keywords: [
    "SEO Autopilot",
    "Governed SEO",
    "Multi-Tenant SEO Platform",
    "Technical SEO Crawler",
    "Automated SEO Operations",
    "Google Search Console Analytics",
    "Core Web Vitals Optimization",
    "SEO Governance",
  ],
  authors: [{name: "SEO Autopilot Team"}],
  creator: "SEO Autopilot",
  publisher: "SEO Autopilot",
  robots: {
    index: true,
    follow: true,
    googleBot: {
      index: true,
      follow: true,
      "max-video-preview": -1,
      "max-image-preview": "large",
      "max-snippet": -1,
    },
  },
  alternates: {
    canonical: "/",
  },
  openGraph: {
    type: "website",
    locale: "en_US",
    url: siteUrl,
    title: "SEO Autopilot — Auditable SEO Operations",
    description:
      "Connect crawl and Search Console evidence to prioritize opportunities, review proposed changes, and track outcomes under governance.",
    siteName: "SEO Autopilot",
  },
  twitter: {
    card: "summary_large_image",
    title: "SEO Autopilot — Auditable SEO Operations",
    description:
      "SEO operations with separation of duties, fail-closed deployment policy, and clearly labeled outcome tracking.",
    creator: "@seoautopilot",
  },
};

export default function RootLayout({children}: Readonly<{children: React.ReactNode}>) {
  return (
    <html lang="en" className={`${sans.variable} ${mono.variable} ${serif.variable}`}>
      <body>{children}</body>
    </html>
  );
}
