import React, { useEffect, useId, useState } from 'react';
import axios from 'axios';
import { useLang, type TKey } from '../../lib/i18n';

export interface ImportedSession {
  chat_id: number;
  workspace: string;
  title?: string;
  model_name?: string;
  imported?: number;
  total?: number;
  modified_recently?: boolean;
}

interface SessionPreview {
  session_id: string;
  cwd: string;
  cwd_exists?: boolean;
  title?: string;
  total: number;
  importable?: number;
  modified_recently?: boolean;
}

interface Props {
  open: boolean;
  onClose: () => void;
  onImported: (result: ImportedSession) => void | Promise<void>;
  API: string;
  token: string;
  /** The open project's folder; a session from another folder switches the project on import. */
  currentPath?: string | null;
}

/** The backend's error code (400/404/409 body) or '' when the failure has none. */
const errorCode = (err: any): string => {
  const e = err?.response?.data?.error;
  return typeof e === 'string' ? e : '';
};

/** Controlled dialog: the id is previewed first, then imported with a second press of [Aktar]. */
export const ImportSessionDialog: React.FC<Props> = ({ open, onClose, onImported, API, token, currentPath }) => {
  const { t } = useLang();
  const labelId = useId();
  const [value, setValue] = useState('');
  const [preview, setPreview] = useState<SessionPreview | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (open) { setValue(''); setPreview(null); setError(''); setBusy(false); }
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape' && !busy) onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, busy, onClose]);

  if (!open) return null;

  const id = value.trim();
  const headers = { 'X-Session-Token': token };
  const fail = (err: any, cwd?: string) => {
    const code = errorCode(err);
    const key: TKey = code === 'bad_session_id' ? 'importSession.errBadId'
      : code === 'session_not_found' ? 'importSession.errNotFound'
      : code === 'cwd_missing' ? 'importSession.errCwdMissing'
      : code === 'cwd_unsupported' ? 'importSession.cwdUnsupported'
      : 'importSession.errGeneric';
    setError(t(key, { cwd: err?.response?.data?.cwd ?? cwd ?? '' }));
  };

  const go = async () => {
    if (!id || busy) return;
    setBusy(true);
    setError('');
    try {
      if (!preview || preview.session_id !== id) {
        const res = await axios.get(`${API}/claude-sessions/${encodeURIComponent(id)}/preview`, { headers });
        const p = res.data as SessionPreview;
        if (p.importable === 0) { setPreview(null); setError(t('importSession.empty')); return; }
        if (p.cwd_exists === false) { setPreview(null); setError(t('importSession.errCwdMissing', { cwd: p.cwd })); return; }
        setPreview({ ...p, session_id: id });
        return;
      }
      const res = await axios.post(`${API}/claude-sessions/import`, { session_id: id }, { headers });
      await onImported(res.data as ImportedSession);
    } catch (err) {
      fail(err, preview?.cwd);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="confirm-veil" onClick={() => { if (!busy) onClose(); }}>
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={labelId}
        className="confirm"
        data-testid="import-session-dialog"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="confirm-body">
          <div style={{ flex: 1, minWidth: 0 }}>
            <p id={labelId} className="confirm-msg">{t('importSession.title')}</p>
            <label className="set-field" style={{ marginTop: 10 }}>
              <span>{t('importSession.label')}</span>
              <input
                className="set-input set-input-mono"
                data-testid="import-session-input"
                autoFocus
                value={value}
                disabled={busy}
                onChange={(e) => { setValue(e.target.value); setPreview(null); setError(''); }}
                onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); void go(); } }}
              />
            </label>
            <p className="menu-row-d">{t('importSession.hint')}</p>

            {preview && (
              <div data-testid="import-session-preview" style={{ marginTop: 10 }}>
                <p className="menu-row-t">{preview.title || preview.session_id}</p>
                <p className="menu-row-d">{t('importSession.folder')}: {preview.cwd}</p>
                <p className="menu-row-d">{t('importSession.count', { n: preview.total })}</p>
                {currentPath && preview.cwd !== currentPath && (
                  <p className="menu-row-d" data-testid="import-session-switch">
                    {t('importSession.switch', { cwd: preview.cwd })}
                  </p>
                )}
                {preview.modified_recently && (
                  <p className="menu-row-d" role="alert" data-testid="import-session-recent">
                    {t('importSession.recent')}
                  </p>
                )}
              </div>
            )}
            {error && <p className="menu-row-d" role="alert" data-testid="import-session-error">{error}</p>}
          </div>
        </div>
        <div className="confirm-actions">
          <button type="button" className="btn btn-ghost confirm-no" disabled={busy} onClick={onClose}>
            {t('importSession.cancel')}
          </button>
          <button
            type="button"
            className="btn btn-primary confirm-go"
            data-testid="import-session-go"
            disabled={busy || !id}
            onClick={() => { void go(); }}
          >
            {t('importSession.go')}
          </button>
        </div>
      </div>
    </div>
  );
};
