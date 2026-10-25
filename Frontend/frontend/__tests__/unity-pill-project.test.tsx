import React from 'react'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import ts from 'typescript'
import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { UnityMcpToggle } from '../renderer/components/home/UnityMcpToggle'
import { tr } from '../renderer/lib/i18n'

afterEach(cleanup)
const base = { status: 'connected' as const, toggling: false, reason: null, error: null, onToggle: vi.fn() }

// Evaluate the actual home expressions without loading Electron, Monaco and the full page.
const home = readFileSync(resolve(__dirname, '../renderer/pages/home.tsx'), 'utf8')
const source = ts.createSourceFile('home.tsx', home, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
function expression(name: string): string {
  let value = ''
  const visit = (node: ts.Node) => {
    if (ts.isVariableDeclaration(node) && node.name.getText(source) === name) value = node.initializer?.getText(source) ?? ''
    ts.forEachChild(node, visit)
  }
  visit(source)
  if (!value) throw new Error(`Missing home expression: ${name}`)
  return ts.transpile(value, { target: ts.ScriptTarget.ES2020 }).trim().replace(/;$/, '')
}
function projectProps(projects: string[], workspace: string | null) {
  const ai = { unityMcpProjects: projects, unityMcpStatus: 'connected' }
  const unityProjectName = new Function('ai', 'projectName', `return (${expression('unityProjectName')})`)(ai, workspace)
  const projectMismatch = new Function('ai', 'projectName', 'unityProjectName', `return (${expression('projectMismatch')})`)(ai, workspace, unityProjectName)
  return { projectName: unityProjectName, projectMismatch }
}

it('shows the Unity project and mismatch hint when the workspace differs', () => {
  render(<UnityMcpToggle {...base} {...projectProps(['BenchmarkTest3'], 'MatchDayOfficial')} />)
  expect(screen.getByText('BenchmarkTest3')).toBeTruthy()
  expect(screen.queryByText('MatchDayOfficial')).toBeNull()
  expect(screen.getByText(tr['unity.hintProjectMismatch'])).toBeTruthy()
  expect(home).toContain('unityProjectName={unityProjectName}')
  expect(home).toContain('projectName={unityProjectName}')
  expect(home).toContain('projectMismatch={projectMismatch}')
})

it('prefers the matching instance and hides the mismatch hint', () => {
  render(<UnityMcpToggle {...base} {...projectProps(['Other', 'GameA'], 'GameA')} />)
  expect(screen.getByText('GameA')).toBeTruthy()
  expect(screen.queryByText(tr['unity.hintProjectMismatch'])).toBeNull()
})

it('handles missing workspace and empty instance lists', () => {
  expect(projectProps([], 'GameA')).toEqual({ projectName: null, projectMismatch: false })
  expect(projectProps(['Other'], null)).toEqual({ projectName: 'Other', projectMismatch: false })
})
