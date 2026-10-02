import axios from 'axios';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { aktifDilAyarla } from '../renderer/lib/i18n';

afterEach(() => {
  aktifDilAyarla(null);
  axios.defaults.headers.common['X-UI-Lang'] = 'en';
});

describe('UI language request header', () => {
  it('defaults to English before a language is announced', async () => {
    // Other suites announce languages; exercise a fresh module initialization.
    vi.resetModules();
    await import('../renderer/lib/i18n');
    const { default: freshAxios } = await import('axios');
    expect(freshAxios.defaults.headers.common['X-UI-Lang']).toBe('en');
  });

  it('follows language changes and keeps the last header for null', () => {
    aktifDilAyarla('tr');
    expect(axios.defaults.headers.common['X-UI-Lang']).toBe('tr');
    aktifDilAyarla(null);
    expect(axios.defaults.headers.common['X-UI-Lang']).toBe('tr');
    aktifDilAyarla('en');
    expect(axios.defaults.headers.common['X-UI-Lang']).toBe('en');
    aktifDilAyarla(null);
    expect(axios.defaults.headers.common['X-UI-Lang']).toBe('en');
  });
});
