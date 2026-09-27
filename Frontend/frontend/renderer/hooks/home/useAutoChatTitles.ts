import { useCallback, useEffect, useRef, useState } from 'react';
import axios from 'axios';
import { cevir } from '../../lib/i18n';

type ShowToast = (message: string, type?: 'success' | 'error' | 'warning' | 'info') => void;

/** The "Auto chat titles" setting. The backend owns it (app_settings), because
 *  the title is decided there after a turn ends, also for mail wake turns. */
export function useAutoChatTitles(API: string, userId: number | null | undefined, showToast?: ShowToast) {
  const [autoTitles, setAutoTitles] = useState(true);
  const [saving, setSaving] = useState(false);
  // A toggle before the first read answered must not be undone by that answer.
  const touchedRef = useRef(false);

  useEffect(() => {
    if (!API || userId == null) return;
    let live = true;
    axios.get(`${API}/chat-title-setting`)
      .then(res => {
        if (live && !touchedRef.current && typeof res.data?.enabled === 'boolean') setAutoTitles(res.data.enabled);
      })
      .catch(() => { /* default stays on; the backend defaults to on too */ });
    return () => { live = false; };
  }, [API, userId]);

  const toggleAutoTitles = useCallback(async () => {
    if (!API || saving) return;
    touchedRef.current = true;
    const next = !autoTitles;
    setAutoTitles(next);
    setSaving(true);
    try {
      await axios.post(`${API}/chat-title-setting`, { enabled: next });
    } catch {
      setAutoTitles(!next);
      showToast?.(cevir('settings.autoTitlesFailed'), 'error');
    } finally {
      setSaving(false);
    }
  }, [API, autoTitles, saving, showToast]);

  return { autoTitles, autoTitlesSaving: saving, toggleAutoTitles };
}
