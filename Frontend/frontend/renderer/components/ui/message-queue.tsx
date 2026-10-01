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

// The queue sits above the composer box on the paper, in the strip's small type.
const iconButton = "icon-btn queue-btn";

export function MessageQueue({
    items, paused, onEdit, onDelete, onSendNow, onResume, onEditTake,
}: MessageQueueProps & { onEditTake: (item: QueueItemView) => void }) {
    const { t } = useLang();
    if (items.length === 0) return null;
    return (
        <div data-message-queue className="queue">
            <div className="queue-head">
                <span className="queue-k">
                    {paused ? t('queue.paused') : t('queue.title')} · {items.length}
                </span>
                {paused && (
                    <button
                        type="button"
                        data-queue-resume
                        onClick={onResume}
                        className="strip-item queue-resume"
                    >
                        <Play size={13} aria-hidden="true" />
                        <span>{t('queue.sendNext')}</span>
                    </button>
                )}
            </div>
            <ul className="queue-list custom-scrollbar">
                {items.map(item => {
                    const extra = (item.images?.length ?? 0) + (item.videos?.length ?? 0);
                    const preview = item.draft.replace(/\s+/g, ' ').trim();
                    return (
                        <li key={item.id} data-queue-item className="queue-item">
                            <span className="queue-text" title={item.draft}>
                                {preview || t('queue.attachmentsOnly')}
                                {extra > 0 && <span className="queue-extra"> · {t('queue.attachments', { sayi: extra })}</span>}
                            </span>
                            <button
                                type="button"
                                data-queue-edit
                                onClick={() => { const taken = onEdit(item.id); if (taken) onEditTake(taken); }}
                                className={iconButton}
                                title={t('queue.edit')}
                                aria-label={t('queue.edit')}
                            >
                                <Pencil size={14} aria-hidden="true" />
                            </button>
                            <button
                                type="button"
                                data-queue-send-now
                                onClick={() => onSendNow(item.id)}
                                className={iconButton}
                                title={t('queue.sendNow')}
                                aria-label={t('queue.sendNow')}
                            >
                                <ArrowUpIcon size={14} aria-hidden="true" />
                            </button>
                            <button
                                type="button"
                                data-queue-delete
                                onClick={() => onDelete(item.id)}
                                className={iconButton}
                                title={t('queue.delete')}
                                aria-label={t('queue.delete')}
                            >
                                <XIcon size={14} aria-hidden="true" />
                            </button>
                        </li>
                    );
                })}
            </ul>
        </div>
    );
}
