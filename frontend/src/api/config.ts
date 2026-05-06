import { getApiBase } from './apiBase';
import type { ProviderType } from '../types/messages';

export async function fetchDefaultProvider(): Promise<ProviderType> {
  try {
    const res = await fetch(`${getApiBase()}/config`);
    if (!res.ok) return 'claude';
    const data = await res.json();
    return (data.default_provider as ProviderType) ?? 'claude';
  } catch {
    return 'claude';
  }
}
