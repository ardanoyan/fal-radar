import type { Metadata, Viewport } from "next";
import { IBM_Plex_Mono, Instrument_Sans, Marcellus } from "next/font/google";
import Link from "next/link";
import Nav from "@/components/Nav";
import { REPO_URL, site } from "@/lib/data";
import "./globals.css";

const marcellus = Marcellus({
  subsets: ["latin", "latin-ext"],
  weight: "400",
  variable: "--font-marcellus",
  display: "swap",
});
const instrument = Instrument_Sans({
  subsets: ["latin", "latin-ext"],
  variable: "--font-instrument",
  display: "swap",
});
const plexMono = IBM_Plex_Mono({
  subsets: ["latin", "latin-ext"],
  weight: ["400", "500"],
  variable: "--font-plex-mono",
  display: "swap",
});

export const metadata: Metadata = {
  title: { default: "fal radar: built on fal, mapped weekly", template: "%s | fal radar" },
  description:
    "Public GitHub projects built on fal, found through GitHub's REST API, classified and mapped weekly. An unofficial community project.",
  metadataBase: new URL("https://fal-radar.vercel.app"),
};

export const viewport: Viewport = { themeColor: "#ecece9", width: "device-width", initialScale: 1 };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  const s = site();
  return (
    <html lang="en" className={`${marcellus.variable} ${instrument.variable} ${plexMono.variable}`}>
      <body>
        <header className="site-head">
          <div className="wrap">
            <Link href="/" className="wordmark">
              fal radar
            </Link>
            <Nav />
          </div>
        </header>
        <main className="wrap">{children}</main>
        <footer className="site-foot">
          <div className="wrap">
            <p>Unofficial. Not affiliated with fal.</p>
            <p>
              Data: public GitHub. Data date <span className="mono">{s.data_date}</span>.
            </p>
            <p>
              Built by Arda Noyan Karasoglu. <a href={REPO_URL}>Source on GitHub</a>.
            </p>
          </div>
        </footer>
      </body>
    </html>
  );
}
