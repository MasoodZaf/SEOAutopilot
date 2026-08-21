import type {Metadata} from "next";
import {Inter} from "next/font/google";
import "./globals.css";

const inter = Inter({subsets: ["latin"]});

const siteUrl = process.env.NEXT_PUBLIC_APP_URL || "https://seoautopilot.dev";

export const metadata: Metadata = {
  metadataBase: new URL(siteUrl),
  title: {
    default: "SEO Autopilot — Auditable Multi-Tenant Autonomous SEO Operations",
    template: "%s | SEO Autopilot",
  },
  description:
    "Auditable, multi-tenant SEO operations system connecting crawl evidence, GSC search metrics, and Core Web Vitals to safely deploy and measure ranking improvements.",
  keywords: [
    "SEO Autopilot",
    "Autonomous SEO",
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
    title: "SEO Autopilot — Auditable Autonomous SEO Operations",
    description:
      "Connect crawl, Search Console, analytics, and delivery data to prioritize, approve, deploy, and measure SEO improvements with zero hallucinations.",
    siteName: "SEO Autopilot",
  },
  twitter: {
    card: "summary_large_image",
    title: "SEO Autopilot — Auditable Autonomous SEO Operations",
    description:
      "Enterprise SEO operations with separation of duties, fail-closed policy, and 28-day causal measurement.",
    creator: "@seoautopilot",
  },
};

export default function RootLayout({children}: Readonly<{children: React.ReactNode}>) {
  return (
    <html lang="en">
      <body className={inter.className}>{children}</body>
    </html>
  );
}
