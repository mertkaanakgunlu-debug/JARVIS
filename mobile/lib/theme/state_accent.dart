import 'package:flutter/material.dart';
import '../models/conversation_state.dart';
import 'jarvis_theme.dart';

Color accentForState(ConversationState state) {
  switch (state) {
    case ConversationState.idle:
      return JarvisColors.cyan;
    case ConversationState.listening:
      return JarvisColors.cyan;
    case ConversationState.thinking:
      return JarvisColors.thinking;
    case ConversationState.working:
      return JarvisColors.thinking;
    case ConversationState.speaking:
      return JarvisColors.speaking;
  }
}

String labelForState(ConversationState state) {
  switch (state) {
    case ConversationState.idle:
      return 'ALL SYSTEMS NOMINAL';
    case ConversationState.listening:
      return 'LISTENING';
    case ConversationState.thinking:
      return 'REASONING';
    case ConversationState.working:
      return 'WORKING';
    case ConversationState.speaking:
      return 'RESPONDING';
  }
}
