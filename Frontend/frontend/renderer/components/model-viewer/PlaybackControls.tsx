import React from 'react';
import { useLang } from '../../lib/i18n';
import { fractionAtTime, formatTimecode, SPEEDS, type Speed } from './timeline';

export interface PlaybackControlsProps {
  /** Clip length in seconds. The caller renders nothing when there is no clip. */
  duration: number;
  time: number;
  playing: boolean;
  speed: Speed;
  onTogglePlay: () => void;
  /** Slider fraction in `0..1`; the owner converts it to clip time. */
  onSeek: (fraction: number) => void;
  onSpeedChange: (speed: Speed) => void;
}

// The range input is integer-stepped and scaled down on read: a float `step`
// makes browsers snap to their own rounding of the interval, which drifts the
// thumb away from the pointer on long clips.
const STEPS = 1000;

/**
 * The transport bar. Presentational and three-free on purpose — it is the only
 * part of the playback UI that can be exercised under jsdom, where no WebGL
 * context exists and the panel that owns the mixer never gets built.
 */
export const PlaybackControls: React.FC<PlaybackControlsProps> = ({
  duration, time, playing, speed, onTogglePlay, onSeek, onSpeedChange,
}) => {
  const { t } = useLang();
  const label = playing ? t('preview.pause') : t('preview.play');

  // The mockup's transport (`.pv-bar`): play / pause, the timeline, the frame counter. The
  // counter is a timecode pair: the clip's frame rate is not known reliably across formats, and a
  // made-up frame number would be a wrong number on screen.
  return (
    <div className="pv-bar">
      <button
        type="button"
        onClick={onTogglePlay}
        aria-label={label}
        title={label}
        className="pv-play"
      >
        {playing
          ? <svg className="ic" viewBox="0 0 20 20" aria-hidden="true"><path className="fill" d="M6 4.5h2.6v11H6zM11.4 4.5H14v11h-2.6z" /></svg>
          : <svg className="ic" viewBox="0 0 20 20" aria-hidden="true"><path className="fill" d="M6.5 4.5l9 5.5-9 5.5z" /></svg>}
      </button>

      <input
        type="range"
        min={0}
        max={STEPS}
        step={1}
        value={Math.round(fractionAtTime(time, duration) * STEPS)}
        onChange={e => onSeek(Number(e.target.value) / STEPS)}
        aria-label={t('preview.timeline')}
        title={t('preview.timeline')}
        className="pv-time"
      />

      <span className="pv-frame num">
        <span>{formatTimecode(time)}</span> / <span>{formatTimecode(duration)}</span>
      </span>

      <select
        value={speed}
        onChange={e => onSpeedChange(Number(e.target.value) as Speed)}
        aria-label={t('preview.speed')}
        title={t('preview.speed')}
        className="pv-speed"
      >
        {SPEEDS.map(value => (
          <option key={value} value={value}>{`${value}×`}</option>
        ))}
      </select>
    </div>
  );
};

export default PlaybackControls;
