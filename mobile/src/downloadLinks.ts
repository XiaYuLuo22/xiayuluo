export function downloadPage(repository: string, platform: 'windows' | 'android'): string {
  if (typeof repository !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9-]{0,38}\/[A-Za-z0-9_.-]{1,100}$/.test(repository)
    || ['.', '..'].includes(repository.split('/')[1])) throw new Error('下载仓库配置无效。');
  return `https://github.com/${repository}/${platform === 'windows' ? 'actions/workflows/windows-app.yml' : 'releases'}`;
}
