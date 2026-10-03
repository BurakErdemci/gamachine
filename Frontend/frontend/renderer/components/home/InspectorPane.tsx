import React, { useState } from 'react';
import { useLang } from '../../lib/i18n';
import type { ComponentGroup, Inspection, SceneField } from '../../lib/sceneEditor';
import { SceneCube } from './HierarchyPanel';

const Checkbox = ({ checked, label }: { checked: boolean; label: string }) => (
  <input className="f-bool" type="checkbox" checked={checked} disabled aria-label={label} />
);
const optionLabel = (option: NonNullable<SceneField['options']>[number]) => typeof option === 'string' ? option : option.label;
// Unity sends the full type name (UnityEngine.Transform).
const isTransform = (type: string) => /^(UnityEngine\.)?(Rect)?Transform$/.test(type);

function Field({ field }: { field: SceneField }) {
  const { t } = useLang();
  const value = field.value;
  let content: React.ReactNode;
  switch (field.kind) {
    case 'bool': content = <Checkbox checked={value === true} label={field.label} />; break;
    case 'float': case 'int': case 'string': {
      const input = <input className={`f-in${field.kind !== 'string' ? ' f-num' : ''}`} aria-label={field.label} readOnly
        value={value == null ? '' : String(value)} />;
      const range = field.range;
      // Unity serialises NaN and Infinity as strings; they stay text, never a NaN slider value.
      content = range && field.kind !== 'string' && typeof value === 'number' && Number.isFinite(value) ? <span className="f-sl"><input type="range" disabled aria-label={field.label}
        min={Array.isArray(range) ? range[0] : range.min} max={Array.isArray(range) ? range[1] : range.max} value={value} />{input}</span> : input;
      break;
    }
    case 'vec2': case 'vec3': case 'vec4': {
      const axes = ['x', 'y', 'z', 'w'].slice(0, Number(field.kind.slice(-1)));
      content = <div className="f-vec" style={{ gridTemplateColumns: `repeat(${axes.length}, minmax(0, 1fr))` }}>
        {axes.map((axis, index) => <label className="f-ax" key={axis}><span>{axis.toUpperCase()}</span>
          <input className="f-in f-num" readOnly aria-label={`${field.label} ${axis.toUpperCase()}`}
            value={String((Array.isArray(value) ? value[index] : (value as Record<string, unknown> | null)?.[axis]) ?? '')} /></label>)}
      </div>;
      break;
    }
    case 'enum': {
      const options = field.options ?? [];
      const option = options.find(item => typeof item !== 'string' && item.value === value)
        ?? (typeof value === 'number' ? options[value] : options.find(item => optionLabel(item) === value));
      content = <span className="f-enum">{option === undefined ? String(value ?? '') : optionLabel(option)}</span>;
      break;
    }
    case 'mask': {
      const mask = Number(value) | 0;
      const labels = (field.options ?? []).filter((_, index) => (mask & (1 << index)) !== 0).map(optionLabel);
      content = <span className="f-enum" title={String(mask)}>{labels.length ? labels.join(', ') : t('sceneEditor.none')}</span>;
      break;
    }
    case 'color': content = <span className="f-col"><span className="f-col-sw" style={{ backgroundColor: String(value) }} />
      <span className="f-col-hex">{String(value)}</span></span>; break;
    case 'ref': {
      const reference = value as { name: string; type: string } | null;
      content = <span className={`f-ref${reference ? '' : ' is-none'}`}>{reference ? `${reference.name} (${reference.type})` : t('sceneEditor.none')}</span>;
      break;
    }
    case 'list': content = <span className="f-list"><span aria-hidden="true">› </span><span>{t('sceneEditor.items', { count: Number(value) })}</span></span>; break;
    case 'unsupported': content = <span className="f-unsupported">{String(value)}</span>; break;
  }
  return <div className="fr" title={field.tooltip}><span className="fr-l">{field.label}</span><div className="fr-c">{content}</div></div>;
}

function Group({ group, many }: { group: ComponentGroup; many: boolean }) {
  const { t } = useLang();
  const [open, setOpen] = useState(!many || isTransform(group.type));
  if (group.type === 'missing') return <section className="cmp cmp-missing" role="note"><span aria-hidden="true">⚠ </span><span>{t('sceneEditor.missingScript')}</span></section>;
  return <section className={`cmp${!open ? ' is-shut' : ''}${group.enabled === false ? ' is-disabled' : ''}`}>
    <header className="cmp-head">
      <button type="button" className="cmp-toggle" aria-label={group.label} aria-expanded={open} onClick={() => setOpen(value => !value)}>
        <span className="cmp-tw"><svg className="ic" viewBox="0 0 20 20" aria-hidden="true"><path d="M5 7l5 5 5-5" /></svg></span>
        <SceneCube className="cmp-ic" /><span className="cmp-name">{group.label}</span>
      </button>
      {group.enabled !== null && <Checkbox checked={group.enabled} label={group.label} />}
    </header>
    <div className="cmp-body" hidden={!open}>{group.fields.map((field, index) => <Field key={`${field.path}-${index}`} field={field} />)}</div>
  </section>;
}

export const InspectorPane = ({ inspection, loading, error, stale }: {
  inspection: Inspection | null; loading: boolean; error: number | null; stale: boolean;
}) => {
  const { t } = useLang();
  const node = inspection?.node;
  return <div className={`insp${node?.prefab !== 'none' && node ? ' is-pf' : ''}`}>
    {stale && <p className="insp-note"><span className="hier-stale">{t('sceneEditor.stale')}</span> {t('sceneEditor.compiling')}</p>}
    {error !== null && <p className="insp-note" role="alert">{t(error === 503 ? 'sceneEditor.unavailable' : error === 504 ? 'sceneEditor.timeout' : 'sceneEditor.error')}</p>}
    {!node ? <div className="insp-empty" role={loading ? 'status' : undefined}><SceneCube className="ih-ic" /><p>{t(loading ? 'sceneEditor.loading' : 'sceneEditor.inspectorEmpty')}</p></div> : <>
      <header className="insp-head">
        <div className="ih-row"><Checkbox checked={node.activeSelf} label={t('sceneEditor.active')} /><SceneCube className="ih-ic" />
          <input className="ih-name" aria-label={t('sceneEditor.name')} title={node.name} value={node.name} readOnly /></div>
        <div className="ih-grid"><span>Tag</span><span className="f-enum">{node.tag}</span><span>Layer</span><span className="f-enum">{node.layer.name}</span></div>
        <div className="ih-row">
          {node.prefab !== 'none' && <div className="ih-pf"><span>Prefab</span><span className="ih-pf-name">{node.prefab === 'missing' ? t('sceneEditor.missingPrefab') : node.name}</span></div>}
          <label className="ih-static"><Checkbox checked={node.isStatic} label="Static" />Static</label>
        </div>
      </header>
      <div className="insp-comps">{inspection.groups.map((group, index) => <Group key={`${node.id}-${group.componentId ?? 'missing'}-${index}`} group={group} many={inspection.groups.length > 6} />)}</div>
      {inspection.truncated && <p className="ws-note insp-truncated">{t('sceneEditor.inspectTruncated')}</p>}
    </>}
  </div>;
};
