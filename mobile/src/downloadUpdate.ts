/** A button press stays in-app: trusted check -> optional consent -> verified install. */
export async function downloadUpdate<T>(options: {
  check(): Promise<{ available: boolean; release: T }>;
  approve(release: T): Promise<boolean>;
  install(release: T): Promise<void>;
  noUpdate(): void;
}): Promise<boolean> {
  const result = await options.check();
  if (!result.available) { options.noUpdate(); return false; }
  if (!await options.approve(result.release)) return false;
  await options.install(result.release);
  return true; // Installation was handed off, not proof the new App is installed.
}
