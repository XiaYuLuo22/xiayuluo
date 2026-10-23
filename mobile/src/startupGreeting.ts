export const STARTUP_GREETING = '你好，我是夏鱼洛。今天想聊些什么，或有什么需要我帮忙的吗？';

/** A local welcome, not a conversation message or a microphone authorization. */
export class StartupGreeting {
  private announced = false;

  take(state: { ready: boolean; connected: boolean; online: boolean; foreground: boolean;
    visible: boolean; idle: boolean; speechReady: boolean }): string | null {
    if (this.announced || !state.ready || !state.connected || !state.online || !state.foreground ||
        !state.visible || !state.idle || !state.speechReady) return null;
    // Claim before any async playback. Reconnects, tab changes, status polling and
    // cancelled/muted speech must not replay the welcome during this App launch.
    this.announced = true;
    return STARTUP_GREETING;
  }
}
