"use client";

import { UploadCloud } from "lucide-react";
import { useRef, useState } from "react";

export function Dropzone({
  onFiles,
  multiple = false,
  disabled = false,
  title,
  hint,
}: {
  onFiles: (files: File[]) => void;
  multiple?: boolean;
  disabled?: boolean;
  title: string;
  hint?: string;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);

  const handle = (list: FileList | null) => {
    if (!list || disabled) return;
    const files = Array.from(list).filter((f) => f.type.startsWith("video/") || /\.(mp4|m4v|mkv|mov|avi|webm|mpe?g|ts|wmv|flv|ogv|3gp)$/i.test(f.name));
    if (files.length) onFiles(multiple ? files : files.slice(0, 1));
  };

  return (
    <div
      role="button"
      tabIndex={0}
      aria-disabled={disabled}
      onClick={() => !disabled && input.current?.click()}
      onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && !disabled && input.current?.click()}
      onDragOver={(e) => {
        e.preventDefault();
        if (!disabled) setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setOver(false);
        handle(e.dataTransfer.files);
      }}
      className={`flex cursor-pointer flex-col items-center justify-center gap-2 rounded-2xl border-2 border-dashed px-6 py-10 text-center transition focus-visible:outline-2 focus-visible:outline-accent ${
        disabled
          ? "cursor-not-allowed border-ink-700 opacity-60"
          : over
            ? "border-accent bg-accent/5"
            : "border-ink-600 bg-ink-900/60 hover:border-accent/60 hover:bg-ink-850"
      }`}
    >
      <UploadCloud className={`size-8 ${over ? "text-accent" : "text-slate-400"}`} aria-hidden />
      <p className="text-sm font-medium text-slate-100">{title}</p>
      {hint && <p className="text-xs text-slate-400">{hint}</p>}
      <input
        ref={input}
        type="file"
        accept="video/*,.mkv,.avi,.ts,.wmv,.flv"
        multiple={multiple}
        className="hidden"
        onChange={(e) => {
          handle(e.target.files);
          e.target.value = "";
        }}
      />
    </div>
  );
}
