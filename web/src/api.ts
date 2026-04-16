import type {
  AppSettings,
  BrowseResponse,
  Language,
  OllamaHealth,
  OllamaModel,
  Project,
  SubtitleFile,
  VideoTrack,
} from './types'

const BASE = '/api'

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`${BASE}${path}`, init)
  if (!res.ok) {
    const body = await res.text().catch(() => '')
    throw new Error(body || `${res.status} ${res.statusText}`)
  }
  if (res.status === 204) return undefined as unknown as T
  const ct = res.headers.get('content-type') || ''
  if (ct.includes('application/json')) return res.json() as Promise<T>
  return (await res.text()) as unknown as T
}

const jsonInit = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

export const api = {
  listProjects: () => request<Project[]>('/projects'),
  getProject: (id: number) => request<Project>(`/projects/${id}`),
  createProject: (data: Partial<Project>) =>
    request<Project>('/projects', jsonInit('POST', data)),
  updateProject: (id: number, data: Partial<Project>) =>
    request<Project>(`/projects/${id}`, jsonInit('PATCH', data)),
  deleteProject: (id: number) =>
    request<void>(`/projects/${id}`, { method: 'DELETE' }),

  listFiles: (pid: number) => request<SubtitleFile[]>(`/projects/${pid}/files`),
  getFile: (id: number) => request<SubtitleFile>(`/files/${id}`),
  deleteFile: (id: number) => request<void>(`/files/${id}`, { method: 'DELETE' }),
  downloadUrl: (id: number) => `${BASE}/files/${id}/download`,

  uploadFiles: async (
    pid: number,
    files: File[],
    target_lang: string,
    model?: string,
  ): Promise<SubtitleFile[]> => {
    const fd = new FormData()
    for (const f of files) fd.append('files', f)
    fd.append('target_lang', target_lang)
    if (model) fd.append('model', model)
    const res = await fetch(`${BASE}/projects/${pid}/files`, {
      method: 'POST',
      body: fd,
    })
    if (!res.ok) throw new Error((await res.text()) || res.statusText)
    return res.json()
  },

  browse: (path = '') =>
    request<BrowseResponse>(`/browse?path=${encodeURIComponent(path)}`),
  videoTracks: (path: string) =>
    request<{ tracks: VideoTrack[] }>(
      `/video/tracks?path=${encodeURIComponent(path)}`,
    ),
  extractTracks: (
    pid: number,
    body: { video_path: string; track_ids: number[] },
  ) =>
    request<SubtitleFile[]>(`/projects/${pid}/extract`, jsonInit('POST', body)),

  translateFile: (
    fid: number,
    body: { target_lang: string; model?: string },
  ) => request<SubtitleFile>(`/files/${fid}/translate`, jsonInit('POST', body)),

  getSettings: () => request<AppSettings>('/settings'),
  updateSettings: (data: Partial<AppSettings & { ollama_api_key: string }>) =>
    request<AppSettings>('/settings', jsonInit('PUT', data)),
  listModels: () => request<{ models: OllamaModel[] }>('/settings/models'),
  listLanguages: () => request<Language[]>('/settings/languages'),
  ollamaHealth: () => request<OllamaHealth>('/settings/ollama-health'),
}
