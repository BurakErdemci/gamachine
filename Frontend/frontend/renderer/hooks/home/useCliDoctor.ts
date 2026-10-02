import { useCallback, useState } from 'react';

import { apiHataMesaji } from '../../lib/apiError';
import type { TKey, TValues } from '../../lib/i18n';
import type { CliDoctor, CliGroupDef } from '../../components/home/providerGroups';

type Toast = (msg: string, type: 'success' | 'error' | 'warning' | 'info') => void;

interface Options {
  API: string;
  /** The axios instance to call through (the model menu receives one as a prop). */
  http: { get: (...a: any[]) => Promise<any>; post: (...a: any[]) => Promise<any> };
  token?: string | null;
  showToast: Toast;
  t: (key: TKey, values?: TValues) => string;
}

/**
 * The subscription CLIs' install and sign-in state (`/cli-doctor`) and the two actions that fix
 * it (`/cli-install/{cli}`, `/cli-login/{cli}`). The model menu and the settings screen's
 * "Modeller ve hesaplar" page both read it, so they cannot disagree about a CLI.
 */
export function useCliDoctor({ API, http, token, showToast, t }: Options) {
  const [doctor, setDoctor] = useState<CliDoctor | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [busyCli, setBusyCli] = useState<string | null>(null);
  const headers = { 'X-Session-Token': token ?? '' };

  const fetchDoctor = useCallback(async (refresh = false) => {
    if (!API) return;
    if (refresh) setRefreshing(true);
    try {
      const res = await http.get(`${API}/cli-doctor${refresh ? '?refresh=true' : ''}`, { headers });
      setDoctor(res?.data || {});
      if (refresh) showToast(t('models.doctorRefreshed'), 'success');
    } catch {
      if (refresh) showToast(t('models.doctorFailed'), 'error');
    } finally {
      if (refresh) setRefreshing(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [API, http, token, showToast, t]);

  const installCli = async (g: CliGroupDef) => {
    setBusyCli(g.key);
    try {
      await http.post(`${API}/cli-install/${g.availKey}`, null, { headers });
      showToast(t('models.installStarted'), 'info');
    } catch (e: any) {
      showToast(apiHataMesaji(e, t('models.installFailed')), 'error');
    } finally { setBusyCli(null); }
  };

  const loginCli = async (g: CliGroupDef) => {
    setBusyCli(g.key);
    try {
      await http.post(`${API}/cli-login/${g.availKey}`, null, { headers });
      showToast(t('models.loginStarted'), 'info');
    } catch (e: any) {
      showToast(apiHataMesaji(e, t('models.loginFailed')), 'error');
    } finally { setBusyCli(null); }
  };

  /** Unknown until the doctor answered: no warning is shown before that. */
  const isInstalled = (g: CliGroupDef): boolean => !doctor || doctor[g.availKey]?.installed !== false;
  const needsLogin = (g: CliGroupDef): boolean =>
    !!doctor && doctor[g.availKey]?.installed === true && doctor[g.availKey]?.loggedIn === false;
  const isLoggedIn = (g: CliGroupDef): boolean | null =>
    !doctor ? null : doctor[g.availKey]?.installed === true && doctor[g.availKey]?.loggedIn !== false ? true : false;

  return { doctor, fetchDoctor, refreshing, busyCli, installCli, loginCli, isInstalled, needsLogin, isLoggedIn };
}
