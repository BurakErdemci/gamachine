import React from 'react';

/**
 * The approved sidebar lockup: the mascot head + the letter-drawn GAMACHINE (maket index.html
 * `.brand-logo`, art from maskot/_build.py). The paths keep their original hex fills; shell.css
 * re-points them to the --mascot-* tokens under `.brand-head`, so each theme recolours the head
 * and `.brand.is-linked` can blink the visor. Inlined (not <use>) because the app has no shared
 * sprite and an inline group needs no id to stay unique.
 */
const EAR = 'M192 222L200 142Q203 126 216 134L268 180Z';
const EAR_TIP = 'M196 182L200 142Q203 126 216 134L248.2 162.5Z';
const EAR_LINE = 'M196 182L248.2 162.5';
const HEAD = 'M300 168C390 168 432 190 434 248L436 316Q436 374 384 376H216Q164 374 164 316L166 248C168 190 210 168 300 168Z';
const MIRROR = 'matrix(-1 0 0 1 600 0)';

const Ear = () => (
  <>
    <path fill="#2A2F3D" stroke="none" d={EAR} />
    <path fill="#FF6B3D" stroke="none" d={EAR_TIP} />
    <path fill="none" strokeWidth="6.7" d={EAR_LINE} />
    <path fill="none" d={EAR} />
  </>
);
const Cheek = () => (
  <>
    <ellipse cx="160" cy="272" rx="34" ry="46" fill="#FF6B3D" />
    <ellipse cx="146" cy="272" rx="11" ry="22" fill="#2A2F3D" />
  </>
);

const LETTERS: Array<[number, string]> = [
  [0, 'M18 0H88V22H24V78H64V64H46V42H88V82L70 100H18L0 82V18Z'],
  [102, 'M0 100L28 0H68L96 100H72L67 82H29L24 100ZM45.8 22L35.2 60H60.8L50.2 22Z'],
  [212, 'M0 100V12L12 0H28L55 40L82 0H110V100H86V44L62 76H48L24 44V100Z'],
  [336, 'M0 100L28 0H68L96 100H72L67 82H29L24 100ZM45.8 22L35.2 60H60.8L50.2 22Z'],
  [446, 'M18 0H82V22H24V78H82V100H18L0 82V18Z'],
  [542, 'M0 12L12 0H24V39H64V0H88V100H64V61H24V100H0Z'],
  [644, 'M0 12L12 0H24V100H0Z'],
  [682, 'M0 100V12L12 0H26L66 58V0H90V100H66L24 42V100Z'],
  [786, 'M0 12L12 0H76V22H24V39H64V61H24V78H76V100H0Z'],
];

export const BrandLogo: React.FC = () => (
  <svg className="brand-logo" viewBox="0 0 714.4 150" role="img" aria-label="Gamachine">
    <svg className="brand-head" viewBox="0 0 512 512" width="153.6" height="153.6" overflow="visible">
      <g transform="translate(-146 -79) scale(1.3)">
        {/* sticker outline: the whole silhouette drawn fat in the eye colour */}
        <g fill="#F1EEE6" stroke="#F1EEE6" strokeWidth="31.3" strokeLinejoin="round">
          <path d={EAR} />
          <path transform={MIRROR} d={EAR} />
          <ellipse cx="160" cy="272" rx="34" ry="46" />
          <ellipse cx="440" cy="272" rx="34" ry="46" />
          <ellipse cx="146" cy="272" rx="11" ry="22" />
          <ellipse cx="454" cy="272" rx="11" ry="22" />
          <path d={HEAD} />
        </g>
        <g stroke="#141826" strokeWidth="10.4" strokeLinejoin="round" strokeLinecap="round">
          <Ear />
          <g transform={MIRROR}><Ear /></g>
          <Cheek />
          <g transform={MIRROR}><Cheek /></g>
          <path fill="#2A2F3D" d={HEAD} />
          <path fill="#FF6B3D" d="M180 328Q300 360 420 328L417 352Q300 390 183 352Z" />
          <rect x="192" y="204" width="216" height="122" rx="30" fill="#FF6B3D" />
          <rect x="209" y="220" width="182" height="90" rx="18" fill="#141826" stroke="none" />
          <g fill="#F1EEE6" stroke="none">
            <ellipse cx="258" cy="265" rx="18" ry="23" />
            <ellipse cx="342" cy="265" rx="18" ry="23" />
          </g>
        </g>
      </g>
    </svg>
    <g className="brand-word" fill="#141826" fillRule="evenodd" transform="translate(180 44) scale(0.6)">
      {LETTERS.map(([x, d]) => <path key={x} transform={`translate(${x} 0)`} d={d} />)}
    </g>
  </svg>
);
