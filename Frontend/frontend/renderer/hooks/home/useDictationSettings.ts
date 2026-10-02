import { useCallback, useEffect, useRef, useState } from 'react';
import axios from 'axios';
import { cevir } from '../../lib/i18n';

type ShowToast = (message: string, type?: 'success' | 'error' | 'warning' | 'info') => void;

/** "Detect language automatically" for dictation on a CPU-only machine. The
 *  backend owns it (app_settings) because the backend picks the language when
 *  it transcribes; on a GPU it always detects, whatever this says. */
export function useDictationSettings(API: string, userId: number | null | undefined, showToast?: ShowToast) {
  const [autoLanguageCpu, setAutoLanguageCpu] = useState(false);
  const [saving, setSaving] = useState(false);
  // A toggle before the first read answered must not be undone by that answer.
  const touchedRef = useRef(false);

  useEffect(() => {
    if (!API || userId == null) return;
    let live = true;
    axios.get(`${API}/transcribe/settings`)
      .then(res => {
        const v = res.data?.auto_language_cpu;
        if (live && !touchedRef.current && typeof v === 'boolean') setAutoLanguageCpu(v);
      })
      .catch(() => { /* off stays off; the backend defaults to off too */ });
    return () => { live = false; };
  }, [API, userId]);

  const toggleAutoLanguageCpu = useCallback(async (): Promise<boolean> => {
    if (!API || saving) return false;
    touchedRef.current = true;
    const next = !autoLanguageCpu;
    setAutoLanguageCpu(next);
    setSaving(true);
    try {
      await axios.post(`${API}/transcribe/settings`, { auto_language_cpu: next });
      return true;
    } catch {
      setAutoLanguageCpu(!next);
      showToast?.(cevir('settings.dictationAutoLangFailed'), 'error');
      return false;
    } finally {
      setSaving(false);
    }
  }, [API, autoLanguageCpu, saving, showToast]);

  return { autoLanguageCpu, autoLanguageCpuSaving: saving, toggleAutoLanguageCpu };
}
