import type {Metadata} from "next";
import {Inter} from "next/font/google";
import "./globals.css";
const inter=Inter({subsets:["latin"]});
export const metadata:Metadata={title:"SEO Autopilot",description:"Auditable SEO operations from evidence to measured change."};
export default function Layout({children}:Readonly<{children:React.ReactNode}>){return <html lang="en"><body className={inter.className}>{children}</body></html>}
