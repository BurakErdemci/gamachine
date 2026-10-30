/** `isOpenChat`, when given, limits the reveal to the chat open in the UI; other chats' events are ignored. */
export function createLiveRevealTracker(reveal: () => void, isOpenChat?: (conversationId: number) => boolean): (raw: unknown) => void {
  let activeConversationId: number | null = null;

  return (raw: unknown) => {
    if (raw === null || typeof raw !== 'object' || Array.isArray(raw)) {
      activeConversationId = null;
      return;
    }
    const payload = raw as { active?: unknown; conversationId?: unknown };
    const conversationId = payload.conversationId;
    // Another chat going idle must not re-arm the reveal for the open chat.
    if (payload.active !== true && isOpenChat && typeof conversationId === 'number' && !isOpenChat(conversationId)) return;
    if (payload.active !== true) {
      activeConversationId = null;
      return;
    }
    if (typeof conversationId !== 'number' || !Number.isInteger(conversationId)) return;
    if (isOpenChat && !isOpenChat(conversationId)) return;
    if (activeConversationId === conversationId) return;
    activeConversationId = conversationId;
    reveal();
  };
}
