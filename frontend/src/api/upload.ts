const BASE = 'http://localhost:8000';

export interface UploadResult {
  file_id: string;
  filename: string;
  size: number;
  mime_type: string;
}

export async function uploadFile(file: File, token: string): Promise<UploadResult> {
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
