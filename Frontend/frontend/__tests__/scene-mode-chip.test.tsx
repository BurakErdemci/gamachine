import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import React from 'react';
import { render, screen, cleanup, fireEvent, act, waitFor, within } from '@testing-library/react';

vi.mock('axios', () => {
  const get = vi.fn();
  const post = vi.fn();
  return { default: { get, post }, get, post };
});

import axios from 'axios';
import { ThreadHeader } from '../renderer/components/home/ThreadHeader';
import { EmptyChat } from '../renderer/components/home/EmptyChat';
import { LangContext, translations, ceviriUygula, type Lang } from '../renderer/lib/i18n';

const mockGet = (axios as any).get as ReturnType<typeof vi.fn>;
const mockPost = (axios as any).post as ReturnType<typeof vi.fn>;

const sampleConv: any = {
  id: 42,
  title: 'Oyuncu hareketi ve kamera kontrolü',
  created_at: '2026-11-26 14:30:00',
};

const sceneAnswer = (mode: string, unity = true, workspace = 'C:/work/BenchmarkTest4') => ({
  data: { mode, modes: ['code', 'mixed', 'team'], workspace, unity },
});

const renderHeader = (
  props: Partial<React.ComponentProps<typeof ThreadHeader>> = {},
  lang: Lang = 'tr',
) => {
  const value = {
    lang,
    setLang: () => {},
    t: (key: keyof typeof translations.tr, params?: Record<string, string | number>) =>
      ceviriUygula(lang, key, params),
  };
  return render(
    <LangContext.Provider value={value}>
      <ThreadHeader
        conversation={sampleConv}
        projectName="BenchmarkTest4"
        awaiting={false}
        api="http://127.0.0.1:8000"
        sessionToken="tok-123"
        workspacePath="C:/work/BenchmarkTest4"
        {...props}
      />
    </LangContext.Provider>,
  );
};

const findChip = () => screen.getByRole('button', { name: /Sahne yazım modu|Scene writing mode/i });

beforeEach(() => {
  mockGet.mockReset();
  mockPost.mockReset();
  localStorage.setItem('app-lang', 'tr');
});

afterEach(() => {
  cleanup();
});

describe('SceneModeChip', () => {
  it('stays hidden until GET resolves and stays hidden when unity=false', async () => {
    let resolveGet: (v: unknown) => void = () => {};
    mockGet.mockImplementationOnce(() => new Promise((r) => { resolveGet = r; }));

    renderHeader();
    expect(screen.queryByRole('button', { name: /Sahne yazım modu/i })).toBeNull();
    expect(screen.getByText('BenchmarkTest4')).toBeTruthy();

    await act(async () => {
      resolveGet(sceneAnswer('mixed', false));
      await Promise.resolve();
    });

    expect(screen.queryByRole('button', { name: /Sahne yazım modu/i })).toBeNull();
    expect(screen.getByText('BenchmarkTest4')).toBeTruthy();
  });

  it('stays hidden when api or sessionToken is missing or when GET fails', async () => {
    mockGet.mockResolvedValue(sceneAnswer('mixed'));
    renderHeader({ sessionToken: null });
    await act(async () => { await Promise.resolve(); });
    expect(mockGet).not.toHaveBeenCalled();
    expect(screen.queryByRole('button', { name: /Sahne yazım modu/i })).toBeNull();
    cleanup();

    renderHeader({ api: undefined });
    await act(async () => { await Promise.resolve(); });
    expect(mockGet).not.toHaveBeenCalled();
    expect(screen.queryByRole('button', { name: /Sahne yazım modu/i })).toBeNull();
    cleanup();

    mockGet.mockRejectedValueOnce(new Error('network down'));
    renderHeader();
    await waitFor(() => expect(mockGet).toHaveBeenCalledTimes(1));
    await act(async () => { await Promise.resolve(); });
    expect(screen.queryByRole('button', { name: /Sahne yazım modu/i })).toBeNull();
  });

  it('shows Karma by default for a Unity project with the mockup contract', async () => {
    mockGet.mockResolvedValueOnce(sceneAnswer('mixed'));
    renderHeader();

    const chip = await waitFor(() => findChip());
    expect(mockGet).toHaveBeenCalledWith('http://127.0.0.1:8000/workspace/scene-mode', {
      headers: { 'X-Session-Token': 'tok-123' },
    });
    expect(chip.getAttribute('type')).toBe('button');
    expect(chip.getAttribute('aria-haspopup')).toBe('true');
    expect(chip.getAttribute('aria-expanded')).toBe('false');
    expect(chip.getAttribute('aria-label')).toBe('Sahne yazım modu: Karma');
    expect(chip.textContent).toContain('Sahne:');
    expect(chip.textContent).toContain('Karma');
  });

  it('picking Ekip POSTs {"mode":"team"} and updates immediately', async () => {
    mockGet.mockResolvedValueOnce(sceneAnswer('mixed'));
    let resolvePost: (v: unknown) => void = () => {};
    mockPost.mockImplementationOnce(() => new Promise((r) => { resolvePost = r; }));

    renderHeader();
    const chip = await waitFor(() => findChip());
    fireEvent.click(chip);

    expect(chip.getAttribute('aria-expanded')).toBe('true');
    const group = screen.getByRole('radiogroup', { name: 'Sahne yazım modu' });
    const radios = within(group).getAllByRole('radio');
    expect(radios).toHaveLength(3);
    expect(radios.map((r) => r.getAttribute('aria-checked'))).toEqual(['false', 'true', 'false']);
    expect(group.textContent).toContain('En hızlı; sahneyi kod kurar');
    expect(group.textContent).toContain("Sistem kodda, bölüm sahnede/prefab'da");
    expect(group.textContent).toContain('Her şey sahnede, prefab, Inspector');

    const teamOpt = radios.find((r) => r.textContent?.includes('Ekip'))!;
    fireEvent.click(teamOpt);

    // Optimistic update shows Ekip at once and closes popover.
    expect(chip.getAttribute('aria-expanded')).toBe('false');
    expect(chip.getAttribute('aria-label')).toBe('Sahne yazım modu: Ekip');
    expect(mockPost).toHaveBeenCalledWith(
      'http://127.0.0.1:8000/workspace/scene-mode',
      { mode: 'team' },
      { headers: { 'X-Session-Token': 'tok-123' } },
    );

    await act(async () => {
      resolvePost({ data: { mode: 'team', modes: ['code', 'mixed', 'team'], workspace: 'C:/work/BenchmarkTest4' } });
      await Promise.resolve();
    });
    expect(chip.getAttribute('aria-label')).toBe('Sahne yazım modu: Ekip');
  });

  it('reverts to the previous mode without crashing if POST fails', async () => {
    mockGet.mockResolvedValueOnce(sceneAnswer('code'));
    mockPost.mockRejectedValueOnce(new Error('400'));

    renderHeader();
    const chip = await waitFor(() => findChip());
    expect(chip.getAttribute('aria-label')).toBe('Sahne yazım modu: Kod');

    fireEvent.click(chip);
    const teamOpt = screen.getAllByRole('radio').find((r) => r.textContent?.includes('Ekip'))!;
    await act(async () => {
      fireEvent.click(teamOpt);
      await Promise.resolve();
    });

    await waitFor(() => expect(chip.getAttribute('aria-label')).toBe('Sahne yazım modu: Kod'));
  });

  it('supports keyboard open/arrows/Escape focus return and outside click close', async () => {
    mockGet.mockResolvedValueOnce(sceneAnswer('mixed'));
    renderHeader();
    const chip = await waitFor(() => findChip());

    // Enter opens and focuses the currently checked option (Karma).
    chip.focus();
    fireEvent.keyDown(chip, { key: 'Enter' });
    expect(chip.getAttribute('aria-expanded')).toBe('true');
    const radios = screen.getAllByRole('radio');
    expect(document.activeElement).toBe(radios[1]);

    // ArrowDown moves to Ekip, ArrowDown wraps to Kod, ArrowUp wraps back to Ekip.
    const group = screen.getByRole('radiogroup');
    fireEvent.keyDown(group, { key: 'ArrowDown' });
    expect(document.activeElement).toBe(radios[2]);
    fireEvent.keyDown(group, { key: 'ArrowDown' });
    expect(document.activeElement).toBe(radios[0]);
    fireEvent.keyDown(group, { key: 'ArrowUp' });
    expect(document.activeElement).toBe(radios[2]);

    // Escape closes and returns focus to the chip.
    fireEvent.keyDown(document.activeElement!, { key: 'Escape' });
    expect(chip.getAttribute('aria-expanded')).toBe('false');
    expect(screen.queryByRole('radiogroup')).toBeNull();
    expect(document.activeElement).toBe(chip);

    // Space opens; clicking outside closes.
    fireEvent.keyDown(chip, { key: ' ' });
    expect(chip.getAttribute('aria-expanded')).toBe('true');
    fireEvent.mouseDown(document.body);
    expect(chip.getAttribute('aria-expanded')).toBe('false');
    expect(screen.queryByRole('radiogroup')).toBeNull();
  });

  it('ignores a stale GET response arriving after workspacePath changes', async () => {
    let resolveOld: (v: unknown) => void = () => {};
    mockGet.mockImplementationOnce(() => new Promise((r) => { resolveOld = r; }));
    mockGet.mockResolvedValueOnce(sceneAnswer('team', true, 'C:/work/NewProject'));

    const view = renderHeader({ projectName: 'OldProject', workspacePath: 'C:/work/OldProject' });
    view.rerender(
      <LangContext.Provider
        value={{
          lang: 'tr',
          setLang: () => {},
          t: (key, params) => ceviriUygula('tr', key, params),
        }}
      >
        <ThreadHeader
          conversation={sampleConv}
          projectName="NewProject"
          awaiting={false}
          api="http://127.0.0.1:8000"
          sessionToken="tok-123"
          workspacePath="C:/work/NewProject"
        />
      </LangContext.Provider>,
    );

    const chip = await waitFor(() => findChip());
    expect(chip.getAttribute('aria-label')).toBe('Sahne yazım modu: Ekip');

    await act(async () => {
      resolveOld(sceneAnswer('code', true, 'C:/work/OldProject'));
      await Promise.resolve();
    });

    expect(chip.getAttribute('aria-label')).toBe('Sahne yazım modu: Ekip');
  });

  it('hides and skips GET while workspacePath is null during a switch, then fetches once saved', async () => {
    mockGet.mockResolvedValueOnce(sceneAnswer('code', true, 'C:/work/ProjA'));
    mockGet.mockResolvedValueOnce(sceneAnswer('team', true, 'C:/work/ProjB'));

    const view = renderHeader({ projectName: 'ProjA', workspacePath: 'C:/work/ProjA' });
    const chip = await waitFor(() => findChip());
    expect(chip.getAttribute('aria-label')).toBe('Sahne yazım modu: Kod');
    expect(mockGet).toHaveBeenCalledTimes(1);

    const wrap = (projectName: string, workspacePath: string | null) => (
      <LangContext.Provider
        value={{
          lang: 'tr',
          setLang: () => {},
          t: (key, params) => ceviriUygula('tr', key, params),
        }}
      >
        <ThreadHeader
          conversation={sampleConv}
          projectName={projectName}
          awaiting={false}
          api="http://127.0.0.1:8000"
          sessionToken="tok-123"
          workspacePath={workspacePath}
        />
      </LangContext.Provider>
    );

    // Switch starts: savedWorkspacePath becomes null while UI shows ProjB.
    view.rerender(wrap('ProjB', null));
    await act(async () => { await Promise.resolve(); });
    expect(screen.queryByRole('button', { name: /Sahne yazım modu/i })).toBeNull();
    expect(mockGet).toHaveBeenCalledTimes(1);

    // Backend save completes: savedWorkspacePath becomes ProjB.
    view.rerender(wrap('ProjB', 'C:/work/ProjB'));
    const nextChip = await waitFor(() => findChip());
    expect(mockGet).toHaveBeenCalledTimes(2);
    expect(nextChip.getAttribute('aria-label')).toBe('Sahne yazım modu: Ekip');
  });

  it('renders English labels when language is en', async () => {
    mockGet.mockResolvedValueOnce(sceneAnswer('mixed'));
    renderHeader({}, 'en');

    const chip = await waitFor(() => findChip());
    expect(chip.getAttribute('aria-label')).toBe('Scene writing mode: Mixed');
    expect(chip.textContent).toContain('Scene:');
    expect(chip.textContent).toContain('Mixed');

    fireEvent.click(chip);
    const radios = screen.getAllByRole('radio');
    expect(radios.map((r) => r.querySelector('.scene-opt-name')?.textContent)).toEqual([
      'Code',
      'Mixed',
      'Team',
    ]);
  });
});

describe('SceneModeChip on the empty chat', () => {
  it('offers the mode before the first message of a Unity project', async () => {
    mockGet.mockResolvedValue(sceneAnswer('team'));
    render(
      <EmptyChat userName="Burak" projectName="BenchmarkTest4" onPick={() => {}}
        api="http://127.0.0.1:8000" sessionToken="tok-123" workspacePath="C:/work/BenchmarkTest4" />,
    );
    await waitFor(() => expect(findChip().getAttribute('aria-label')).toMatch(/Ekip/));
  });

  it('shows no chip without a saved workspace', async () => {
    mockGet.mockResolvedValue(sceneAnswer('team'));
    render(
      <EmptyChat userName="Burak" projectName="BenchmarkTest4" onPick={() => {}}
        api="http://127.0.0.1:8000" sessionToken="tok-123" workspacePath={null} />,
    );
    await act(async () => { await Promise.resolve(); });
    expect(screen.queryByRole('button', { name: /Sahne yazım modu/i })).toBeNull();
    expect(mockGet).not.toHaveBeenCalled();
  });
});
