/** One microphone-start attempt per root App launch, not per tab or foreground poll. */
export class VoiceAutostart {
  attempted = false;
  suspended = false;

  shouldStart(state: { ready: boolean; connected: boolean; online: boolean;
    foreground: boolean; available: boolean; running: boolean; busy: boolean }): boolean {
    if (state.running) this.attempted = true;
    if (this.attempted || this.suspended || !state.ready || !state.connected || !state.online ||
      !state.foreground || !state.available || state.busy) return false;
    this.attempted = true; // Includes permission rejection and service failures: no prompt/retry loop.
    return true;
  }

  stop() { this.suspended = true; this.attempted = true; }
  manualStart() { this.suspended = false; this.attempted = true; }
}
