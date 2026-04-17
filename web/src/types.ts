export type FileStatus =
  | 'extracting'
  | 'extracted'
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
  format: 'srt' | 'vtt'
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
  detected_lang?: string
  error?: string
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
