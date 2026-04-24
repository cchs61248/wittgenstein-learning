const BASE = 'http://localhost:8000';

export interface UploadResult {
  content: string;
  filename: string;
  char_count: number;
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
