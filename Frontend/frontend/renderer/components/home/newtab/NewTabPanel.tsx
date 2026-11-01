import React from 'react';

import { useAppearance, type Theme } from '../../../lib/appearance';
import { ArenaNewTab } from './ArenaNewTab';
import { AtolyeNewTab } from './AtolyeNewTab';
import { OzelNewTab } from './OzelNewTab';
import { PaftaNewTab } from './PaftaNewTab';
import { SadeNewTab } from './SadeNewTab';
import type { NewTabLayoutProps } from './parts';

/**
 * What the agent browser shows on a tab with no page yet. Same pattern as the mini Hub (HubPanel):
 * one engine (the sites from suggestedSites and onPick), five compositions, the theme picks the one.
 */
const LAYOUTS: Record<Theme, (p: NewTabLayoutProps) => React.ReactElement> = {
  arena: ArenaNewTab,
  sade: SadeNewTab,
  pafta: PaftaNewTab,
  atolye: AtolyeNewTab,
  ozel: OzelNewTab,
};

export const NewTabPanel = (props: NewTabLayoutProps & { theme?: Theme }) => {
  const { appearance } = useAppearance();
  const Layout = LAYOUTS[props.theme ?? appearance.theme] ?? ArenaNewTab;
  return <Layout sites={props.sites} onPick={props.onPick} />;
};
