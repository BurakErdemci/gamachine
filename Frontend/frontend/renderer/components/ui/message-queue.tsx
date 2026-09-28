import * as React from "react";
import { ArrowUpIcon, Pencil, Play, XIcon } from "lucide-react";
import { useLang } from "../../lib/i18n";

/** What the composer needs of one queued message. */
export type QueueItemView = {
    id: number;
    draft: string;
    images?: string[];
    videos?: any[];
};

export type MessageQueueProps = {
    items: QueueItemView[];
    paused: boolean;
    /** Removes the message and returns it, so the composer can take it back. */
    onEdit: (id: number) => QueueItemView | null;
    onDelete: (id: number) => void;
    onSendNow: (id: number) => void;
    /** "Send next" on a paused queue. */
    onResume: () => void;
};

const iconButton = "p-1 rounded text-white/40 hover:text-white/90 hover:bg-white/5 transition-colors";

export function MessageQueue({
    items, paused, onEdit, onDelete, onSendNow, onResume, onEditTake,
}: MessageQueueProps & { onEditTake: (item: QueueItemView) => void }) {
    const { t } = useLang();
    if (items.length === 0) return null;
    return (
        <div data-message-queue className="px-3 pt-2 pb-1 border-b border-white/[0.06]">
            <div className="flex items-center justify-between mb-1">
                <span className="text-[10px] uppercase tracking-wide text-white/40">
                    {paused ? t('queue.paused') : t('queue.title')} · {items.length}
                </span>
                {paused && (
                    <button
                        type="button"
                        data-queue-resume
                        onClick={onResume}
                        className="flex items-center gap-1 px-2 py-0.5 rounded text-[10px] text-white/70 hover:text-white hover:bg-white/5 transition-colors"
                    >
                        <Play className="w-3 h-3" />
                        <span>{t('queue.sendNext')}</span>
                    </button>
                )}
            </div>
            <ul className="space-y-0.5 max-h-[96px] overflow-y-auto custom-scrollbar">
                {items.map(item => {
                    const extra = (item.images?.length ?? 0) + (item.videos?.length ?? 0);
                    const preview = item.draft.replace(/\s+/g, ' ').trim();
                    return (
                        <li key={item.id} data-queue-item className="flex items-center gap-2 text-[12px] text-white/70">
                            <span className="flex-1 min-w-0 truncate" title={item.draft}>
                                {preview || t('queue.attachmentsOnly')}
                                {extra > 0 && <span className="text-white/30"> · {t('queue.attachments', { sayi: extra })}</span>}
                            </span>
                            <button
                                type="button"
                                data-queue-edit
                                onClick={() => { const taken = onEdit(item.id); if (taken) onEditTake(taken); }}
                                className={iconButton}
                                title={t('queue.edit')}
                                aria-label={t('queue.edit')}
                            >
                                <Pencil className="w-3 h-3" />
                            </button>
                            <button
                                type="button"
                                data-queue-send-now
                                onClick={() => onSendNow(item.id)}
                                className={iconButton}
                                title={t('queue.sendNow')}
                                aria-label={t('queue.sendNow')}
                            >
                                <ArrowUpIcon className="w-3 h-3" />
                            </button>
                            <button
                                type="button"
                                data-queue-delete
                                onClick={() => onDelete(item.id)}
                                className={iconButton}
                                title={t('queue.delete')}
                                aria-label={t('queue.delete')}
                            >
                                <XIcon className="w-3 h-3" />
                            </button>
                        </li>
                    );
                })}
            </ul>
        </div>
    );
}
