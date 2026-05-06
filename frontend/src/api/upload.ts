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

export async function uploadUrl(url: string, token: string): Promise<UploadUrlResult> {
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
    throw new Error(err.detail || 'URL 擷取失敗');
  }
  return res.json();
}

// 向下相容舊程式碼
export type UploadResult = UploadFileResult;
