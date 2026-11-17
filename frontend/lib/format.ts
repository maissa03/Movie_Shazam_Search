// Formats lisibles (français) pour les durées, tailles, dates et scores.

export function hms(ms: number | null | undefined, withTenths = false): string {
  if (ms == null || Number.isNaN(ms)) return "—";
  const total = Math.max(0, ms) / 1000;
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const sec = withTenths ? s.toFixed(1).padStart(4, "0") : String(Math.floor(s)).padStart(2, "0");
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${sec}`;
}

export function duration(ms: number | null | undefined): string {
  if (ms == null) return "—";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  const s = ms / 1000;
  if (s < 60) return `${s.toFixed(s < 10 ? 1 : 0)} s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} min ${String(Math.round(s % 60)).padStart(2, "0")} s`;
  return `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, "0")} min`;
}

export function bytes(n: number | null | undefined): string {
  if (n == null) return "—";
  const units = ["o", "Ko", "Mo", "Go", "To"];
  let v = n;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(v < 10 && i > 0 ? 1 : 0)} ${units[i]}`;
}

export function num(n: number | null | undefined, digits = 0): string {
  if (n == null) return "—";
  return n.toLocaleString("fr-FR", { maximumFractionDigits: digits, minimumFractionDigits: digits });
}

export function pct(f: number | null | undefined, digits = 0): string {
  if (f == null) return "—";
  return `${(f * 100).toFixed(digits)} %`;
}

export function dateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString("fr-FR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function relative(iso: string | null | undefined): string {
  if (!iso) return "—";
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  if (diff < 60) return "à l'instant";
  if (diff < 3600) return `il y a ${Math.floor(diff / 60)} min`;
  if (diff < 86400) return `il y a ${Math.floor(diff / 3600)} h`;
  return `il y a ${Math.floor(diff / 86400)} j`;
}
