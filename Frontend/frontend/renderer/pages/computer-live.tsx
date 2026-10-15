import React, { useEffect } from 'react';
import Head from 'next/head';
import { ComputerLiveIndicator } from '../components/home/ComputerLiveIndicator';

/**
 * The floating indicator's own window (main/helpers/computer-use-live.ts): frameless and
 * transparent, so the page drops the app's ground colour and shows only the tag.
 */
export default function ComputerLivePage() {
  useEffect(() => {
    const root = document.documentElement;
    root.classList.add('cul-window');
    return () => root.classList.remove('cul-window');
  }, []);
  return (
    <>
      <Head><title>Gamachine</title></Head>
      <ComputerLiveIndicator />
    </>
  );
}
