import React, { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { useLang } from '../../lib/i18n';
import { inspectorWriteKey } from '../../lib/sceneEditor';
import type { ComponentGroup, Inspection } from '../../lib/sceneEditor';
import type { InspectorActions } from '../../hooks/home/useSceneEditor';
import { SceneCube } from './HierarchyPanel';
import { SceneContextMenu } from './SceneContextMenu';
import { InspectorField, InspectorTextInput, InspectorWriteError } from './InspectorFieldEditors';
import { AddComponentPicker } from './AddComponentPicker';

const Checkbox = ({ checked, label }: { checked: boolean; label: string }) => (
  <input className="f-bool" type="checkbox" checked={checked} disabled aria-label={label} />
);
// Unity sends the full type name (UnityEngine.Transform).
const isTransform = (type: string) => /^(UnityEngine\.)?(Rect)?Transform$/.test(type);

function Group({ group, many, index, groups, disabled, actions }: {
  group: ComponentGroup; many: boolean; index: number; groups: ComponentGroup[]; disabled: boolean; actions?: InspectorActions;
}) {
  const { t } = useLang();
  const [open, setOpen] = useState(!many || isTransform(group.type));
  const [menu, setMenu] = useState<{ x: number; y: number } | null>(null);
  const more = useRef<HTMLButtonElement>(null);
  const enableKey = group.componentId === null ? '' : inspectorWriteKey.enabled(group.componentId);
  const actionKey = group.componentId === null ? '' : inspectorWriteKey.component(group.componentId);
  const enableWrite = actions?.inspectorWrites[enableKey];
  const actionWrite = actions?.inspectorWrites[actionKey];
  const blocked = disabled || !actions || group.componentId === null;
  const close = (focus: boolean) => { setMenu(null); if (focus) more.current?.focus({ preventScroll: true }); };
  useEffect(() => { if (blocked) setMenu(null); }, [blocked]);
  const run = (action: 'reset' | 'remove' | 'up' | 'down') => { if (!blocked && !actionWrite?.pending) void actions?.componentAction(group.componentId!, action); };
  if (group.type === 'missing') return <section className="cmp cmp-missing" role="note"><span aria-hidden="true">⚠ </span><span>{t('sceneEditor.missingScript')}</span></section>;
  const enabled = enableWrite?.value === undefined ? group.enabled : enableWrite.value === true;
  return <section data-component-id={group.componentId} className={`cmp${!open ? ' is-shut' : ''}${enabled === false ? ' is-disabled' : ''}${enableWrite?.pending || actionWrite?.pending ? ' is-writing' : ''}`}>
    <header className="cmp-head">
      <button type="button" className="cmp-tw" aria-label={group.label} aria-expanded={open} onClick={() => setOpen(value => !value)}>
        <svg className="ic" viewBox="0 0 20 20" aria-hidden="true"><path d="M5 7l5 5 5-5" /></svg>
      </button>
      {group.enabled !== null && <input className="f-bool cmp-on" type="checkbox" checked={enabled === true} disabled={blocked || enableWrite?.pending}
        aria-label={group.label} onChange={event => { if (!blocked) void actions?.setComponentEnabled(group.componentId!, event.target.checked); }} />}
      <SceneCube className="cmp-ic" /><span className="cmp-name" title={group.type}>{group.label}</span>
      <button type="button" ref={more} className="icon-btn cmp-more" aria-haspopup="menu" aria-expanded={!!menu}
        aria-label={t('sceneEditor.componentMenu', { name: group.label })} disabled={blocked || actionWrite?.pending}
        onClick={() => { if (blocked) return; const rect = more.current!.getBoundingClientRect(); setMenu({ x: rect.right - 220, y: rect.bottom + 4 }); }}>…</button>
    </header>
    <InspectorWriteError error={enableWrite?.error} disabled={blocked} revert={() => actions?.clearInspectorWrite(enableKey)} />
    <InspectorWriteError error={actionWrite?.error} disabled={blocked} revert={() => actions?.clearInspectorWrite(actionKey)} />
    <div className="cmp-body" hidden={!open}>{group.fields.map((field, index) => <InspectorField key={`${field.path}-${index}`} field={field}
      componentId={group.componentId} disabled={disabled} actions={actions} />)}</div>
    {menu && !blocked && <SceneContextMenu x={menu.x} y={menu.y} label={t('sceneEditor.componentMenu', { name: group.label })} onClose={close}
      entries={[
        { kind: 'item', label: t('sceneEditor.componentReset'), run: () => run('reset') },
        { kind: 'item', label: t('sceneEditor.componentRemove'), disabled: !group.removable, run: () => run('remove') },
        { kind: 'sep' },
        { kind: 'item', label: t('sceneEditor.componentUp'), disabled: isTransform(group.type) || index === 0 || isTransform(groups[index - 1]?.type ?? ''), run: () => run('up') },
        { kind: 'item', label: t('sceneEditor.componentDown'), disabled: isTransform(group.type) || index === groups.length - 1, run: () => run('down') },
      ]} />}
  </section>;
}

export const InspectorPane = ({ inspection, loading, error, stale, actions }: {
  inspection: Inspection | null; loading: boolean; error: number | null; stale: boolean;
  actions?: InspectorActions;
}) => {
  const { t } = useLang();
  const node = inspection?.node;
  const root = useRef<HTMLDivElement>(null);
  const addButton = useRef<HTMLButtonElement>(null);
  const [picker, setPicker] = useState(false);
  const [added, setAdded] = useState<number | null>(null);
  const objectId = node?.id;
  const allFields = inspection?.groups.flatMap(group => group.fields) ?? [];
  const locked = allFields.length > 0 && allFields.every(field => field.readonly);
  const disabled = stale || locked || !actions;
  const activeKey = node ? inspectorWriteKey.active(node.id) : '';
  const nameKey = node ? inspectorWriteKey.name(node.id) : '';
  const addKey = node ? inspectorWriteKey.add(node.id) : '';
  const activeWrite = actions?.inspectorWrites[activeKey];
  const nameWrite = actions?.inspectorWrites[nameKey];
  const addWrite = actions?.inspectorWrites[addKey];
  const closePicker = (focus: boolean) => { setPicker(false); if (focus) addButton.current?.focus({ preventScroll: true }); };
  useEffect(() => { setPicker(false); setAdded(null); }, [objectId]);
  useEffect(() => { if (disabled) setPicker(false); }, [disabled]);
  useLayoutEffect(() => {
    if (added === null || !inspection?.groups.some(group => group.componentId === added)) return;
    const component = root.current?.querySelector<HTMLElement>(`[data-component-id="${added}"]`);
    const scroller = root.current?.closest<HTMLElement>('.ws-pane') ?? root.current?.parentElement;
    if (component && scroller) {
      const rect = component.getBoundingClientRect(), bounds = scroller.getBoundingClientRect();
      if (rect.bottom > bounds.bottom) scroller.scrollTop += rect.bottom - bounds.bottom;
      else if (rect.top < bounds.top) scroller.scrollTop -= bounds.top - rect.top;
    }
    setAdded(null);
  }, [added, inspection]);
  return <div ref={root} className={`insp${node?.prefab !== 'none' && node ? ' is-pf' : ''}`}>
    {stale && <p className="insp-note"><span className="hier-stale">{t('sceneEditor.stale')}</span> {t('sceneEditor.compiling')}</p>}
    {error !== null && <p className="insp-note" role="alert">{t(error === 503 ? 'sceneEditor.unavailable' : error === 504 ? 'sceneEditor.timeout' : 'sceneEditor.error')}</p>}
    {!node ? <div className="insp-empty" role={loading ? 'status' : undefined}><SceneCube className="ih-ic" /><p>{t(loading ? 'sceneEditor.loading' : 'sceneEditor.inspectorEmpty')}</p></div> : <>
      <header className="insp-head">
        <div className={`ih-row${activeWrite?.pending || nameWrite?.pending ? ' is-writing' : ''}`}>
          <input className="f-bool" type="checkbox" checked={activeWrite?.value === undefined ? node.activeSelf : activeWrite.value === true}
            disabled={disabled || activeWrite?.pending} aria-label={t('sceneEditor.active')}
            onChange={event => { if (!disabled) void actions?.setActive(node.id, event.target.checked); }} /><SceneCube className="ih-ic" />
          <InspectorTextInput key={node.id} className="ih-name" value={nameWrite?.value ?? node.name} label={t('sceneEditor.name')}
            disabled={disabled || !!nameWrite?.pending} parse={raw => raw.trim() || undefined}
            commit={name => { if (!disabled) void actions?.rename(node.id, name, true); }} /></div>
        <InspectorWriteError error={activeWrite?.error} disabled={disabled} revert={() => actions?.clearInspectorWrite(activeKey)} />
        <InspectorWriteError error={nameWrite?.error} disabled={disabled} revert={() => actions?.clearInspectorWrite(nameKey)} />
        <div className="ih-grid"><span>Tag</span><span className="f-enum">{node.tag}</span><span>Layer</span><span className="f-enum">{node.layer.name}</span></div>
        <div className="ih-row">
          {node.prefab !== 'none' && <div className="ih-pf"><span>Prefab</span><span className="ih-pf-name">{node.prefab === 'missing' ? t('sceneEditor.missingPrefab') : node.name}</span></div>}
          <label className="ih-static"><Checkbox checked={node.isStatic} label="Static" />Static</label>
        </div>
      </header>
      <div className="insp-comps">{inspection.groups.map((group, index) => <Group key={`${node.id}-${group.componentId ?? `missing-${index}`}`} group={group}
        index={index} groups={inspection.groups} many={inspection.groups.length > 6} disabled={disabled} actions={actions} />)}</div>
      <div className="insp-add"><button type="button" ref={addButton} className="btn btn-ghost" aria-haspopup="dialog" aria-expanded={picker}
        disabled={disabled || addWrite?.pending} onClick={() => {
          if (disabled) return;
          if (picker) closePicker(false);
          else { setPicker(true); actions?.loadComponentMenu(node.id); }
        }}><span aria-hidden="true">＋</span>{t('sceneEditor.addComponent')}</button>
        {!picker && <InspectorWriteError error={addWrite?.error} disabled={disabled} revert={() => actions?.clearInspectorWrite(addKey)} />}
      </div>
      {picker && !disabled && actions && addButton.current && <AddComponentPicker key={node.id} id={node.id} anchor={addButton.current} actions={actions}
        onClose={closePicker} onAdded={id => { setAdded(id); closePicker(true); }} />}
      {inspection.truncated && <p className="ws-note insp-truncated">{t('sceneEditor.inspectTruncated')}</p>}
    </>}
  </div>;
};
