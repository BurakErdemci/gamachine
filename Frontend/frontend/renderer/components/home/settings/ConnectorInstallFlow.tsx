import React, { useCallback, useEffect, useRef, useState } from 'react';

import { useLang, type TKey } from '../../../lib/i18n';
import {
  INSTALL_STEPS, cancelInstall, formatClock, installStatus, planInstall, startedAtMs, startInstall,
  type ConnectorView, type InstallJob, type InstallPlan, type StepId,
} from '../../../lib/connectors';
import { confirmDialog } from '../../ui/ConfirmDialog';

type Toast = (msg: string, type: 'success' | 'error' | 'warning' | 'info') => void;

const POLL_MS = 1000;

interface Props {
  view: ConnectorView;
  /** The consent step is open (the user pressed Kur / Güncelle / Onar). */
  consent: boolean;
  onConsentClose: () => void;
  /** The connector changed on the backend (install finished, failed or was cancelled): reload the list. */
  onChanged: () => void;
  showToast?: Toast;
}

/**
 * Installing a connector in three calm moments: a plan the user agrees to (what, how big, how long,
 * where), a progress view that survives leaving the page (the job lives in the backend and is read
 * again on mount), and a result with a way forward. Nothing is downloaded before "Kur".
 */
export const ConnectorInstallFlow = ({ view, consent, onConsentClose, onChanged, showToast }: Props) => {
  const { t } = useLang();
  const id = view.id;
  const [job, setJob] = useState<InstallJob | null>(null);
  const [plan, setPlan] = useState<InstallPlan | null>(null);
  const [planState, setPlanState] = useState<'idle' | 'loading' | 'failed'>('idle');
  const [starting, setStarting] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  const seenAt = useRef(Date.now());
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);

  const running = job?.state === 'running' || (job === null && view.state === 'installing');
  const hasPlan = plan !== null;

  // Coming back to the page: pick up a job that is still running (or one that failed) from the backend.
  useEffect(() => {
    void installStatus(id).then(r => {
      if (!alive.current || !r.ok || !r.data.job) return;
      if (r.data.job.state === 'running' || r.data.job.state === 'failed') setJob(r.data.job);
    });
  }, [id]);

  const loadPlan = useCallback(async () => {
    setPlanState('loading');
    const r = await planInstall(id);
    if (!alive.current) return;
    if (r.ok) { setPlan(r.data); setPlanState('idle'); } else setPlanState('failed');
  }, [id]);
  // The plan is asked for when the consent opens, and for a job found running (estimate and steps).
  useEffect(() => { if (consent || (running && !hasPlan)) void loadPlan(); }, [consent, running, hasPlan, loadPlan]);

  // One status read about every second while a job runs; a finished job ends the loop.
  useEffect(() => {
    if (!running) return;
    let stop = false;
    let timer: ReturnType<typeof setTimeout>;
    const tick = async () => {
      const r = await installStatus(id);
      if (stop || !alive.current) return;
      const next = r.ok ? r.data.job : null;
      if (next) {
        if (next.state === 'cancelled') { setJob(null); onChanged(); return; }
        setJob(next);
        if (next.state !== 'running') { onChanged(); return; }
      } else if (r.ok) {
        onChanged();
      }
      timer = setTimeout(tick, POLL_MS);
    };
    timer = setTimeout(tick, POLL_MS);
    return () => { stop = true; clearTimeout(timer); };
  }, [running, id, onChanged]);

  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => setNow(Date.now()), POLL_MS);
    return () => clearInterval(timer);
  }, [running]);

  const begin = async () => {
    if (starting) return;
    setStarting(true);
    try {
      const r = await startInstall(id, !!plan?.replace_addon);
      if (!alive.current) return;
      if (r.ok) {
        seenAt.current = Date.now();
        setNow(Date.now());
        setJob(r.data.job);
        onConsentClose();
        onChanged();
      } else if ("status" in r && r.status === 409) {
        // A job is already running (another window): show it instead of failing.
        onConsentClose();
        onChanged();
        const s = await installStatus(id);
        if (alive.current && s.ok && s.data.job) setJob(s.data.job);
      } else {
        showToast?.(t('conn.installStartFailed'), 'error');
      }
    } finally {
      if (alive.current) setStarting(false);
    }
  };

  const cancel = async () => {
    if (!(await confirmDialog(t('conn.progress.cancelConfirm'), t('conn.progress.cancelYes'), t('conn.progress.cancelNo')))) return;
    const r = await cancelInstall(id);
    if (!alive.current) return;
    if (!r.ok) showToast?.(t('conn.actionFailed'), 'error');
    else if (r.data.cancelled) { setJob(null); onChanged(); }
  };

  if (consent && !running) {
    return (
      <div className="set-row" role="group" aria-label={t('conn.plan.title')} data-testid={`conn-plan-${id}`}>
        <div className="set-rt">
          <p className="set-name">{t('conn.plan.title')}</p>
          {planState === 'loading' && <p className="set-hint" role="status">{t('conn.plan.loading')}</p>}
          {planState === 'failed' && (
            <p className="set-hint" role="alert" data-testid={`conn-plan-failed-${id}`}>
              {t('conn.plan.failed')}{' '}
              <button type="button" className="set-link" onClick={() => void loadPlan()}>{t('conn.act.retry')}</button>
            </p>
          )}
          {planState === 'idle' && plan && (
            <ul className="set-hint" data-testid={`conn-plan-facts-${id}`}>
              <li>{t('conn.plan.what', { name: view.name })}</li>
              <li data-testid={`conn-plan-size-${id}`}>{t('conn.plan.size', { mb: plan.download_mb })}</li>
              <li data-testid={`conn-plan-time-${id}`}>{t('conn.plan.time', { time: formatClock(plan.estimated_s) })}</li>
              <li data-testid={`conn-plan-target-${id}`}>{t('conn.plan.target', { target: plan.target })}</li>
              {plan.replace_addon && <li data-testid={`conn-plan-replace-${id}`}>{t('conn.plan.replaceAddon')}</li>}
              <li>{t('conn.plan.note')}</li>
            </ul>
          )}
        </div>
        <div className="set-actions">
          <button type="button" className="btn btn-primary btn-sm" onClick={() => void begin()}
            disabled={starting || planState !== 'idle' || !plan} data-testid={`conn-plan-go-${id}`}>
            {t('conn.plan.start')}
          </button>
          <button type="button" className="btn btn-ghost btn-sm" onClick={onConsentClose} disabled={starting}
            data-testid={`conn-plan-cancel-${id}`}>
            {t('conn.plan.cancel')}
          </button>
        </div>
      </div>
    );
  }

  if (running) return <Progress view={view} job={job} plan={plan} now={now} seenAt={seenAt.current} onCancel={() => void cancel()} />;

  if (job?.state === 'failed') {
    return (
      <div className="set-row" role="alert" data-testid={`conn-failed-${id}`}>
        <div className="set-rt">
          <p className="set-name">{t('conn.failed')}</p>
          <p className="set-hint">{job.error?.message || t('conn.failedGeneric')}</p>
        </div>
        <button type="button" className="btn btn-primary btn-sm" onClick={() => void begin()} disabled={starting}
          data-testid={`conn-retry-${id}`}>
          {t('conn.act.retry')}
        </button>
      </div>
    );
  }

  if (job?.state === 'done') {
    return <p className="set-hint" role="status" data-testid={`conn-done-${id}`}>{t('conn.done')}</p>;
  }
  return null;
};

const MARK: Record<'done' | 'current' | 'pending', string> = { done: '✓', current: '→', pending: '·' };

const Progress = ({ view, job, plan, now, seenAt, onCancel }: {
  view: ConnectorView; job: InstallJob | null; plan: InstallPlan | null; now: number; seenAt: number; onCancel: () => void;
}) => {
  const { t } = useLang();
  const steps: readonly StepId[] = plan?.steps?.length ? plan.steps : INSTALL_STEPS;
  const started = job ? startedAtMs(job.started_at) : NaN;
  const elapsed = Math.max(0, (now - (Number.isFinite(started) ? started : seenAt)) / 1000);
  const time = plan
    ? t('conn.progress.elapsed', { elapsed: formatClock(elapsed), estimate: formatClock(plan.estimated_s) })
    : t('conn.progress.elapsedOnly', { elapsed: formatClock(elapsed) });
  const mb = job?.downloaded_mb;
  return (
    <div className="set-row" data-testid={`conn-progress-${view.id}`}>
      <div className="set-rt">
        <p className="set-name">{t('conn.progress.title')}</p>
        <ol className="set-hint" aria-label={t('conn.progress.stepsAria')}>
          {steps.map(step => {
            const status = job?.steps_done.includes(step) ? 'done' : job?.step === step ? 'current' : 'pending';
            return (
              <li key={step} data-testid={`conn-step-${view.id}-${step}`} data-status={status}
                aria-current={status === 'current' ? 'step' : undefined}>
                <span aria-hidden="true">{MARK[status]}</span> {t(`conn.step.${step}` as TKey)}
                {' '}({t(`conn.stepState.${status}` as TKey)})
              </li>
            );
          })}
        </ol>
        <p className="set-hint" data-testid={`conn-time-${view.id}`}>{time}</p>
        {mb != null && (
          <p className="set-hint" data-testid={`conn-downloaded-${view.id}`}>
            {plan ? t('conn.progress.downloadedOf', { mb, total: plan.download_mb }) : t('conn.progress.downloaded', { mb })}
          </p>
        )}
      </div>
      <button type="button" className="btn btn-ghost btn-sm" onClick={onCancel} data-testid={`conn-cancel-${view.id}`}>
        {t('conn.progress.cancel')}
      </button>
    </div>
  );
};
