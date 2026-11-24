import type { Health, ImportResult, Job, Movie, MovieDetail, Search, Stats } from "./types";

export const API_URL = (process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000").replace(/\/$/, "");

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

const TOO_LARGE = "Fichier trop volumineux : la limite de taille autorisée par le backend est dépassée.";

function errorMessage(status: number, body: unknown, fallback: string): string {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (status === 413) return typeof detail === "string" && detail !== "Content Too Large" ? detail : TOO_LARGE;
  if (typeof detail === "string") return detail;
  return detail != null ? JSON.stringify(detail) : fallback;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, { cache: "no-store", ...init });
  } catch {
    throw new ApiError(0, `Backend injoignable sur ${API_URL}. Lancez-le avec scripts/start_windows.ps1 (ou start_unix.sh).`);
  }
  if (!res.ok) {
    let body: unknown = null;
    try {
      body = await res.json();
    } catch {
    }
    throw new ApiError(res.status, errorMessage(res.status, body, res.statusText));
  }
  return (await res.json()) as T;
}

function upload<T>(path: string, form: FormData, onProgress?: (fraction: number) => void): Promise<T> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${API_URL}${path}`);
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && onProgress) onProgress(e.loaded / e.total);
    };
    xhr.onload = () => {
      let body: unknown = null;
      try {
        body = JSON.parse(xhr.responseText);
      } catch {
      }
      if (xhr.status >= 200 && xhr.status < 300) resolve(body as T);
      else reject(new ApiError(xhr.status, errorMessage(xhr.status, body, `Erreur HTTP ${xhr.status}`)));
    };
    xhr.onerror = () => reject(new ApiError(0, `Backend injoignable sur ${API_URL}.`));
    xhr.send(form);
  });
}

export const api = {
  health: () => request<Health>("/api/health"),
  stats: () => request<Stats>("/api/stats"),
  movies: (params: { q?: string; sort?: string; order?: string; status?: string } = {}) => {
    const qs = new URLSearchParams(Object.entries(params).filter(([, v]) => v) as [string, string][]);
    return request<Movie[]>(`/api/movies${qs.size ? `?${qs}` : ""}`);
  },
  movie: (id: number) => request<MovieDetail>(`/api/movies/${id}`),
  importMovie: (file: File, onProgress?: (f: number) => void, title?: string, year?: string) => {
    const form = new FormData();
    form.append("file", file);
    if (title) form.append("title", title);
    if (year) form.append("year", year);
    return upload<ImportResult>("/api/movies", form, onProgress);
  },
  updateMovie: (id: number, body: { title?: string; year?: number; clear_year?: boolean }) =>
    request<Movie>(`/api/movies/${id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }),
  deleteMovie: (id: number) => request<{ deleted: boolean }>(`/api/movies/${id}`, { method: "DELETE" }),
  reindex: (id: number, force = false) =>
    request<{ movie: Movie; job: Job | null; message: string }>(`/api/movies/${id}/reindex?force=${force}`, { method: "POST" }),
  startSearch: (file: File, method: "bands" | "exhaustive", onProgress?: (f: number) => void) => {
    const form = new FormData();
    form.append("file", file);
    form.append("method", method);
    return upload<{ search_id: string; job: Job }>("/api/search", form, onProgress);
  },
  search: (id: string) => request<Search>(`/api/search/${id}`),
  searches: (limit = 10) => request<Search[]>(`/api/searches?limit=${limit}`),
  jobs: (kind?: string) => request<Job[]>(`/api/jobs${kind ? `?kind=${kind}` : ""}`),
  job: (id: string) => request<Job>(`/api/jobs/${id}`),
  cancelJob: (id: string) => request<Job>(`/api/jobs/${id}/cancel`, { method: "POST" }),
};

export const mediaUrl = {
  thumbnail: (id: number) => `${API_URL}/api/media/${id}/thumbnail`,
  frame: (id: number, ms: number) => `${API_URL}/api/media/${id}/frame?ms=${Math.max(0, Math.round(ms))}`,
  video: (id: number) => `${API_URL}/api/media/${id}/video`,
};
