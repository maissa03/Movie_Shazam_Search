"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Activity, Clapperboard, Gauge, LayoutDashboard, Library, ScanSearch } from "lucide-react";

const NAV = [
  { href: "/", label: "Tableau de bord", icon: LayoutDashboard },
  { href: "/reconnaissance", label: "Reconnaissance", icon: ScanSearch },
  { href: "/bibliotheque", label: "Bibliothèque", icon: Library },
  { href: "/traitements", label: "Traitements", icon: Activity },
  { href: "/performances", label: "Performances", icon: Gauge },
];

export function Sidebar() {
  const path = usePathname();

  return (
    <aside className="border-b border-ink-800 bg-ink-900/70 backdrop-blur md:sticky md:top-0 md:h-screen md:w-64 md:shrink-0 md:border-b-0 md:border-r">
      <div className="flex h-full flex-col gap-6 p-4 md:p-5">
        <Link href="/" className="flex items-center gap-3">
          <span className="grid size-10 place-items-center rounded-xl bg-accent text-ink-950 shadow-lg shadow-accent/20">
            <Clapperboard className="size-5" aria-hidden />
          </span>
          <span>
            <span className="block text-base font-semibold tracking-tight text-white">MovieShazam</span>
            <span className="block text-xs text-slate-400">Identification locale de scènes</span>
          </span>
        </Link>

        <nav aria-label="Navigation principale" className="-mx-1 flex gap-1 overflow-x-auto md:flex-col md:overflow-visible">
          {NAV.map(({ href, label, icon: Icon }) => {
            const active = href === "/" ? path === "/" : path.startsWith(href);
            return (
              <Link
                key={href}
                href={href}
                aria-current={active ? "page" : undefined}
                className={`flex shrink-0 items-center gap-3 rounded-xl px-3 py-2 text-sm transition ${
                  active ? "bg-ink-800 text-white ring-1 ring-ink-700" : "text-slate-400 hover:bg-ink-850 hover:text-slate-200"
                }`}
              >
                <Icon className={`size-4 ${active ? "text-accent" : ""}`} aria-hidden />
                {label}
              </Link>
            );
          })}
        </nav>
      </div>
    </aside>
  );
}
