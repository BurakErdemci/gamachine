import React from 'react';

import { useAppearance, type Theme } from '../../../lib/appearance';
import { ArenaHub } from './ArenaHub';
import { AtolyeHub } from './AtolyeHub';
import { OzelHub } from './OzelHub';
import { PaftaHub } from './PaftaHub';
import { SadeHub } from './SadeHub';
import type { HubLayoutProps } from './parts';

/**
 * The mini Unity Hub on the welcome screen. One engine (useHub), five compositions: unlike the
 * rest of the app, where one DOM is restyled per theme, each theme here lays the Hub out its own
 * way, so the theme picks the component.
 */
const LAYOUTS: Record<Theme, (p: HubLayoutProps) => React.ReactElement> = {
  arena: ArenaHub,
  sade: SadeHub,
  pafta: PaftaHub,
  atolye: AtolyeHub,
  ozel: OzelHub,
};

export const HubPanel = (props: HubLayoutProps & { theme?: Theme }) => {
  const { appearance } = useAppearance();
  const Layout = LAYOUTS[props.theme ?? appearance.theme] ?? ArenaHub;
  return <Layout {...props} />;
};
