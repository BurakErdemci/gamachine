import React from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'

import { ComputerUsePage } from '../renderer/components/home/settings/ComputerUsePage'
import { aktifDilAyarla } from '../renderer/lib/i18n'

afterEach(() => {
  cleanup()
  aktifDilAyarla('tr')
  vi.unstubAllGlobals()
})

describe('computer-use provider availability note', () => {
  it.each([
    ['tr', 'Ollama dışındaki bütün sağlayıcılarla çalışır; Ollama modelleri bilgisayarı kullanamaz.'],
    ['en', 'Works with every provider except Ollama; Ollama models cannot use the computer.'],
  ] as const)('renders the %s note directly below the page head', (language, text) => {
    vi.stubGlobal('ipc', undefined)
    aktifDilAyarla(language)

    render(<ComputerUsePage saved={vi.fn()} />)

    const note = screen.getByText(text)
    expect(note.previousElementSibling?.querySelector('h1')).toBeTruthy()
    expect(note.classList.contains('set-msg')).toBe(true)
  })
})
