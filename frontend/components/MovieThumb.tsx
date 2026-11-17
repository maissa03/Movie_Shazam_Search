"use client";

import { Film } from "lucide-react";
import { useState } from "react";

/** Image servie par le backend (jamais un chemin du disque) avec repli neutre. */
export function MovieThumb({ src, alt, className = "" }: { src: string | null; alt: string; className?: string }) {
  const [failed, setFailed] = useState(false);
  if (!src || failed) {
    return (
      <div className={`grid place-items-center bg-gradient-to-br from-ink-800 to-ink-850 text-slate-500 ${className}`}>
        <Film className="size-8" aria-hidden />
      </div>
    );
  }
  // eslint-disable-next-line @next/next/no-img-element
  return <img src={src} alt={alt} className={`object-cover ${className}`} onError={() => setFailed(true)} loading="lazy" />;
}
