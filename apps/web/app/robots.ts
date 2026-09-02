import type {MetadataRoute} from "next";

export default function robots(): MetadataRoute.Robots {
  const baseUrl = process.env.NEXT_PUBLIC_APP_URL || "https://seoautopilot.dev";

  return {
    rules: [
      {
        userAgent: "*",
        allow: "/",
        disallow: ["/pilot", "/settings", "/api"],
      },
    ],
    sitemap: `${baseUrl}/sitemap.xml`,
  };
}
