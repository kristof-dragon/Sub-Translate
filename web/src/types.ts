export type FileStatus =
  | 'extracting'
  | 'extracted'
  | 'ocr_queued'
  | 'ocr_running'
  | 'ocr_done'
  | 'ocr_error'
  | 'queued'
  | 'detecting'
  | 'translating'
  | 'done'
  | 'error'

export interface Project {
  id: number
  name: string
  description: string
  default_target_lang: string
  default_model: string
  created_at: string
  file_count: number
}

export interface SubtitleFile {
  id: number
  project_id: number
  original_filename: string
  // 'srt' | 'vtt' for native text subtitles, plus bitmap codecs like 'sup'
  // (PGS) before OCR runs. After OCR succeeds the row flips to 'srt'.
  format: string
  detected_lang: string
  target_lang: string
  model: string
  status: FileStatus
  progress_pct: number
  error: string
  created_at: string
  translated_available: boolean
  // Display name of the translated file on disk, with the {id}_ prefix stripped.
  // Empty until translation finishes. User-editable via PATCH /files/:id/rename.
  translated_filename: string
  // Absolute path (media-root-relative in the UI) of the video the subtitle
  // was extracted from. "" for drag-and-drop uploads.
  source_video_path: string
  // "pgs" if the file went through bitmap OCR, "" for native text subtitles.
  // Lets the UI label OCR-origin rows and decide whether retry-OCR applies.
  source_format: string
  // Independent 0–100 counter for the OCR phase. Distinct from progress_pct
  // (translation) so both phases render without overwriting each other.
  ocr_progress_pct: number
}

export interface AppSettings {
  ollama_url: string
  ollama_api_key_set: boolean
  default_model: string
  chunk_size: number
  disable_thinking: boolean
  request_timeout: number // seconds
  num_ctx: number // 0 = don't send (use model default)
  context_sent: boolean // derived: true when num_ctx > 0
  ocr_llm_cleanup: boolean
  ocr_llm_model: string // empty = fall back to default_model
}

export interface OllamaModel {
  name: string
  size?: number
  modified_at?: string
}

export interface Language {
  code: string
  name: string
}

export interface EventMessage {
  file_id: number
  status?: FileStatus
  progress_pct?: number
  ocr_progress_pct?: number
  detected_lang?: string
  error?: string
  // Sent by the OCR worker when format flips from "pgs" to "srt" after a
  // successful OCR run, so the UI can re-render the row's format pill.
  format?: string
}

export interface BrowseEntry {
  name: string
  is_dir: boolean
  is_video: boolean
  size: number | null
}

export interface BrowseResponse {
  path: string // relative to media root, '' == root
  parent: string | null // null when already at root
  entries: BrowseEntry[]
}

export interface VideoTrack {
  id: number // absolute ffmpeg stream index (use directly with -map 0:<id>)
  codec: string
  codec_id: string
  language: string
  name: string
  ext: string | null
  supported: boolean
}

export interface OllamaHealth {
  configured: boolean
  ok: boolean
  model_count?: number
  error?: string
}

export interface ExportItem {
  file_id: number
  name: string
  path: string // media-root-relative
  reason?: string // only present on skipped items
}

export interface ExportResult {
  written: ExportItem[]
  skipped: ExportItem[]
}
