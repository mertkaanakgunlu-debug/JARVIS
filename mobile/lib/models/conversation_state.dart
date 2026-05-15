enum ConversationState {
  idle,
  listening,
  thinking,
  working,
  speaking;

  static ConversationState parse(String? value) {
    switch (value?.toLowerCase()) {
      case 'listening': return ConversationState.listening;
      case 'thinking':  return ConversationState.thinking;
      case 'working':   return ConversationState.working;
      case 'speaking':  return ConversationState.speaking;
      default:          return ConversationState.idle;
    }
  }
}
