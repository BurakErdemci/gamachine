import Head from "next/head";
import { ChevronRight, Clock, FolderOpen, LogOut } from "lucide-react";
import { motion } from "framer-motion";
import { useLang } from '../../lib/i18n';
import { displayName } from '../../lib/displayName';


interface WorkspaceScreenProps {
  userName: string;
  lastWorkspacePath: string | null;
  onOpenWorkspaceDialog: () => Promise<void>;
  onSelectLastWorkspace: () => Promise<void>;
  onLogout: () => void;
}


export const WorkspaceScreen = ({
  userName,
  lastWorkspacePath,
  onOpenWorkspaceDialog,
  onSelectLastWorkspace,
  onLogout,
}: WorkspaceScreenProps) => {
  const { t } = useLang();
  return (
  <div className="h-screen flex items-center justify-center bg-[var(--shell-bg)] text-[var(--shell-text)]">
    <Head><title>Gamachine | Workspace</title></Head>
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      className="bg-[var(--menu-bg)] p-10 rounded-2xl border border-[var(--shell-line)] w-[480px] shadow-2xl"
    >
      <div className="flex flex-col items-center gap-4 mb-8 text-center">
        <div className="bg-[var(--accent)] text-[var(--accent-ink)] p-4 rounded-2xl shadow-lg">
          <FolderOpen size={36} />
        </div>
        <div>
          <h1 className="text-xl font-extrabold tracking-tight">
            {/* No name yet (local login): "Welcome," with nothing after it read as a broken sentence. */}
            {displayName(userName)
              ? <>{t('workspace.welcome')} <span className="text-[var(--accent)]">{displayName(userName)}</span></>
              : t('workspace.welcomeNoName')}
          </h1>
          <p className="text-[var(--shell-text-dim)] text-[11px] font-medium mt-1">
            {t('workspace.subtitle')}
          </p>
        </div>
      </div>

      <div className="space-y-3">
        <button
          onClick={onOpenWorkspaceDialog}
          className="btn btn-primary w-full flex items-center justify-center gap-2.5 p-4 rounded-xl font-bold text-sm tracking-wide transition-all active:scale-[0.98]"
        >
          <FolderOpen size={18} />
          {t('workspace.selectFolder')}
        </button>

        {lastWorkspacePath && (
          <button
            onClick={onSelectLastWorkspace}
            className="w-full flex items-center gap-3 bg-[var(--shell-bg)] hover:bg-[var(--shell-bg-active)] border border-[var(--shell-line)] p-4 rounded-xl transition-all group"
          >
            <div className="bg-[var(--menu-bg)] p-2 rounded-lg transition-colors">
              <Clock size={16} className="text-[var(--shell-text-dim)] group-hover:text-[var(--accent)]" />
            </div>
            <div className="text-left flex-1 min-w-0">
              <p className="text-[10px] text-[var(--shell-text-dim)] font-semibold uppercase tracking-wider">{t('workspace.recentLabel')}</p>
              <p className="text-[12px] text-[var(--shell-text)] font-medium truncate mt-0.5">
                {lastWorkspacePath.split('/').slice(-2).join('/')}
              </p>
            </div>
            <ChevronRight size={14} className="text-[var(--shell-text-dim)] group-hover:text-[var(--accent)]" />
          </button>
        )}

        <button
          onClick={onLogout}
          className="w-full flex items-center justify-center gap-2 text-[11px] font-medium text-[var(--shell-text-dim)] hover:text-[var(--accent)] transition-colors py-3 mt-2"
        >
          <LogOut size={13} />
          {t('workspace.logout')}
        </button>
      </div>
    </motion.div>
  </div>
  );
};
