
export type MovieStatus = "pending" | "indexing" | "indexed" | "failed" | "cancelled";
export type JobState = "queued" | "running" | "completed" | "failed" | "cancelled";
export type SearchStatus =
  | "queued"
  | "running"
  | "match"
  | "ambiguous"
  | "no_match"
  | "insufficient"
  | "failed"
  | "cancelled";

export interface Movie {
  id: number;
  title: string;
  year: number | null;
  original_filename: string;
  file_size: number;
  duration_ms: number;
  width: number;
  height: number;
  codec: string;
  fps: number;
  format_name: string;
  status: MovieStatus;
  index_version: string | null;
  segments_total: number;
  segments_done: number;
  fingerprints_count: number;
  has_thumbnail: boolean;
  playable_in_browser: boolean;
  error: string | null;
  created_at: string;
  indexed_at: string | null;
  active_job_id: string | null;
}

export interface Segment {
  id: number;
  segment_index: number;
  start_ms: number;
  end_ms: number;
  status: "pending" | "done" | "failed";
  fingerprints_count: number;
  frames_decoded: number;
  processing_ms: number | null;
  worker_pid: number | null;
  error: string | null;
  index_version: string;
}

export interface MovieDetail extends Movie {
  segments: Segment[];
}

export interface Job {
  id: string;
  kind: "index" | "search" | "benchmark";
  label: string;
  movie_id: number | null;
  state: JobState;
  stage: string | null;
  completed_units: number;
  total_units: number;
  progress: number;
  fingerprints: number;
  frames_decoded: number;
  workers_used: number;
  worker_pids: number[];
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  elapsed_ms: number | null;
  error: string | null;
  cancel_requested: boolean;
  search_id: string | null;
  details: Record<string, unknown>;
}

export interface ImportResult {
  movie: Movie;
  job: Job | null;
  created: boolean;
  message: string;
}

export interface Hypothesis {
  movie_id: number;
  title: string;
  year: number | null;
  movie_duration_ms: number | null;
  has_thumbnail: boolean;
  start_ms: number;
  end_ms: number;
  start_hms: string;
  end_hms: string;
  score: number;
  base_similarity: number;
  stability: number;
  coverage: number;
  span_ratio: number;
  matched_frames: number;
  expected_frames: number;
  mean_distance: number;
  offset_std_ms: number;
  order_rejected: number;
  votes: number;
  aligned: [number, number, number][]; // [t_clip_ms, t_film_ms, distance]
}

export interface SearchResult {
  status?: SearchStatus;
  reason?: string;
  margin?: number | null;
  informative_frames?: number;
  total_frames?: number;
  best?: Hypothesis | null;
  candidates?: Hypothesis[];
  clip?: { name: string; duration_ms: number; width: number; height: number; fps: number; codec: string; frames: number; sample_step_ms: number };
  candidate_stats?: { method: string; query_frames: number; probes: number; rows_scanned: number; unique_fingerprints: number; candidates: number; elapsed_ms: number };
  timings?: Record<string, number>;
  method?: string;
  params?: { max_hamming: number; accept_score: number; min_coverage: number; ambiguity_ratio: number };
  error?: string;
}

export interface Search {
  id: string;
  job_id: string;
  status: SearchStatus;
  clip_name: string;
  method: string;
  query_duration_ms: number | null;
  best_movie_id: number | null;
  start_ms: number | null;
  end_ms: number | null;
  score: number | null;
  elapsed_ms: number | null;
  created_at: string;
  result: SearchResult;
  job: Job | null;
}

export interface Health {
  status: "ok" | "degraded";
  app: string;
  index_version: string;
  database: { ok: boolean; error: string | null; writer_alive: boolean };
  ffmpeg: { available: boolean; version?: string; error?: string };
  concurrency: {
    process_workers: number;
    worker_pids: number[];
    io_threads: number;
    max_active_index_jobs: number;
    max_active_search_jobs: number;
    cpu_count: number;
    accepting_jobs: boolean;
  };
  limits: { max_upload_gb: number; max_clip_mb: number; query_duration_s: [number, number]; disk_free_gb: number };
  python: string;
  platform: string;
}

export interface Stats {
  movies_total: number;
  movies_indexed: number;
  indexed_duration_ms: number;
  fingerprints_total: number;
  movies_by_status: Record<string, number>;
  searches_total: number;
  recent_searches: {
    id: string;
    status: SearchStatus;
    clip_name: string;
    score: number | null;
    start_ms: number | null;
    best_movie_id: number | null;
    best_title: string | null;
    created_at: string;
  }[];
  active_jobs: Job[];
  process_workers: number;
}

export interface BenchIndexRow {
  mode: "sequential" | "parallel";
  process_count: number;
  label: string;
  elapsed_ms_median: number;
  elapsed_ms_all: number[];
  write_ms_median: number;
  speedup: number;
  efficiency: number;
  frames_per_s: number;
  fingerprints_per_s: number;
  peak_ram_mb: number;
  cpu_percent_avg: number;
  busy_cores_avg: number;
  distinct_pids: number[];
  identical_to_sequential: boolean;
  pool_startup_ms: number;
}

export interface RecallPoint {
  probe_radius: number;
  distance: number;
  recall: number;
  band_ms: number;
  rows_scanned: number;
}

export interface BenchRun {
  run_id: string;
  created_at: string;
  dataset_id: string;
  index: BenchIndexRow[];
  corpus?: { label: string; duration_ms: number }[];
  segments?: number;
  machine: Machine | null;
  recall: {
    available: boolean;
    samples?: number;
    index_size?: number;
    curve?: RecallPoint[];
    exhaustive_ms_same_queries?: number;
    search?: { method: string; clips: number; correct: number; search_ms_mean: number; search_ms_median: number }[];
    scaling?: { distractors: number; index_fingerprints: number; bands_ms_median: number; exhaustive_ms_median: number; bands_correct: string; exhaustive_correct: string }[];
  } | null;
}

export interface Machine {
  cpu: string;
  physical_cores: number;
  logical_cpus: number;
  ram_total_gb: number;
  ram_available_gb: number;
  os: string;
  python: string;
  ffmpeg: string;
  engine: Record<string, string | number>;
}
