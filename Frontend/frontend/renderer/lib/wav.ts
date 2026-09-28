/**
 * WAV encoding for voice dictation — pure functions, NO DOM.
 *
 * Separated from the recording hook on purpose: the hook cannot be tested
 * without stubbing getUserMedia/AudioContext/AudioWorklet, but the byte layout
 * is exactly the part the backend rejects when it is wrong (`stt_not_wav`,
 * `stt_wrong_format`). Keeping it DOM-free means the header can be asserted
 * byte by byte without a browser.
 *
 * The backend validates with the stdlib `wave` module and demands PCM 16-bit,
 * mono, 16000 Hz. Every constant here is that contract, not a preference.
 */

/** Backend limit: decoded WAV must be ≤ 2 MiB, else 413 `stt_too_large`. */
export const WAV_MAX_BYTES = 2 * 1024 * 1024;

/**
 * Hard stop for one recording.
 *
 * 60 s of 16 kHz mono 16-bit is 1,920,044 bytes — deliberately just under
 * WAV_MAX_BYTES, so the clock and the byte budget run out at nearly the same
 * moment and neither limit can be hit without the other being close.
 */
export const MAX_RECORD_MS = 60_000;

export const WAV_SAMPLE_RATE = 16000;

/**
 * Float samples [-1, 1] → signed 16-bit.
 *
 * Clamping is not defensive dressing: the worklet hands us raw microphone
 * float, which can exceed 1.0 on a loud peak, and an unclamped multiply wraps
 * around in the Int16Array store — a loud syllable would come back as a loud
 * syllable of the opposite sign, i.e. audible garbage the model cannot read.
 */
export function floatTo16BitPcm(input: Float32Array): Int16Array {
  const out = new Int16Array(input.length);
  for (let i = 0; i < input.length; i++) {
    const s = Math.max(-1, Math.min(1, input[i]));
    // Asymmetric scale: the negative side of int16 reaches -32768, the
    // positive side only 32767.
    out[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  return out;
}

/**
 * Decimate to 16 kHz by averaging each source window.
 *
 * Averaging rather than picking every Nth sample: plain sample-dropping is
 * aliasing with no low-pass in front of it, and the frequencies it folds back
 * land inside the speech band. Averaging is a crude box filter, but it is a
 * filter.
 */
export function downsampleTo16k(input: Float32Array, inputRate: number): Float32Array {
  if (inputRate === WAV_SAMPLE_RATE) return input;
  if (inputRate < WAV_SAMPLE_RATE) {
    throw new Error(`cannot upsample ${inputRate} Hz to ${WAV_SAMPLE_RATE} Hz`);
  }
  const ratio = inputRate / WAV_SAMPLE_RATE;
  const outLength = Math.floor(input.length / ratio);
  const out = new Float32Array(outLength);
  for (let i = 0; i < outLength; i++) {
    const start = Math.round(i * ratio);
    const end = Math.min(Math.round((i + 1) * ratio), input.length);
    let sum = 0;
    let count = 0;
    for (let j = start; j < end; j++) { sum += input[j]; count++; }
    out[i] = count > 0 ? sum / count : 0;
  }
  return out;
}

/** Byte count a WAV file with this many samples will occupy. */
export function wavByteLength(sampleCount: number): number {
  return 44 + sampleCount * 2;
}

/**
 * Wrap PCM samples in a canonical 44-byte RIFF/WAVE header.
 *
 * Everything is little-endian, which is what `wave` expects for RIFF (RIFX
 * big-endian exists but nothing here writes it).
 */
export function encodeWav16kMono(samples: Int16Array): Uint8Array {
  const dataBytes = samples.length * 2;
  const buffer = new ArrayBuffer(44 + dataBytes);
  const view = new DataView(buffer);

  const writeAscii = (offset: number, text: string) => {
    for (let i = 0; i < text.length; i++) view.setUint8(offset + i, text.charCodeAt(i));
  };

  writeAscii(0, 'RIFF');
  view.setUint32(4, 36 + dataBytes, true);   // chunk size = file size - 8
  writeAscii(8, 'WAVE');
  writeAscii(12, 'fmt ');                     // trailing space is part of the id
  view.setUint32(16, 16, true);               // fmt chunk body size (PCM)
  view.setUint16(20, 1, true);                // audio format 1 = PCM
  view.setUint16(22, 1, true);                // channels
  view.setUint32(24, WAV_SAMPLE_RATE, true);
  view.setUint32(28, WAV_SAMPLE_RATE * 2, true);  // byte rate = rate * blockAlign
  view.setUint16(32, 2, true);                // block align = channels * bytesPerSample
  view.setUint16(34, 16, true);               // bits per sample
  writeAscii(36, 'data');
  view.setUint32(40, dataBytes, true);

  for (let i = 0; i < samples.length; i++) view.setInt16(44 + i * 2, samples[i], true);
  return new Uint8Array(buffer);
}

/**
 * Chunked base64.
 *
 * `String.fromCharCode(...bytes)` on a 2 MiB array blows the argument-count
 * limit and throws RangeError ("Maximum call stack size exceeded") — the
 * failure would land exactly at the size limit we are allowed to send, i.e.
 * only on the longest recordings. The chunk is a multiple of 3 so each btoa
 * call encodes whole 3-byte groups and the pieces concatenate without padding
 * appearing in the middle.
 */
export function bytesToBase64(bytes: Uint8Array): string {
  // 32760: divisible by 3 and well under the ~64k spread/apply argument limit.
  const CHUNK = 32760;
  let binary = '';
  for (let i = 0; i < bytes.length; i += CHUNK) {
    const slice = bytes.subarray(i, Math.min(i + CHUNK, bytes.length));
    binary += String.fromCharCode.apply(null, slice as unknown as number[]);
  }
  return btoa(binary);
}

/**
 * Raw PCM samples → base64, with no RIFF header.
 *
 * The chunk-session route appends every chunk to one buffer that becomes a
 * single WAV, so a header in the middle of it would be decoded as audio: the backend contract is "raw 16 kHz mono
 * signed-16-bit little-endian PCM, NO WAV header". `Int16Array`'s own buffer is
 * host-endian, which is little-endian on every platform this ships to but is
 * not guaranteed by the spec — the DataView writes make the wire order the
 * thing the code states rather than the thing the CPU happens to do.
 */
export function int16ToBase64(samples: Int16Array): string {
  const bytes = new Uint8Array(samples.length * 2);
  const view = new DataView(bytes.buffer);
  for (let i = 0; i < samples.length; i++) view.setInt16(i * 2, samples[i], true);
  return bytesToBase64(bytes);
}
