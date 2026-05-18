import { getApiBase } from './apiBase';

const BASE = getApiBase();

export interface UploadFileResult {
  file_id: string;
  filename: string;
  size: number;
  mime_type: string;
}

export interface UploadUrlResult {
  file_id: string;
  title: string;
  url: string;
  char_count: number;
}

export interface YoutubeAsrRequired {
  asr_required: true;
  video_id: string;
  url: string;
  title: string;
  reason: string;
}

export type UploadUrlResponse = UploadUrlResult | YoutubeAsrRequired;

export type YoutubeAsrEvent =
  | { type: 'progress'; stage: 'download' | 'transcribe'; progress: number }
  | { type: 'done'; file_id: string; title: string; url: string; char_count: number }
  | { type: 'error'; message: string };

export async function uploadFile(file: File, token: string): Promise<UploadFileResult> {
  const form = new FormData();
  form.append('file', file);

  const res = await fetch(`${BASE}/upload`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}` },
    body: form,
  });

  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || '檔案上傳失敗');
  }
  return res.json();
}

export async function uploadUrl(url: string, token: string): Promise<UploadUrlResponse> {
  const res = await fetch(`${BASE}/upload/url`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${token}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ url }),
  });

  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    if (err?.asr_required) {
      return err as YoutubeAsrRequired;
    }
    throw new Error(err.detail || 'URL 擷取失敗');
  }
  return res.json();
}

export async function streamYoutubeAsr(
  url: string,
  token: string,
  onEvent?: (evt: YoutubeAsrEvent) => void,
  signal?: AbortSignal,
): Promise<UploadUrlResult> {
  const res = await fetch(`${BASE}/upload/youtube/asr/stream`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${token}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ url }),
    signal,
  });

  if (!res.ok || !res.body) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || 'YouTube 音訊轉寫失敗');
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder('utf-8');
  let buffer = '';

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    // NDJSON by '\n'
    while (true) {
      const nl = buffer.indexOf('\n');
      if (nl < 0) break;
      const line = buffer.slice(0, nl).trim();
      buffer = buffer.slice(nl + 1);
      if (!line) continue;

      let msg: YoutubeAsrEvent;
      try {
        msg = JSON.parse(line) as YoutubeAsrEvent;
      } catch {
        continue;
      }
      if (!msg) continue;
      onEvent?.(msg);

      if (msg.type === 'done') {
        return {
          file_id: msg.file_id,
          title: msg.title,
          url: msg.url,
          char_count: msg.char_count,
        };
      }
      if (msg.type === 'error') {
        throw new Error(msg.message || 'YouTube 音訊轉寫失敗');
      }
    }
  }

  throw new Error('YouTube 音訊轉寫未完成（連線中斷）');
}

// 向下相容舊程式碼
export type UploadResult = UploadFileResult;
