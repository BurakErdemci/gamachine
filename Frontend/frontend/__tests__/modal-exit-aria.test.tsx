/**
 * A dialog that is animating out no longer claims the keyboard: AnimatePresence keeps its node
 * for the exit animation, and the window's Ctrl+S guard (home.tsx) skips the save while any
 * [aria-modal="true"] is in the document.
 */
import React from 'react'
import { afterEach, describe, expect, it } from 'vitest'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'

import { ConfirmDialogHost, confirmDialog } from '../renderer/components/ui/ConfirmDialog'
import { ExportModal } from '../renderer/components/home/ExportModal'
import { LangContext, ceviriUygula } from '../renderer/lib/i18n'

afterEach(() => { cleanup() })

const modal = () => document.querySelector('[aria-modal="true"]')

describe('dialogs drop aria-modal while they animate out', () => {
  it('ConfirmDialog', async () => {
    render(<ConfirmDialogHost />)
    let answer: Promise<boolean> | null = null
    act(() => { answer = confirmDialog('Delete it?', 'Delete', 'Cancel') })
    expect(modal()).toBe(screen.getByRole('alertdialog'))
    act(() => { fireEvent.click(screen.getByText('Delete')) })
    // Still on screen for its exit animation, but no longer modal.
    expect(screen.queryByRole('alertdialog')).not.toBeNull()
    expect(modal()).toBeNull()
    await expect(answer).resolves.toBe(true)
  })

  it('ExportModal', () => {
    const props = {
      exportFileName: 'a.cs', workspacePath: null, onFileNameChange: () => {}, onClose: () => {},
      onChangeExportDir: async () => {}, onExportSingleFile: async () => {}, onExportMultipleFiles: async () => {},
    }
    const ui = (open: boolean) => (
      <LangContext.Provider value={{ lang: 'en', setLang: () => {}, t: (k: any, v?: any) => ceviriUygula('en', k, v) }}>
        <ExportModal {...props} exportModal={open ? {
          isOpen: true, codeString: 'class A {}', suggestedName: 'A.cs', targetDir: 'C:/p',
          existingFile: false, multiFile: false, files: [], exportResult: null,
        } : null} />
      </LangContext.Provider>
    )
    const { rerender } = render(ui(true))
    expect(modal()).toBe(screen.getByRole('dialog'))
    rerender(ui(false))
    expect(screen.queryByRole('dialog')).not.toBeNull()
    expect(modal()).toBeNull()
  })
})
