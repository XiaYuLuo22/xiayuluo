import type { Asset, Release } from './updateProtocol.ts';

export type UpdatePhase = 'idle' | 'checking' | 'downloading' | 'verifying' | 'ready' | 'awaiting-system' | 'cancelled' | 'error';
export type UpdateState = { phase: UpdatePhase; message: string; version: string | null;
  received: number; total: number; cached: boolean };
export type UpdateDependencies<T> = {
  check(signal: AbortSignal): Promise<{ available: boolean; release: Release }>;
  download(asset: Asset, signal: AbortSignal, progress: (received: number) => void): Promise<T>;
  verify(file: T, asset: Asset): Promise<boolean>;
  launch(file: T): Promise<void>;
  discard(file: T): Promise<void>;
};

export function sameAndroidRelease(left: Release, right: Release): boolean {
  const a = left.assets['android-arm64'], b = right.assets['android-arm64'];
  return left.application === right.application && left.version === right.version && left.android_code === right.android_code
    && a.url === b.url && a.size === b.size && a.sha256 === b.sha256;
}

/** A system Intent is a handoff, never evidence that an APK was installed. */
export class AndroidUpdateInstaller<T> {
  private state: UpdateState = { phase: 'idle', message: '尚未开始更新。', version: null, received: 0, total: 0, cached: false };
  private listeners = new Set<(state: UpdateState) => void>();
  private operation: AbortController | null = null;
  private cached: { file: T; release: Release } | null = null;
  private readonly dependencies: UpdateDependencies<T>;
  constructor(dependencies: UpdateDependencies<T>) { this.dependencies = dependencies; }
  status(): UpdateState { return { ...this.state }; }
  subscribe(listener: (state: UpdateState) => void): () => void {
    this.listeners.add(listener); listener(this.status());
    return () => { this.listeners.delete(listener); };
  }
  private set(changes: Partial<UpdateState>) {
    this.state = { ...this.state, ...changes, cached: !!this.cached };
    for (const listener of this.listeners) { try { listener(this.status()); } catch {} }
  }
  cancel() {
    if (this.operation && ['checking', 'downloading', 'verifying'].includes(this.state.phase)) this.operation.abort();
  }
  async install(expected: Release): Promise<void> {
    if (this.operation) throw new Error('已有更新正在处理，请先等待或取消。');
    const abort = this.operation = new AbortController();
    let temporary: T | null = null;
    const active = () => { if (abort.signal.aborted) throw new Error('更新已取消，当前版本保留。'); };
    try {
      this.set({ phase: 'checking', version: expected.version, message: '正在重新核对签名更新清单…', received: 0, total: 0 });
      const latest = await this.dependencies.check(abort.signal); active();
      if (!latest.available || !sameAndroidRelease(latest.release, expected)) throw new Error('发布版本已变化或不再是新版，请重新检查更新。');
      const asset = latest.release.assets['android-arm64'];
      this.set({ total: asset.size });
      if (this.cached && !sameAndroidRelease(this.cached.release, latest.release)) {
        const previous = this.cached; this.cached = null;
        await this.dependencies.discard(previous.file); active();
      }
      if (!this.cached) {
        this.set({ phase: 'downloading', message: '正在下载 0%…' });
        temporary = await this.dependencies.download(asset, abort.signal, received => {
          active();
          if (!Number.isSafeInteger(received) || received < 0 || received > asset.size) throw new Error('安装包下载大小超限，已停止。');
          this.set({ received, message: `正在下载 ${Math.floor(received * 100 / asset.size)}%…` });
        });
        active();
      }
      const file = this.cached ? this.cached.file : temporary!;
      this.set({ phase: 'verifying', message: '正在校验安装包大小和 SHA-256…' });
      if (!await this.dependencies.verify(file, asset)) {
        if (this.cached) { this.cached = null; temporary = file; }
        throw new Error('安装包校验失败，已拒绝安装；当前版本保留。');
      }
      active();
      this.cached = { file, release: latest.release }; temporary = null;
      this.set({ phase: 'ready', received: asset.size, message: '安装包已校验，正在打开安卓系统安装器…' });
      active();
      this.set({ phase: 'awaiting-system', message: '等待安卓系统确认安装；返回 App 不代表安装完成。' });
      try { await this.dependencies.launch(file); }
      catch {
        this.set({ phase: 'ready', message: '安装包已保留，但未能打开系统安装器。请检查“安装未知应用”权限后重试；不要卸载旧版。' });
        throw new Error(this.state.message);
      }
      this.set({ phase: 'awaiting-system', message: '已交给系统安装器。若取消或被权限拦截，可再次打开安装器；不要卸载旧版。是否安装成功以重启后的版本为准。' });
    } catch (error) {
      if (abort.signal.aborted) this.set({ phase: 'cancelled', message: '更新已取消，当前版本与个人数据保留。' });
      else if (this.state.phase !== 'ready') this.set({ phase: 'error', message: error instanceof Error ? error.message : '更新未完成，当前版本保留。' });
      throw new Error(this.state.message);
    } finally {
      if (temporary !== null) { try { await this.dependencies.discard(temporary); } catch {} }
      this.operation = null;
    }
  }
  async reopen(): Promise<void> {
    if (!this.cached) throw new Error('暂无已校验安装包，请先下载更新。');
    return this.install(this.cached.release);
  }
}
