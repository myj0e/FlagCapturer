/** Explain known failure classes without displaying provider bodies, callback URLs or credentials. */
export function authenticationErrorMessage(error) {
  const causes = [];
  const seen = new Set();
  for (let item = error; item && !seen.has(item) && causes.length < 5; item = item.cause) {
    seen.add(item);
    causes.push(item);
  }
  const messages = causes.map(item => typeof item.message === 'string' ? item.message : '').join('\n');
  const codes = new Set(causes.map(item => item.code));
  let detail;
  if (codes.has('CTFBOT_AUTH_SETTINGS')) detail = '无法读取或保存 Pi 安装设置，请检查 Pi 配置目录的权限及 settings.json 格式。';else if (/device ID|installation.*UUID/i.test(messages)) detail = 'Pi 安装设备 ID 缺失或无效，请检查 Pi 全局 settings.json 中的 deviceId（UUID）。';else if (codes.has('EADDRINUSE') || /Port 1455 is in use/.test(messages)) detail = '本机 1455 回调端口被占用，请取消其他 Pi/Codex 的登录后重试。';else if (codes.has('EACCES') || codes.has('EPERM')) detail = '权限不足：请检查 Pi 设置/凭据文件的读写权限，以及本机 OAuth 回调监听权限。';else if (/OAuth state mismatch/.test(messages)) detail = '登录回调与本次授权不匹配，请使用本次新打开的页面完成登录。';else if (/Paste the full callback URL|pasted callback URL must start|Missing authorization code|Missing OAuth state/.test(messages)) detail = '回调 URL 不完整或无效，请粘贴浏览器最终跳转的完整 URL。';else if (/registration callback did not contain an issued client ID/.test(messages)) detail = '服务回调缺少已签发的 client ID，请重新进行浏览器登录。';else if (/token request failed \((\d{3})\)/.test(messages)) detail = `服务拒绝令牌交换（HTTP ${messages.match(/token request failed \((\d{3})\)/)[1]}），请重新登录并检查账户与网络。`;else if (/token response|OAuth grant did not include/.test(messages)) detail = '服务返回的 OAuth 令牌或授权范围不符合 Pi 的要求，请重新登录并检查 Pi/provider 兼容性。';else if (/ChatGPT authorization failed/.test(messages)) detail = 'ChatGPT 未完成或拒绝了本次授权，请检查浏览器中的授权结果。';else if (['ENOTFOUND', 'EAI_AGAIN', 'ECONNREFUSED', 'ECONNRESET', 'ETIMEDOUT', 'UND_ERR_CONNECT_TIMEOUT'].some(code => codes.has(code)) || /fetch failed/.test(messages)) detail = '认证网络连接失败，请检查 DNS、代理和到登录服务的网络连接。';else detail = '认证未完成。服务响应可能包含敏感信息，未直接显示；请检查浏览器授权结果与 Pi 配置后重试 /login。';
  if (error?.name === 'CredentialSynchronizationError') return '服务认证已完成，但 Pi 本地凭据/模型状态同步失败。' + detail;
  return detail;
}
