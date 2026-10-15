import React, { useEffect, useState } from 'react';
import Head from 'next/head';
import { ComputerLiveIndicator } from '../components/home/ComputerLiveIndicator';

/**
 * The floating indicator's own window (main/helpers/computer-use-live.ts): frameless and
 * transparent, so the page drops the app's ground colour and shows only the tag.
 */
export default function ComputerLivePage() {
  // The server renders in the default language and the window in the user's: drawing the tag only
  // after mount avoids a hydration mismatch (measured live: the dev error overlay covered the tag).
  const [mounted, setMounted] = useState(false);
  useEffect(() => {
    const root = document.documentElement;
    root.classList.add('cul-window');
    setMounted(true);
    return () => root.classList.remove('cul-window');
  }, []);
  return (
    <>
      <Head><title>Gamachine</title></Head>
      {mounted && <ComputerLiveIndicator />}
    </>
  );
}
