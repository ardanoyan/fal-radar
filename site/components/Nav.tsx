"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const LINKS = [
  { href: "/", label: "Radar" },
  { href: "/models/", label: "Models" },
  { href: "/about/", label: "About" },
];

export default function Nav() {
  const path = usePathname() || "/";
  const norm = path.endsWith("/") ? path : `${path}/`;
  return (
    <nav className="nav" aria-label="Main">
      {LINKS.map((l) => (
        <Link key={l.href} href={l.href} aria-current={norm === l.href ? "page" : undefined}>
          {l.label}
        </Link>
      ))}
    </nav>
  );
}
