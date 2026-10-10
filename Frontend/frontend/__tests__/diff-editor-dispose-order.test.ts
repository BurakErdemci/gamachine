/**
 * Closing a diff (accepting an approval card) crashed the renderer: @monaco-editor/react 4.7.0
 * disposes the two models before the DiffEditor, and monaco-editor 0.55 throws "TextModel got
 * disposed before DiffEditorWidget model got reset". The editors keep their models and free them
 * once the editor is gone.
 */
import { describe, it, expect, vi } from 'vitest'
import { readFileSync } from 'fs'
import path from 'path'

vi.mock('@monaco-editor/react', () => ({ loader: { config: () => {} } }))

import { disposeDiffModelsAfterEditor } from '../renderer/components/home/monaco-theme'

// A DiffEditor that throws the way Monaco does when a model goes before the widget is reset.
const fakeDiffEditor = () => {
  let alive = true
  const listeners: Array<() => void> = []
  const model = (name: string) => ({
    disposed: false,
    dispose() {
      if (alive) throw new Error(`TextModel ${name} got disposed before DiffEditorWidget model got reset`)
      this.disposed = true
    },
  })
  const models = { original: model('original'), modified: model('modified') }
  return {
    models,
    getModel: () => (alive ? models : null),
    onDidDispose: (l: () => void) => { listeners.push(l) },
    dispose: () => { alive = false; listeners.forEach((l) => l()) },
  }
}

describe('diff editor teardown', () => {
  it('the library order (models first) throws without the guard', () => {
    const editor = fakeDiffEditor()
    expect(() => editor.models.original.dispose()).toThrow(/before DiffEditorWidget/)
  })

  it('frees both models after the editor is disposed', () => {
    const editor = fakeDiffEditor()
    disposeDiffModelsAfterEditor(editor)
    // keepCurrent*Model: the library skips the models and disposes only the editor.
    expect(() => editor.dispose()).not.toThrow()
    expect(editor.models.original.disposed).toBe(true)
    expect(editor.models.modified.disposed).toBe(true)
  })

  it.each(['DiffViewer.tsx', 'EditorPanel.tsx'])('%s keeps its models and wires the guard', (file) => {
    const src = readFileSync(path.resolve(__dirname, '../renderer/components/home', file), 'utf8')
    const diff = src.slice(src.indexOf('<DiffEditor'))
    const tag = diff.slice(0, diff.indexOf('options='))
    expect(tag).toContain('keepCurrentOriginalModel')
    expect(tag).toContain('keepCurrentModifiedModel')
    expect(tag).toContain('disposeDiffModelsAfterEditor(editor)')
  })
})
