import { describe, expect, it, vi } from 'vitest';
import { createLiveRevealTracker } from '../renderer/lib/computerLiveReveal';

describe('createLiveRevealTracker', () => {
  it('reveals on the first active payload and ignores 20 more frames for that conversation', () => {
    const reveal = vi.fn();
    const track = createLiveRevealTracker(reveal);

    track({ active: true, conversationId: 3, frame: null });
    expect(reveal).toHaveBeenCalledOnce();
    for (let frame = 0; frame < 20; frame++) {
      track({ active: true, conversationId: 3, frame: `frame-${frame}` });
    }
    expect(reveal).toHaveBeenCalledOnce();
  });

  it('reveals again for the same conversation after an inactive payload', () => {
    const reveal = vi.fn();
    const track = createLiveRevealTracker(reveal);

    track({ active: true, conversationId: 3 });
    track({ active: false, conversationId: 3 });
    expect(reveal).toHaveBeenCalledOnce();
    track({ active: true, conversationId: 3 });
    expect(reveal).toHaveBeenCalledTimes(2);
  });

  it('reveals when the active conversation changes', () => {
    const reveal = vi.fn();
    const track = createLiveRevealTracker(reveal);

    track({ active: true, conversationId: 3 });
    track({ active: true, conversationId: 4 });
    expect(reveal).toHaveBeenCalledTimes(2);
    track({ active: true, conversationId: 4 });
    expect(reveal).toHaveBeenCalledTimes(2);
  });

  it('never reveals or throws for garbage, including around an inactive payload', () => {
    const reveal = vi.fn();
    const track = createLiveRevealTracker(reveal);
    const garbage = [null, undefined, 'x', 42, [], {}, { active: true },
      { active: true, conversationId: '3' }, { active: true, conversationId: 1.5 }];

    for (const raw of garbage) expect(() => track(raw)).not.toThrow();
    track({ active: false });
    for (const raw of garbage) expect(() => track(raw)).not.toThrow();
    expect(reveal).not.toHaveBeenCalled();
    track({ active: true, conversationId: 3 });
    track({ active: true, conversationId: 3 });
    expect(reveal).toHaveBeenCalledOnce();
  });

  it.each([undefined, null, '3', 1.5, NaN, Infinity])(
    'ignores an active payload with invalid conversationId %s without changing state', (conversationId) => {
      const reveal = vi.fn();
      const track = createLiveRevealTracker(reveal);

      track({ active: true, conversationId });
      expect(reveal).not.toHaveBeenCalled();
      track({ active: true, conversationId: 3 });
      track({ active: true, conversationId });
      track({ active: true, conversationId: 3 });
      expect(reveal).toHaveBeenCalledOnce();
    },
  );

  it.each([null, undefined, 'x', 42, [], {}, { active: 'true', conversationId: 3 },
    { active: false, conversationId: 3 }])('resets an active run on inactive input %s', (raw) => {
    const reveal = vi.fn();
    const track = createLiveRevealTracker(reveal);

    track({ active: true, conversationId: 3 });
    expect(() => track(raw)).not.toThrow();
    expect(reveal).toHaveBeenCalledOnce();
    track({ active: true, conversationId: 3 });
    expect(reveal).toHaveBeenCalledTimes(2);
  });

  it.each([0, -1])('accepts integer conversationId %s', (conversationId) => {
    const reveal = vi.fn();
    const track = createLiveRevealTracker(reveal);

    track({ active: true, conversationId });
    track({ active: true, conversationId });
    expect(reveal).toHaveBeenCalledOnce();
  });

  it('ignores an inactive payload from a chat other than the open one', () => {
    const reveal = vi.fn();
    const track = createLiveRevealTracker(reveal, id => id === 3);

    track({ active: true, conversationId: 3 });
    track({ active: false, conversationId: 8 });
    track({ active: true, conversationId: 3 });
    expect(reveal).toHaveBeenCalledOnce();
    track({ active: false, conversationId: 3 });
    track({ active: true, conversationId: 3 });
    expect(reveal).toHaveBeenCalledTimes(2);
  });

  it('reveals again when a chat that went idle while another chat was open starts a new run', () => {
    let open = 3;
    const reveal = vi.fn();
    const track = createLiveRevealTracker(reveal, id => id === open);

    track({ active: true, conversationId: 3 });
    open = 5;
    track({ active: false, conversationId: 3 });
    open = 3;
    track({ active: true, conversationId: 3 });
    expect(reveal).toHaveBeenCalledTimes(2);
  });
});
