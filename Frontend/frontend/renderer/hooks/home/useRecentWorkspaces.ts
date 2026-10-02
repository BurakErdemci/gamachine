import { useCallback, useEffect, useRef, useState } from 'react';
import axios from 'axios';

import { hostWorkspacePath } from '../../lib/backendWorkspacePath';

export type RecentStatus = 'ok' | 'missing' | 'untrusted';

export interface RecentWorkspace {
  /** The path as the backend stored it; the key for remove-workspace. */
  backendPath: string;
  /** This process's path, or null when it cannot be mapped (shown as missing). */
  hostPath: string | null;
  name: string;
  lastAccessed: string;
  chatCount: number;
  status: RecentStatus;
  unityVersion: string | null;
}

export type RecentLoad = 'loading' | 'ok' | 'failed';

interface RecentUser { id: number; sessionToken: string }

const getIpc = () => (typeof window !== 'undefined' ? (window as any).ipc : null);

export const folderName = (path: string): string => {
  const parts = path.split(/[\\/]+/).filter(Boolean);
  return parts.length ? parts[parts.length - 1] : path;
};

export function useRecentWorkspaces(api: string, user: RecentUser | null) {
  const [items, setItems] = useState<RecentWorkspace[]>([]);
  const [load, setLoad] = useState<RecentLoad>('loading');
  // A reload after a remove can overlap the first load; only the newest one may commit.
  const seq = useRef(0);

  const refresh = useCallback(async () => {
    if (!api || !user) return;
    const mine = ++seq.current;
    try {
      const res = await axios.get(`${api}/recent-workspaces/${user.id}`, {
        params: { limit: 12 },
        headers: { 'X-Session-Token': user.sessionToken },
      });
      const rows: any[] = Array.isArray(res.data?.workspaces) ? res.data.workspaces : [];
      const hosts = await Promise.all(rows.map(r => hostWorkspacePath(String(r.path ?? ''))));
      const known = hosts.filter((h): h is string => !!h);
      let info: Record<string, { status: RecentStatus; unityVersion: string | null }> = {};
      let defaultStatus: RecentStatus = 'ok';
      const ipc = getIpc();
      if (known.length && ipc) {
        try {
          const list = await ipc.invoke('workspace-info', known);
          if (Array.isArray(list)) {
            for (const i of list) {
              if (i && typeof i.path === 'string') {
                const status: RecentStatus = i.status === 'missing' || i.status === 'untrusted' ? i.status : 'ok';
                info[i.path] = { status, unityVersion: typeof i.unityVersion === 'string' ? i.unityVersion : null };
              }
            }
          } else {
            defaultStatus = 'untrusted';
          }
        } catch {
          defaultStatus = 'untrusted';
        }
      }
      if (mine !== seq.current) return;
      setItems(rows.map((r, n) => {
        const backendPath = String(r.path ?? '');
        const hostPath = hosts[n] || null;
        const facts = hostPath ? info[hostPath] : undefined;
        return {
          backendPath,
          hostPath,
          name: folderName(hostPath || backendPath),
          lastAccessed: String(r.last_accessed ?? ''),
          chatCount: Number(r.chat_count) > 0 ? Number(r.chat_count) : 0,
          status: hostPath ? (facts?.status ?? defaultStatus) : 'missing',
          unityVersion: facts?.unityVersion ?? null,
        };
      }));
      setLoad('ok');
    } catch {
      if (mine === seq.current) setLoad('failed');
    }
  }, [api, user]);

  useEffect(() => { refresh(); }, [refresh]);

  const remove = useCallback(async (item: RecentWorkspace): Promise<boolean> => {
    if (!api || !user) return false;
    try {
      const res = await axios.post(`${api}/remove-workspace`,
        { user_id: user.id, path: item.backendPath },
        { headers: { 'X-Session-Token': user.sessionToken } });
      await refresh();
      return !!res.data?.removed;
    } catch {
      return false;
    }
  }, [api, user, refresh]);

  return { items, load, refresh, remove };
}
