import React from 'react';

/**
 * The settings screen's building blocks (mockup round 11): a group heading over a card of rows,
 * a row = name + hint on the left and one control on the right. Styled by styles/gm/settings.css.
 */

export const SetPageHead = ({ title, lede }: { title: string; lede: string }) => (
  <header className="set-head">
    <h1 className="set-h1">{title}</h1>
    <p className="set-lede">{lede}</p>
  </header>
);

export const SetGroup = ({ title, note, action, children, testId }: {
  title?: string; note?: string; action?: React.ReactNode; children: React.ReactNode; testId?: string;
}) => (
  <section className="set-group" data-testid={testId}>
    {title && (
      <h2 className="set-gk">
        {title}
        {note && <span className="set-gk-note">{note}</span>}
        {action}
      </h2>
    )}
    {children}
  </section>
);

export const SetCard = ({ children, className = '' }: { children: React.ReactNode; className?: string }) => (
  <div className={`set-card${className ? ` ${className}` : ''}`}>{children}</div>
);

/** One row. `lead` = the 28 px mark column of provider / hero rows. */
export const SetRow = ({ name, hint, lead, control, className = '', testId, nameLang, extra }: {
  name: React.ReactNode; hint?: React.ReactNode; lead?: React.ReactNode; control?: React.ReactNode;
  className?: string; testId?: string; nameLang?: string; extra?: React.ReactNode;
}) => (
  <div className={`set-row${lead ? ' set-prov' : ''}${className ? ` ${className}` : ''}`} data-testid={testId}>
    {lead}
    <div className="set-rt">
      <p className="set-name" lang={nameLang}>{name}</p>
      {hint != null && hint !== '' && <div className="set-hint">{hint}</div>}
      {extra}
    </div>
    {control}
  </div>
);

/** The on/off switch (`role="switch"`): flips at once, no Save. */
export const SetSwitch = ({ checked, onToggle, label, disabled, testId, busy }: {
  checked: boolean; onToggle: () => void; label: string; disabled?: boolean; testId?: string; busy?: boolean;
}) => (
  <button
    type="button"
    role="switch"
    aria-checked={checked}
    aria-label={label}
    aria-busy={busy || undefined}
    data-testid={testId}
    onClick={onToggle}
    disabled={disabled}
    className="set-switch"
  >
    <span className="set-knob" />
  </button>
);

/** A segmented choice (`role="radiogroup"`). */
export function SetSeg<T extends string>({ value, options, onChange, label, small = false }: {
  value: T; options: { id: T; label: React.ReactNode; lang?: string }[]; onChange: (v: T) => void; label: string; small?: boolean;
}) {
  return (
    <span className={`gm-seg${small ? ' gm-seg-sm' : ''}`} role="radiogroup" aria-label={label}>
      {options.map(o => (
        <button key={o.id} type="button" role="radio" aria-checked={value === o.id} lang={o.lang}
          onClick={() => { if (value !== o.id) onChange(o.id); }}>
          {o.label}
        </button>
      ))}
    </span>
  );
}

/** The status lamp before a hint (`.lamp-dot`): ok / warn / plain. */
export const Lamp = ({ tone }: { tone?: 'ok' | 'warn' | 'danger' | 'busy' }) => (
  <span className={`lamp-dot${tone === 'ok' ? ' is-ok' : tone === 'warn' || tone === 'danger' ? ' is-warn' : tone === 'busy' ? ' is-busy' : ''}`}
    data-tone={tone} aria-hidden="true" />
);

export const Chev = () => (
  <svg className="ic ic-sm" viewBox="0 0 20 20" aria-hidden="true"><path d="M6 8l4 4 4-4" /></svg>
);
