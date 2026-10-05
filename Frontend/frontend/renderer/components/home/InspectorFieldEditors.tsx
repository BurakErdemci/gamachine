import React, { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useLang } from '../../lib/i18n';
import { inspectorWriteKey } from '../../lib/sceneEditor';
import type { SceneField } from '../../lib/sceneEditor';
import type { InspectorActions, SceneWriteError } from '../../hooks/home/useSceneEditor';

export const optionLabel = (option: NonNullable<SceneField['options']>[number]) => typeof option === 'string' ? option : option.label;
const text = (value: unknown) => value == null ? '' : String(value);
const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);
const floatText = (value: unknown) => typeof value === 'number' && Number.isFinite(value) ? String(Number(value.toPrecision(7))) : text(value);

export function InspectorTextInput({ value, label, disabled, numeric, rounded, className = 'f-in', parse, commit, onDone }: {
  value: unknown; label: string; disabled: boolean; numeric?: boolean; rounded?: boolean; className?: string;
  parse: (raw: string) => unknown; commit: (value: any) => void; onDone?: () => void;
}) {
  const display = rounded ? floatText : text;
  const shown = display(value);
  const [draft, setDraft] = useState(shown);
  const baseline = useRef(shown);
  const raw = useRef(shown);
  useEffect(() => { baseline.current = shown; raw.current = shown; setDraft(shown); }, [shown, disabled]);
  const restore = () => { raw.current = baseline.current; setDraft(baseline.current); };
  const finish = () => {
    if (!disabled && raw.current !== baseline.current) {
      const parsed = parse(raw.current);
      if (parsed === undefined) restore();
      else {
        baseline.current = display(parsed); raw.current = baseline.current; setDraft(baseline.current);
        if (!same(parsed, value)) commit(parsed);
      }
    }
    onDone?.();
  };
  return <input className={className} aria-label={label} title={numeric ? text(value) : undefined} inputMode={numeric ? 'decimal' : undefined} readOnly={disabled}
    value={draft} autoFocus={!!onDone} onChange={event => { if (!disabled) { raw.current = event.target.value; setDraft(raw.current); } }}
    onBlur={finish} onKeyDown={event => {
      if (event.key === 'Enter') { event.preventDefault(); finish(); event.currentTarget.blur(); }
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); restore(); onDone?.(); event.currentTarget.blur(); }
    }} />;
}

export function InspectorWriteError({ error, revert, disabled }: { error?: SceneWriteError; revert: () => void; disabled: boolean }) {
  const { t } = useLang();
  if (!error) return null;
  return <div className="fr-err" role="alert"><span className="fr-err-t">{t(`sceneEditor.write.${error.code}`)}
    {error.unsure && ` ${t('sceneEditor.write.unsure')}`}</span>
    {error.retry && <button type="button" disabled={disabled} onClick={error.retry}>{t('sceneEditor.retry')}</button>}
    <button type="button" onClick={revert}>{t('sceneEditor.revert')}</button>
  </div>;
}

interface Choice { label: string; checked: boolean; pick: () => void }
function ChoiceMenu({ choices, opener, label, checkbox, close }: {
  choices: Choice[]; opener: HTMLButtonElement; label: string; checkbox?: boolean; close: (focus: boolean) => void;
}) {
  const box = useRef<HTMLDivElement>(null);
  const [hot, setHot] = useState(Math.max(0, choices.findIndex(choice => choice.checked)));
  const closeRef = useRef(close); closeRef.current = close;
  useLayoutEffect(() => {
    const element = box.current!;
    const rect = opener.getBoundingClientRect();
    element.style.maxHeight = `${Math.max(0, window.innerHeight - 16)}px`;
    element.style.left = `${Math.max(8, Math.min(rect.left, window.innerWidth - element.offsetWidth - 8))}px`;
    const below = rect.bottom + 4;
    element.style.top = `${Math.max(8, Math.min(below + element.offsetHeight > window.innerHeight - 8 ? rect.top - element.offsetHeight - 4 : below, window.innerHeight - element.offsetHeight - 8))}px`;
    element.focus({ preventScroll: true });
  }, [opener]);
  useLayoutEffect(() => {
    const element = box.current, item = element?.querySelector<HTMLElement>(`[data-index="${hot}"]`);
    if (!element || !item) return;
    if (item.offsetTop < element.scrollTop) element.scrollTop = item.offsetTop;
    else if (item.offsetTop + item.offsetHeight > element.scrollTop + element.clientHeight) element.scrollTop = item.offsetTop + item.offsetHeight - element.clientHeight;
  }, [hot]);
  useEffect(() => {
    const outside = (event: MouseEvent) => { if (!box.current?.contains(event.target as Node) && !opener.contains(event.target as Node)) closeRef.current(false); };
    const away = () => closeRef.current(false);
    document.addEventListener('mousedown', outside, true); window.addEventListener('blur', away); window.addEventListener('resize', away);
    return () => { document.removeEventListener('mousedown', outside, true); window.removeEventListener('blur', away); window.removeEventListener('resize', away); };
  }, [opener]);
  const pick = (index: number) => { close(true); choices[index]?.pick(); };
  return createPortal(<div className="r13-menu insp-field-menu" ref={box} role="menu" tabIndex={-1} aria-label={label}
    aria-activedescendant={`insp-choice-${hot}`} onKeyDown={event => {
      if (['ArrowDown', 'ArrowUp', 'Home', 'End', 'Enter', ' ', 'Escape', 'Tab'].includes(event.key)) { event.preventDefault(); event.stopPropagation(); }
      switch (event.key) {
        case 'ArrowDown': setHot(index => Math.min(choices.length - 1, index + 1)); break;
        case 'ArrowUp': setHot(index => Math.max(0, index - 1)); break;
        case 'Home': setHot(0); break;
        case 'End': setHot(choices.length - 1); break;
        case 'Enter': case ' ': pick(hot); break;
        case 'Escape': case 'Tab': close(true); break;
      }
    }}>
    {choices.map((choice, index) => <button key={index} type="button" id={`insp-choice-${index}`} data-index={index} tabIndex={-1}
      role={checkbox ? 'menuitemcheckbox' : 'menuitemradio'} aria-checked={choice.checked} className={`mi${index === hot ? ' is-hot' : ''}`}
      onMouseMove={() => setHot(index)} onClick={() => pick(index)}><span aria-hidden="true">{choice.checked ? '✓' : ''}</span><span className="mi-t">{choice.label}</span></button>)}
  </div>, document.body);
}

function ChoiceButton({ label, value, disabled, choices, checkbox }: {
  label: string; value: string; disabled: boolean; choices: Choice[]; checkbox?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const button = useRef<HTMLButtonElement>(null);
  useEffect(() => { if (disabled) setOpen(false); }, [disabled]);
  const close = (focus: boolean) => { setOpen(false); if (focus) button.current?.focus({ preventScroll: true }); };
  return <><button type="button" ref={button} className="f-enum" aria-label={label} aria-haspopup="menu" aria-expanded={open}
    disabled={disabled} onClick={() => { if (!disabled) setOpen(on => !on); }}><span>{value}</span><span aria-hidden="true">⌄</span></button>
    {open && !disabled && button.current && <ChoiceMenu choices={choices} opener={button.current} label={label} checkbox={checkbox} close={close} />}</>;
}

function ColorEditor({ value, field, disabled, commit }: { value: unknown; field: SceneField; disabled: boolean; commit: (value: unknown) => void }) {
  const [editing, setEditing] = useState(false);
  useEffect(() => { if (disabled) setEditing(false); }, [disabled]);
  return editing && !disabled ? <InspectorTextInput value={value} label={field.label} disabled={disabled}
    parse={raw => /^#[\da-f]{6}([\da-f]{2})?$/i.test(raw.trim()) ? raw.trim() : undefined} commit={commit} onDone={() => setEditing(false)} />
    : <button type="button" className="f-col" aria-label={field.label} disabled={disabled} onClick={() => setEditing(true)}>
      <span className="f-col-sw" style={{ backgroundColor: text(value) }} /><span className="f-col-hex">{text(value)}</span></button>;
}

function RangeEditor({ value, field, disabled, parse, commit }: {
  value: number; field: SceneField; disabled: boolean; parse: (raw: string) => number | undefined; commit: (value: number) => void;
}) {
  const [draft, setDraft] = useState(value);
  const raw = useRef(value);
  const dragging = useRef(false);
  useEffect(() => { if (!dragging.current) { raw.current = value; setDraft(value); } }, [value]);
  useEffect(() => { if (disabled) { dragging.current = false; raw.current = value; setDraft(value); } }, [disabled, value]);
  const endDrag = () => { if (dragging.current) { dragging.current = false; if (!disabled && raw.current !== value) commit(raw.current); } };
  return <input type="range" aria-label={field.label} disabled={disabled}
    min={Array.isArray(field.range) ? field.range[0] : field.range!.min} max={Array.isArray(field.range) ? field.range[1] : field.range!.max}
    step={field.kind === 'int' ? 1 : 'any'} value={draft}
    onPointerDown={event => { if (!disabled && event.button === 0) { dragging.current = true; event.currentTarget.setPointerCapture?.(event.pointerId); } }}
    onChange={event => {
      if (disabled) return;
      const next = parse(event.target.value); if (next === undefined) return;
      raw.current = next; setDraft(next);
      if (!dragging.current && next !== value) commit(next);
    }}
    onPointerUp={endDrag} onLostPointerCapture={endDrag}
    onPointerCancel={() => { dragging.current = false; raw.current = value; setDraft(value); }} />;
}

export function InspectorField({ field, componentId, disabled, actions }: {
  field: SceneField; componentId: number | null; disabled: boolean; actions?: InspectorActions;
}) {
  const { t } = useLang();
  const key = componentId === null ? '' : inspectorWriteKey.field(componentId, field.path);
  const write = actions?.inspectorWrites[key];
  const [scrub, setScrub] = useState<number | null>(null);
  const value = scrub ?? (write && write.value !== undefined ? write.value : field.value);
  const editable = !disabled && !field.readonly && !field.truncated && componentId !== null && !!actions;
  const blocked = !editable || !!write?.pending;
  const live = useRef({ blocked, value }); live.current = { blocked, value };
  const cancelScrub = useRef<(() => void) | null>(null);
  useEffect(() => () => cancelScrub.current?.(), []);
  useEffect(() => { if (blocked) { cancelScrub.current?.(); setScrub(null); } }, [blocked]);
  const commit = (next: unknown) => { if (!live.current.blocked && !same(next, value)) void actions?.setField(componentId!, field.path, next); };
  const number = (raw: string) => {
    if (!raw.trim()) return undefined;
    let result = Number(raw.trim().replace(',', '.'));
    if (!Number.isFinite(result)) return undefined;
    if (field.kind === 'int') {
      result = Math.round(result);
      if (!Number.isSafeInteger(result)) return undefined;
    }
    if (field.range) {
      const min = Array.isArray(field.range) ? field.range[0] : field.range.min;
      const max = Array.isArray(field.range) ? field.range[1] : field.range.max;
      result = Math.max(min, Math.min(max, result));
    }
    return result;
  };
  let content: React.ReactNode;
  switch (field.kind) {
    case 'float': case 'int': case 'string': {
      const input = <InspectorTextInput value={value} label={field.label} disabled={blocked} numeric={field.kind !== 'string'}
        rounded={field.kind === 'float'}
        className={`f-in${field.kind !== 'string' ? ' f-num' : ''}`} parse={field.kind === 'string' ? raw => raw : number} commit={commit} />;
      content = field.range && field.kind !== 'string' && typeof value === 'number' && Number.isFinite(value)
        ? <span className="f-sl"><RangeEditor value={value} field={field} disabled={!editable} parse={number}
          commit={next => { if (editable) void actions?.setField(componentId!, field.path, next); }} />{input}</span> : input;
      break;
    }
    case 'bool': content = <input type="checkbox" className="f-bool" aria-label={field.label} checked={value === true} disabled={blocked} onChange={event => commit(event.target.checked)} />; break;
    case 'vec2': case 'vec3': case 'vec4': {
      const axes = ['x', 'y', 'z', 'w'].slice(0, Number(field.kind.slice(-1)));
      const vector = axes.map((axis, index) => (Array.isArray(value) ? value[index] : (value as Record<string, unknown> | null)?.[axis]) ?? '');
      content = <div className="f-vec" style={{ gridTemplateColumns: `repeat(${axes.length}, minmax(0, 1fr))` }}>
        {axes.map((axis, index) => <label className="f-ax" key={axis}><span>{axis.toUpperCase()}</span>
          <InspectorTextInput value={vector[index]} label={`${field.label} ${axis.toUpperCase()}`} disabled={blocked} numeric rounded className="f-in f-num" parse={number}
            commit={next => commit(vector.map((value, i) => i === index ? next : value))} /></label>)}
      </div>; break;
    }
    case 'enum': {
      const options = field.options ?? [];
      const current = typeof value === 'number' ? value : options.findIndex(option => typeof option === 'string' ? option === value : option.value === value);
      content = <ChoiceButton label={field.label} value={options[current] === undefined ? text(value) : optionLabel(options[current])} disabled={blocked}
        choices={options.map((option, index) => ({ label: optionLabel(option), checked: index === current, pick: () => commit(index) }))} />; break;
    }
    case 'mask': {
      const mask = Number(value) | 0;
      const options = (field.options ?? []).map((option, index) => ({ label: optionLabel(option), index })).filter(option => option.label.trim());
      const labels = options.filter(option => (mask & (1 << option.index)) !== 0).map(option => option.label);
      content = <ChoiceButton label={field.label} value={labels.length ? labels.join(', ') : t('sceneEditor.none')} disabled={blocked} checkbox
        choices={[
          { label: t('sceneEditor.maskNothing'), checked: mask === 0, pick: () => commit(0) },
          { label: t('sceneEditor.maskEverything'), checked: mask === -1, pick: () => commit(-1) },
          ...options.map(option => ({ label: option.label, checked: (mask & (1 << option.index)) !== 0, pick: () => commit(mask ^ (1 << option.index)) })),
        ]} />; break;
    }
    case 'color': content = <ColorEditor value={value} field={field} disabled={blocked} commit={commit} />; break;
    case 'ref': {
      const reference = value as { name: string; type: string } | null;
      content = <span className={`f-ref${reference ? '' : ' is-none'}`}>{reference ? `${reference.name} (${reference.type})` : t('sceneEditor.none')}</span>; break;
    }
    case 'list': content = <span className="f-list"><span aria-hidden="true">› </span><span>{t('sceneEditor.items', { count: Number(value) })}</span></span>; break;
    case 'unsupported': content = <span className="f-unsupported">{text(value)}</span>; break;
  }
  const startScrub = (event: React.PointerEvent) => {
    if (blocked || !['float', 'int'].includes(field.kind) || typeof value !== 'number' || !Number.isFinite(value) || event.button > 0) return;
    event.preventDefault(); cancelScrub.current?.();
    const x = event.clientX, initial = value;
    let next = initial;
    const move = (event: PointerEvent) => {
      next = number(String(Math.round((initial + Math.round((event.clientX - x) / 2) * (field.kind === 'int' ? 1 : 0.05)) * 100) / 100)) ?? initial;
      setScrub(next);
    };
    const cancel = () => {
      document.removeEventListener('pointermove', move); document.removeEventListener('pointerup', up); document.removeEventListener('pointercancel', abort);
      cancelScrub.current = null;
    };
    const up = () => { cancel(); setScrub(null); if (!live.current.blocked && next !== initial) void actions?.setField(componentId!, field.path, next); };
    const abort = () => { cancel(); setScrub(null); };
    cancelScrub.current = cancel;
    document.addEventListener('pointermove', move); document.addEventListener('pointerup', up); document.addEventListener('pointercancel', abort);
  };
  return <div className={`fr${write?.pending ? ' is-writing' : ''}${write?.error ? ' is-err' : ''}`} title={field.tooltip} aria-busy={write?.pending || undefined}>
    <span className="fr-l" data-scrub={editable && ['float', 'int'].includes(field.kind) ? '' : undefined} onPointerDown={startScrub}>{field.label}</span><div className="fr-c">{content}</div>
    {field.truncated && <span className="fr-long-text">{t('sceneEditor.textTooLong')}</span>}
    <InspectorWriteError error={write?.error} disabled={blocked} revert={() => actions?.clearInspectorWrite(key)} />
  </div>;
}
