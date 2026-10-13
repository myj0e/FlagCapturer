import { discoverSkills } from '../skills/catalog.js';
import { loadSdk } from '../pi/sdk.js';
import { assistantBlocks, assistantDelta, ThinkingBuffer } from './assistant-output.js';
import { basename, resolve, join } from 'node:path';
import { access, stat } from 'node:fs/promises';
import { randomUUID } from 'node:crypto';
import { TuiAltScreen, ProcessTerminal, matchesKey, getKeybindings, isKeyRelease } from '@earendil-works/pi-tui';
import { ModelCatalog } from '../pi/catalog.js';
import { authenticationErrorMessage, safeModelError } from '../pi/auth-errors.js';
import { solve } from '../application/solve.js';
import { inspectResume, listResumable } from '../application/resume.js';
import { importFile, importRemote, preview } from '../challenge/workspace.js';
import { parseConnectionDescription, connectionSummary } from '../challenge/connection.js';
import { publicUrl } from '../urls.js';
import { report } from '../application/report.js';
import { readRegular, invariant } from '../util.js';
import { summaryDocument } from './solving-summary.js';
import { ToolActivity, toolCardText, provisioningText } from './activity.js';
import { WorkbenchView, clean } from './workbench-view.js';
import { openBrowser } from './browser.js';
import { createWorkbenchAutocomplete, findFd, pathArgument } from './autocomplete.js';
export const COMMANDS = [['skills', '查看可用 Skill'], ['menu', '打开操作菜单'], ['login', '配置 API key 或登录服务'], ['model', '选择服务与模型'], ['thinking', '选择思考强度'], ['thoughts', '查看思考原文 [编号]'], ['import', '导入附件或打开 workspace'], ['target', '创建远端题目：URL、连接命令或自然语言'], ['run', '开始解题'], ['resume', '选择记录，恢复解题'], ['stop', '停止、保存续接记录并清理'], ['summary', '阅读完整解题进展'], ['state', '查看业务状态'], ['flags', '查看全部候选'], ['writeup', '阅读解题报告'], ['report', '生成并阅读运行报告'], ['endpoint', '添加自定义 API'], ['image', '选择本地工具镜像'], ['network', '查看授权 / add URL / revoke ID / 授权文件 / off'], ['memory', '设置受审记忆 / off'], ['logout', '移除服务的已保存凭据'], ['help', '查看操作说明'], ['quit', '退出工作台']];
const HELP = `配置：/login [provider] [oauth|api_key] → /model → /thinking\n题目：/import <附件或 workspace 路径> 或 /target <目标地址或连接说明> → /run\n\n/skills 查看指南，/skill:名称 [要求] 显式使用；空闲时选择下次运行的 Skill。\n输入普通文字保存补充要求；运行时直接发送给 agent。Enter 发送，Shift+Enter 换行。/ 指令补全，Tab 接受，↑↓ 选择，Esc 关闭；路径与 @ 引用支持文件补全。\nCtrl+P 打开菜单，F4 展开/折叠思考；/thoughts [编号] 阅读原文。F3 切换日志与摘要浏览（窄屏打开全文），PgUp/PgDn 翻页，Ctrl+C 停止当前操作；空闲时退出。\n\n/run 会再次确认题目、模型和数据发送授权。/stop 会等待进程与容器清理。\n运行中可以改变思考强度，更换模型或登录服务需要先停止。\n\n/state [section] [id|latest] 查看状态、产物和实验，/flags 查看候选；/writeup、/report 在终端内阅读结果。\n/endpoint 添加兼容 API；也会发现已有 Pi models.json 的自定义服务。\n\n${COMMANDS.map(([name, description]) => `/${name}  ${description}`).join('\n')}`;

/** Coordinates the UI with application services; never implements an agent loop. */
export class Workbench {
  constructor({
    view,
    catalog,
    solveRun = solve,
    importChallenge = importFile,
    previewChallenge = preview,
    generateReport = report,
    openUrl = openBrowser,
    fdPath = null,
    options = {}
  }) {
    this.view = view;
    this.openUrl = openUrl;
    this.catalog = catalog;
    this.solveRun = solveRun;
    this.importChallenge = importChallenge;
    this.previewChallenge = previewChallenge;
    this.generateReport = generateReport;
    this.options = {
      runsRoot: 'runs',
      importsRoot: 'data/imports',
      image: 'ctfbot-tools:candidate',
      ...catalog.preferences,
      ...options
    };
    this.toolActivity = new ToolActivity();
    this.active = null;
    this.operation = null;
    this.workspace = null;
    this.controls = null;
    this.result = null;
    this.guidance = '';
    this.queue = Promise.resolve();
    this.view.onSubmit = text => this.submit(text);
    this.autocomplete = createWorkbenchAutocomplete(this, COMMANDS, {
      fdPath
    });
    this.view.editor.setAutocompleteProvider(this.autocomplete);
    this.refreshSelection();
    view.log('工作台', '先用 /login 配置服务、/model 选择模型，再 /import 导入附件或 /target 创建远端题目。Ctrl+P 打开菜单。');
  }
  refreshSelection() {
    if (this.view.state.provider !== this.options.provider || this.view.state.model !== this.options.model) this.view.state.context = undefined;
    this.model = this.catalog.runtime.getModel(this.options.provider, this.options.model);
    const levels = this.model ? this.catalog.thinking(this.model) : ['off'];
    if (!levels.includes(this.options.thinkingLevel)) this.options.thinkingLevel = levels.includes('medium') ? 'medium' : levels[0];
    Object.assign(this.view.state, {
      provider: this.options.provider ?? '',
      model: this.options.model ?? '',
      thinking: this.options.thinkingLevel,
      contextWindow: this.model?.contextWindow
    });
    this.view.changed();
  }
  async persist() {
    const {
      provider,
      model,
      thinkingLevel,
      image
    } = this.options;
    await this.catalog.save({
      ...(provider ? {
        provider
      } : {}),
      ...(model ? {
        model
      } : {}),
      thinkingLevel,
      image
    });
  }
  submit(text) {
    if (this.closing || !text.trim()) return;
    this.view.editor.setText('');
    if (!text.startsWith('/')) {
      this.view.editor.addToHistory(text);
      if (this.active) {
        if (!this.controls) return this.view.log('提示', '运行正在准备，请稍后补充线索。');
        Promise.resolve(this.controls.steer(text)).then(() => this.view.log('你 · 已排队', text)).catch(error => this.error(error));
      } else {
        this.guidance = text;
        this.view.log('你', text);
      }
      return;
    }
    const match = /^\/(\S+)(?:\s+([\s\S]*))?$/.exec(text);
    if (!match) return;
    if (match[1] === 'stop') return this.stop();
    if (match[1] === 'quit') return this.onQuit?.();
    this.queue = this.queue.then(() => this.dispatch(match[1], match[2]?.trim())).catch(error => this.error(error));
  }
  error(error) {
    if (!/已取消|aborted|cancelled/i.test(error.message)) this.view.log('错误', error.message);
  }
  idle() {
    invariant(!this.active, '请先 /stop，等待清理后再更换题目、服务或模型');
  }
  async availableSkills() {
    if (this.controls && this.active && this.controls.skills) return this.controls.skills();
    this.skillDiscovery ??= loadSdk().then(sdk => discoverSkills(sdk, { skillPaths: this.options.skillPaths, userDirectory: this.options.skillUserDirectory })).then(result => ({
      skills: result.skills.map(s => ({ name: s.name, description: s.description, file: s.filePath, explicit_only: s.disableModelInvocation })),
      diagnostics: result.diagnostics
    })).catch(error => { this.skillDiscovery = null; throw error; });
    return this.skillDiscovery;
  }
  async dispatch(command, arg) {
    if (this.closing) return;
    if (command.startsWith('skill:')) {
      const name = command.slice(6);
      invariant((await this.availableSkills()).skills.some(s => s.name === name), '未知 Skill，使用 /skills 查看');
      if (this.active) {
        invariant(this.controls, '运行正在准备，请稍后调用 Skill');
        await this.controls.steer(`/skill:${name}${arg ? ' ' + arg : ''}`);
        this.view.log('Skill', `${name} 已排队，将在当前模型轮次结束后送入`);
      } else {
        this.options.initialSkill = name;
        if (arg) this.guidance = [this.guidance, arg].filter(Boolean).join('\n');
        this.view.log('Skill', `下次 /run 使用 ${name}`);
      }
      return;
    }
    switch (command) {
      case 'skills': {
        const result = await this.availableSkills();
        return this.view.showDocument('可用 Skills', result.skills.map(s => `/${'skill:'}${s.name}${s.explicit_only ? '（仅显式调用）' : ''}\n${s.description}\n来源：${s.file}`).join('\n\n') + (result.diagnostics?.length ? '\n\n加载提示：\n' + result.diagnostics.map(d => d.message).join('\n') : ''));
      }
      case 'help':
        return this.view.showDocument('操作说明', HELP);
      case 'menu':
        {
          const selected = await this.view.choose('操作菜单', COMMANDS.filter(([name]) => name !== 'menu').map(([id, label]) => ({
            id,
            label: `/${id}`,
            description: label
          })));
          if (selected === 'quit') {
            void this.onQuit?.();
            return;
          }
          return selected === 'stop' ? this.stop() : this.dispatch(selected);
        }
      case 'login':
        this.idle();
        return this.login(arg);
      case 'logout':
        this.idle();
        return this.logout();
      case 'endpoint':
        this.idle();
        return this.endpoint();
      case 'model':
        this.idle();
        return this.selectModelReference(arg);
      case 'thinking':
        return this.selectThinking(arg);
      case 'import':
        this.idle();
        return this.openChallenge(arg);
      case 'target':
        this.idle();
        return this.openRemote(arg);
      case 'run':
        return this.run();
      case 'resume':
        this.idle();
        return this.resume(arg);
      case 'thoughts': {
        invariant(!arg || /^\d+$/.test(arg), '用法：/thoughts [编号]');
        const thoughts = this.view.entries.filter(entry => entry.kind === '思考');
        const entry = arg ? thoughts.find(entry => entry.thinkingIndex === Number(arg)) : thoughts.at(-1);
        invariant(entry, '没有可查看的思考记录；旧记录可能已移出界面缓存');
        return this.view.showDocument(`模型思考 #${entry.thinkingIndex}（服务返回，不代表工具执行）`, entry.text || '尚未收到思考正文');
      }
      case 'summary':
        this.poll();
        return this.view.showDocument('解题进展', summaryDocument(this.view.state));
      case 'state': {
        const [section = 'overview', id, extra] = (arg ?? '').trim().split(/\s+/).filter(Boolean);
        invariant(!extra, '用法：/state [section] [id|latest]');
        return this.view.showDocument('业务状态', JSON.stringify(this.controls?.state({ section, ...(id ? { id } : {}), limit: 20 }) ?? { status: '尚未运行' }, null, 2));
      }
      case 'flags':
        return this.view.showDocument('候选 Flag', JSON.stringify(this.controls?.candidates() ?? this.view.state.candidates, null, 2));
      case 'writeup':
        return this.document('writeup.md');
      case 'report':
        return this.document('report.md');
      case 'image':
      case 'network':
        if (command === 'network' && (this.controls || /^(add|revoke)(?: |$)/.test(arg ?? ''))) return this.networkCommand(arg);
      case 'memory':
        {
          this.idle();
          const value = arg ?? (await this.view.ask(command === 'image' ? '本地工具镜像' : command === 'network' ? '网络授权 JSON 路径（off 关闭）' : '受审记忆 JSON 路径（off 关闭）', {
            value: command === 'image' ? this.options.image : ''
          }));
          invariant(value.trim(), '值不能为空');
          const key = {
            image: 'image',
            network: 'networkGrant',
            memory: 'memoryPath'
          }[command];
          this.options[key] = value === 'off' && command !== 'image' ? undefined : command === 'image' ? value : pathArgument(value);
          if (command === 'image') await this.persist();
          this.view.log('配置', `${command}: ${this.options[key] ?? '关闭'}`);
          return;
        }
      default:
        throw new Error(`未知命令 /${command}，输入 /help 查看可用命令`);
    }
  }
  async providerChoice(title, filter = () => true) {
    return this.view.choose(title, this.catalog.providers().filter(filter).map(p => ({
      id: p.id,
      label: p.name,
      description: `${p.id} · ${p.configured ? '已配置' : '未配置'}`
    })));
  }
  async login(reference) {
    let requestedMethod;
    const suffix = reference?.match(/\s+(oauth|api_key)$/i);
    if (suffix) {
      requestedMethod = suffix[1].toLowerCase();
      reference = reference.slice(0, suffix.index);
    }
    const providers = this.catalog.providers();
    const normalized = reference?.trim().toLowerCase();
    const matched = normalized ? providers.find(p => p.id.toLowerCase() === normalized || p.name.toLowerCase() === normalized) : undefined;
    if (normalized) invariant(matched, `未知服务 ${reference}，请使用 /login 选择 Pi 提供的服务`);
    const provider = matched?.id ?? (await this.providerChoice('选择要配置的服务'));
    const entry = this.catalog.providers().find(p => p.id === provider);
    invariant(entry, '所选服务不可用');
    if (!entry.methods.length) return this.view.showDocument('使用环境认证', `${entry.name} 使用环境变量、系统 profile 或其他环境凭据。配置后重新启动工作台；也可选择其他服务。`);
    const methods = [...entry.methods].sort((a, b) => Number(b.id === 'oauth') - Number(a.id === 'oauth'));
    const method = requestedMethod ?? (methods.length === 1 ? methods[0].id : await this.view.choose('选择认证方式', methods, {
      selected: 'oauth',
      detail: entry.name + ' · 凭据由 Pi 管理，与 Pi 共用认证存储。'
    }));
    invariant(methods.some(item => item.id === method), '此服务不支持所选认证方式');
    const controller = new AbortController();
    this.operation = controller;
    this.view.state.status = '认证中';
    this.view.authNotes = [];
    const opened = new Set();
    let loginNotes = [];
    const progressNotes = [];
    let loginUrl;
    const launchBrowser = url => {
      loginUrl = url;
      if (opened.has(url)) return;
      opened.add(url);
      Promise.resolve().then(() => {
        if (!controller.signal.aborted) return this.openUrl(url);
      }).catch(() => {
        if (controller.signal.aborted) return;
        this.view.authNotes.push('无法自动打开浏览器，请复制上方完整链接到浏览器；Enter 重试。');
        this.view.changed();
      });
    };
    const wait = () => this.view.waitForAuth({
      signal: controller.signal,
      onOpen: () => {
        if (loginUrl) {
          opened.delete(loginUrl);
          launchBrowser(loginUrl);
        }
      }
    });
    let pendingWait;
    const prompt = async request => {
      if (this.view.modal?.authWait) this.view.modal.finish();
      const signal = request.signal ? AbortSignal.any([controller.signal, request.signal]) : controller.signal;
      if (request.type === 'select') return this.view.choose(request.message, request.options.map(x => ({
        id: x.id,
        label: x.label,
        description: x.description
      })), {
        signal
      });
      return this.view.ask(request.message, {
        secret: request.type === 'secret' || request.type === 'manual_code',
        detail: request.placeholder ?? '',
        signal
      });
    };
    try {
      await this.catalog.login(provider, method, {
        signal: controller.signal,
        prompt,
        notify: event => {
          if (controller.signal.aborted) return;
          if (event.type === 'auth_url') launchBrowser(event.url);else if (event.type === 'device_code') launchBrowser(event.verificationUri);
          const notes = event.type === 'auth_url' ? [event.instructions ?? '打开以下链接完成登录', event.url] : event.type === 'device_code' ? [`设备码：${event.userCode}`, event.verificationUri] : [event.message ?? '', ...(event.links ?? []).map(link => link.url)];
          if (event.type === 'auth_url' || event.type === 'device_code') loginNotes = notes.map(clean);else {
            progressNotes.push(...notes.map(clean));
            progressNotes.splice(0, Math.max(0, progressNotes.length - 4));
          }
          this.view.authNotes = [...loginNotes, ...progressNotes];
          if (!this.view.modal) {
            pendingWait = wait().catch(() => {
              controller.abort();
            });
            if (this.view.modal) this.view.modal.authWait = true;
          }
          this.view.changed();
        }
      });
      if (this.view.modal?.authWait) this.view.modal.finish();
      this.options.provider = provider;
      this.view.log('认证', `${entry.name} 已配置`);
    } catch (error) {
      const cancelled = controller.signal.aborted || /已取消|abort/i.test(error.message);
      controller.abort();
      if (cancelled) throw new Error('已取消');
      throw new Error(authenticationErrorMessage(error));
    } finally {
      controller.abort();
      if (this.view.modal?.authWait) this.view.modal.finish();
      await pendingWait;
      this.operation = null;
      this.view.authNotes = [];
      this.view.state.status = '就绪';
      this.view.changed();
    }
    await this.selectModel(provider);
  }
  async logout() {
    const stored = await this.catalog.runtime.listCredentials();
    invariant(stored.length, '没有已保存的服务凭据');
    const provider = await this.view.choose('移除服务凭据', stored.map(x => ({
      id: x.providerId,
      label: x.providerId,
      description: x.type
    })));
    if ((await this.view.choose('确认退出登录', [{
      id: 'no',
      label: '返回'
    }, {
      id: 'yes',
      label: '移除凭据'
    }], {
      detail: `将移除 Pi 中 ${provider} 的已保存凭据，环境变量配置不受影响。`
    })) !== 'yes') return;
    await this.catalog.logout(provider);
    this.view.log('认证', `${provider} 的已保存凭据已移除`);
  }
  async selectModelReference(reference) {
    if (!reference) return this.selectModel();
    const slash = reference.indexOf('/');
    if (slash >= 0) return this.selectModel(reference.slice(0, slash), reference.slice(slash + 1));
    if (this.catalog.providers().some(provider => provider.id === reference)) return this.selectModel(reference);
    return this.selectModel(this.options.provider, reference);
  }
  async selectModel(provider, selectedModel) {
    provider ??= await this.providerChoice('选择模型服务');
    const models = this.catalog.models(provider);
    invariant(models.length, '此服务没有可选聊天模型，可通过 /endpoint 配置兼容 API');
    if (selectedModel) invariant(models.some(model => model.id === selectedModel), `此服务没有模型 ${selectedModel}`);
    const id = selectedModel ?? (await this.view.choose('选择模型', models.map(model => ({
      id: model.id,
      label: model.name || model.id,
      description: `${model.id} · ${model.reasoning ? '支持思考' : '无思考模式'} · ${model.input?.includes('image') ? '图文' : '文本'} · ${model.contextWindow} context`
    })), {
      detail: this.catalog.providers().find(x => x.id === provider)?.name ?? provider,
      selected: provider === this.options.provider ? this.options.model : undefined
    }));
    this.options.provider = provider;
    this.options.model = id;
    this.refreshSelection();
    await this.persist();
    this.view.log('模型', `${provider}/${id}`);
    await this.selectThinking();
  }
  async selectThinking(selectedLevel) {
    invariant(this.model, '请先 /model 选择模型');
    const levels = this.catalog.thinking(this.model);
    if (selectedLevel) invariant(levels.includes(selectedLevel), `此模型不支持思考强度 ${selectedLevel}，可选：${levels.join(', ')}`);
    const level = selectedLevel ?? (await this.view.choose('选择思考强度', levels.map(id => ({
      id,
      label: id,
      description: id === 'off' ? '不启用思考' : id === this.options.thinkingLevel ? '当前选择' : ''
    })), {
      selected: this.options.thinkingLevel,
      detail: levels.length === 1 ? '此模型只支持 off。' : '只展示 Pi 声明支持的强度。运行中修改会在后续请求生效。'
    }));
    if (this.active) {
      invariant(this.controls, '运行尚未准备完成');
      this.controls.setThinkingLevel(level);
    }
    this.options.thinkingLevel = level;
    this.view.state.thinking = level;
    await this.persist();
    this.view.log('配置', `思考强度：${level}`);
  }
  async endpoint() {
    const name = await this.view.ask('自定义服务名称');
    const baseUrl = await this.view.ask('API Base URL', {
      value: 'https://',
      detail: '例如 https://example.com/v1，不包含密钥。'
    });
    const api = await this.view.choose('API 协议', [{
      id: 'openai-completions',
      label: 'OpenAI Chat Completions'
    }, {
      id: 'openai-responses',
      label: 'OpenAI Responses'
    }, {
      id: 'anthropic-messages',
      label: 'Anthropic Messages'
    }]);
    const model = await this.view.ask('模型 ID');
    const reasoning = (await this.view.choose('模型是否支持思考', [{
      id: 'no',
      label: '不支持 / 不确定'
    }, {
      id: 'yes',
      label: '支持'
    }])) === 'yes';
    const images = (await this.view.choose('模型是否支持图片', [{
      id: 'no',
      label: '仅文本 / 不确定'
    }, {
      id: 'yes',
      label: '支持图片'
    }])) === 'yes';
    const contextWindow = Number(await this.view.ask('上下文窗口 tokens', {
      value: '128000'
    }));
    const maxTokens = Number(await this.view.ask('最大输出 tokens', {
      value: '8192'
    }));
    const id = `ctf-custom-${randomUUID()}`;
    await this.catalog.addEndpoint({
      id,
      name,
      baseUrl,
      api,
      model,
      reasoning,
      images,
      contextWindow,
      maxTokens
    });
    this.view.log('配置', `已保存自定义服务 ${name}，成本估算未知。请 /login 配置 API key。`);
    this.options.provider = id;
    this.options.model = model;
    this.refreshSelection();
    await this.persist();
  }
  async openRemote(value) {
    value ??= await this.view.ask('目标地址或连接说明（URL、nc/socat/ssh 命令、自然语言）');
    const parsed = parseConnectionDescription(value);
    const task = await this.view.ask('题目目标', { value: '分析授权的 CTF 服务，找出 Flag，并用中文编写解题报告' });
    const yes = await this.view.choose('远端题目数据发送授权', [{ id: 'no', label: '取消' }, { id: 'yes', label: '允许向所选模型发送本题内容和远端输出' }], { confirmation: true, detail: `连接说明：${parsed.connection.description}\n${connectionSummary(parsed.connection)}\n运行前将确认实际允许连接的目标。` });
    if (yes !== 'yes') return;
    const checked = await importRemote(value, this.options.importsRoot, { task, authorized: true, basis: '用户在工作台明确授权本题与远端输出发送给所选模型' });
    this.workspace = checked; this.controls = null; this.options.targets = checked.manifest.targets;
    this.view.state.challenge = checked.manifest.id; this.view.state.consent = true; this.view.state.progress = { task: checked.task };
    this.view.log('题目', `已创建远端题目：${parsed.connection.description}\n${connectionSummary(parsed.connection)}\n/run 开始`);
  }
  async confirmNetwork(request, signal) {
    const hostKey = request.kind?.startsWith('ssh_host_key');
    const changedKey = request.kind === 'ssh_host_key_changed';
    const detail = hostKey ? `SSH 主机：${request.target.host}:${request.target.port}\n${changedKey ? `已固定指纹：${request.expected_fingerprint}\n` : ''}新指纹：${request.fingerprint}${changedKey ? '\n确认将替换本次运行的旧指纹。' : ''}` : `目标：${request.target.host}:${request.target.port}\nIP：${request.target.ips.join('、')}\n能力：${request.target.capabilities.join('、')}\n到期：${request.target.expires_at}`;
    try { return await this.view.choose(changedKey ? 'SSH 主机指纹已变化' : hostKey ? '确认 SSH 主机指纹' : '允许访问目标服务', [{ id: 'no', label: '拒绝' }, { id: 'yes', label: changedKey ? '确认替换指纹' : '允许本次运行使用' }], { confirmation: true, detail, signal }) === 'yes'; }
    catch (error) { if (signal?.aborted) throw error; return false; }
  }
  async networkCommand(arg) {
    invariant(this.controls, '请先开始运行；初始目标可用 /target 配置');
    if (!arg) return this.view.showDocument('当前网络授权', JSON.stringify(this.controls.network(), null, 2));
    if (arg.startsWith('add ')) { const result = await this.controls.grantTarget(arg.slice(4).trim()); this.view.log('网络', JSON.stringify(result)); return; }
    if (arg.startsWith('revoke ')) { await this.controls.revokeTarget(arg.slice(7).trim()); this.view.log('网络', '目标权限已撤销，相关连接已关闭'); return; }
    if (arg === 'off') { for (const target of this.controls.network().filter(t => t.state === 'active')) await this.controls.revokeTarget(target.id); this.view.log('网络', '已撤销全部目标'); return; }
    throw new Error('用法：/network [add URL | revoke ID | off]');
  }
  piUIContext() {
    return {
      confirm: async (title, message, opts = {}) => { try { return await this.view.choose(title, [{ id: 'no', label: '拒绝' }, { id: 'yes', label: '允许' }], { confirmation: true, detail: message, signal: opts.signal }) === 'yes'; } catch { return false; } },
      select: async (title, values, opts = {}) => this.view.choose(title, values.map(value => ({ id: value, label: value })), { signal: opts.signal }),
      input: (title, placeholder, opts = {}) => this.view.ask(title, { value: placeholder, signal: opts.signal }),
      notify: (message, type) => this.view.log(type ?? '提示', message),
      setStatus: () => {}, setWidget: () => {}, setWorkingMessage: () => {}, setWorkingVisible: () => {}, setWorkingIndicator: () => {}, setHiddenThinkingLabel: () => {}, setFooter: () => {}, setHeader: () => {}, onTerminalInput: () => () => {},
      custom: async () => { throw new Error('Use workbench dialogs for custom UI'); }
    };
  }
  async openChallenge(source) {
    source ??= await this.view.ask('附件文件或已有 workspace 路径');
    source = pathArgument(source);
    invariant(source.trim(), '路径不能为空');
    let checked;
    try {
      checked = await this.previewChallenge(source);
    } catch (error) {
      // Existing workspace admission failures must not silently become a new attachment import.
      if ((await stat(source)).isDirectory()) {
        try {
          await access(join(source, 'TASK.md'));
          throw error;
        } catch (probe) {
          if (probe === error) throw error;
          if (probe.code !== 'ENOENT') throw probe;
        }
      }
      const task = await this.view.ask('题目目标', {
        value: '分析附件，找出题目要求的 Flag，并用中文编写解题报告'
      });
      const consent = await this.view.choose('附件数据发送授权', [{
        id: 'no',
        label: '仅导入，暂不授权'
      }, {
        id: 'yes',
        label: '授权向所选模型发送附件和工具输出'
      }], {
        confirmation: true,
        detail: resolve(source)
      });
      const basis = consent === 'yes' ? '工作台中用户明确授权本题附件和工具输出发送给所选模型' : '';
      checked = await this.importChallenge(source, this.options.importsRoot, {
        task,
        authorized: consent === 'yes',
        basis
      });
    }
    const replacingWorkspace = Boolean(this.workspace);
    this.workspace = checked;
    this.controls = null;
    if (checked.manifest.targets?.length) this.options.targets = checked.manifest.targets;
    else if (replacingWorkspace) this.options.targets = [];
    this.view.summaryScroll = 0;
    this.view.summaryFocused = false;
    Object.assign(this.view.state, {
      challenge: basename(checked.root),
      consent: checked.authorized,
      context: undefined, progress: { task: checked.task }, candidates: [], currentActivity: null, latestActivity: null
    });
    this.view.log('题目', `${checked.root}\n${checked.manifest.files.map(file => `${file.path} · ${file.bytes} bytes`).join('\n')}\n模型传输：${checked.authorized ? '已授权' : '未授权'}`);
  }
  async resume(arg) {
    let directory = arg && (/^[a-f0-9-]{36}$/.test(arg) ? join(this.options.runsRoot, arg) : pathArgument(arg));
    if (!directory) {
      const records = await listResumable(this.options.runsRoot);
      invariant(records.length, '没有可续接的任务记录');
      directory = await this.view.choose('续接解题记录', records.map(record => ({ id: record.root,
        label: `${record.task} · ${record.status}`, description: `${record.time} · ${record.id}` })));
      if (!directory) return;
    }
    const saved = await inspectResume(directory);
    this.workspace = await this.previewChallenge(saved.workspace, { requireAuthorization: true });
    // Target hints belong to the resumed task; every endpoint must be approved again in the new run.
    this.options.targets = this.workspace.manifest.targets ?? [];
    if (!this.model) {
      this.options.provider = saved.selection.provider;
      this.options.model = saved.selection.model;
      this.options.thinkingLevel = saved.selection.thinkingLevel;
      this.refreshSelection();
    }
    this.view.state.challenge = basename(saved.root);
    this.view.state.consent = this.workspace.authorized;
    return this.run({ resumeFrom: saved.root, image: saved.started.identity.image });
  }
  async run(resumeOptions = {}) {
    this.idle();
    invariant(this.workspace, '请先 /import 导入附件或 /target 添加远端题目');
    invariant(this.workspace.authorized, '此 workspace 尚未授权模型传输，请重新导入并明确授权');
    invariant(this.model, '请先 /model 选择模型');
    const configured = this.catalog.runtime.getProviderAuthStatus(this.model.provider).configured;
    invariant(configured, '此服务尚未配置认证，请先 /login');
    const targets = this.options.targets?.length ? this.options.targets : this.workspace.manifest?.targets ?? [];
    const answer = await this.view.choose(resumeOptions.resumeFrom ? '续接本次解题' : '开始本次解题', [{
      id: 'no',
      label: '返回'
    }, {
      id: 'yes',
      label: '开始，允许使用模型额度'
    }], {
      confirmation: true,
      detail: `题目：${this.workspace.root}\n模型：${this.model.provider}/${this.model.id}\nAPI：${this.model.baseUrl}\n思考：${this.options.thinkingLevel}\n${resumeOptions.resumeFrom ? '续接：恢复已保存会话与文件；旧进程不恢复，当前网络授权重新校验。\n' : ''}附件与工具输出将发送给该服务。${this.workspace.manifest?.connection ? '\n连接要求：' + connectionSummary(this.workspace.manifest.connection) : ''}${targets.length ? '\n允许连接目标服务：' + targets.map(publicUrl).join('、') : '\n尚无初始网络授权；运行中申请缺失目标。'}`
    });
    if (answer !== 'yes') return;
    this.options.targets = targets;
    this.options.authorizeNetwork = targets.length > 0;
    const controller = new AbortController();
    const started = Date.now();
    this.controller = controller;
    this.controls = null;
    this.assistantEntries = new Map();
    Object.assign(this.view.state, {
      status: '准备运行',
      busy: true,
      candidates: [],
      tools: 0,
      elapsed: 0,
      runDir: '',
      usage: undefined,
      contextMaintenance: undefined,
      context: undefined
    });
    this.view.state.progress = { task: this.workspace.task };
    this.view.state.currentActivity = null;
    this.view.state.latestActivity = null;
    this.view.summaryScroll = 0;
    this.view.summaryFocused = false;
    this.toolActivity.reset();
    const timer = setInterval(() => this.poll(started), 500);
    this.active = Promise.resolve().then(() => this.executeRun(controller, timer, resumeOptions));
  }
  async executeRun(controller, timer, resumeOptions = {}) {
    try {
      this.result = await this.solveRun({
        ...this.options,
        ...resumeOptions,
        workspace: this.workspace.root,
        additionalPrompt: this.guidance,
        confirmModelUsage: true,
        modelRuntime: this.catalog.runtime,
        onNetworkRequest: (request, signal) => this.confirmNetwork(request, signal),
        piUIContext: this.piUIContext(),
        signal: controller.signal,
        onReady: controls => {
          this.controls = controls;
          this.contextSelection = `${this.options.provider}/${this.options.model}`;
        },
        onEvent: event => this.event(event)
      });
      this.view.state.status = this.result.status;
      this.view.state.runDir = this.result.run_dir;
      this.view.log('结果', `${this.result.status}\n${this.result.run_dir}${this.result.error ? '\n' + this.result.error : ''}${this.result.cleanup_error ? '\n清理未确认：' + this.result.cleanup_error.join('; ') : ''}`);
    } catch (error) {
      this.view.state.status = controller.signal.aborted ? '已停止' : '失败';
      this.error(error);
    } finally {
      clearInterval(timer);
      this.poll();
      this.active = null;
      this.controller = null;
      this.view.state.busy = false;
      this.view.state.currentActivity = null;
      this.view.changed();
    }
  }
  poll(started) {
    if (started) this.view.state.elapsed = Date.now() - started;
    if (this.controls) {
      try {
        this.view.state.candidates = this.controls.candidates();
        this.view.state.progress = this.controls.progress?.();
        this.view.state.networkTargets = this.controls.network?.();
        const stats = this.controls.stats();
        this.view.state.usage = stats.usage;
        if (stats.maintenance) this.view.state.contextMaintenance = stats.maintenance;
        this.view.state.context = this.contextSelection === `${this.options.provider}/${this.options.model}` ? stats.context : undefined;
      } catch {}
    }
    this.view.changed();
  }
  updateToolCard(activity) {
    const text = toolCardText(activity);
    if (activity.entry && this.view.entries.includes(activity.entry)) {
      activity.entry.text = clean(text);
      activity.entry.kind = activity.isError ? '工具失败' : '工具';
    } else {
      const entry = this.view.log(activity.isError ? '工具失败' : '工具', text);
      entry.presentation = 'tool';
    }
  }
  thinking(delta) {
    if (!this.thinkingMessage) {
      const entry = this.view.log('思考', '');
      Object.assign(entry, { thinkingIndex: ++this.view.thinkingIndex, thinkingPending: true, thinkingCount: 0 });
      this.thinkingMessage = { entry, buffer: new ThinkingBuffer() };
    }
    const { entry, buffer } = this.thinkingMessage;
    if (delta) buffer.add(delta.index, delta.text);
    entry.text = clean(buffer.text);
    entry.thinkingCount = buffer.count;
    return this.thinkingMessage;
  }
  finishThinking(message, interrupted = false) {
    const blocks = assistantBlocks(message).filter(block => block.kind === '思考');
    if (blocks.length || this.thinkingMessage) {
      const { entry, buffer } = this.thinking();
      if (blocks.length) buffer.replace(blocks);
      Object.assign(entry, { text: clean(buffer.text), thinkingCount: buffer.count, thinkingPending: false, thinkingInterrupted: interrupted });
    }
    this.thinkingMessage = null;
  }
  event(event) {
    if (event.type === 'run_started') {
      this.view.state.runDir = event.run_dir;
      this.view.state.status = '解题中';
      this.view.log('运行', `开始解题 · ${event.run_id ?? ''}\n证据：${event.run_dir}`);
    } else if (event.type === 'user_input_delivered') {
      this.view.log('补充', '已送入模型，将在下一次推理中处理');
    } else if (event.type === 'skills_ready') {
      this.view.log('Skills', `已${event.restored ? '恢复记录中的' : '加载'} ${event.count} 个指南（正文按需读取）${event.diagnostics?.length ? '\n' + event.diagnostics.map(d => d.message).join('\n') : ''}`);
    } else if (event.type === 'skill_loaded') {
      this.view.log('Skill', `使用 ${event.name}${event.truncated ? ' · 参考正文尚有后续内容' : ''}`);
    } else if (event.type === 'route_review_required') {
      this.view.log('路线复核', event.reason);
    } else if (event.type === 'tool_provisioning') {
      this.view.log('工具准备', provisioningText(event));
    } else if (event.type === 'run_resumed') {
      this.view.log('恢复', `已续接 ${event.parent_run_id}\n恢复 ${event.restored_files} 个文件位置 · 待处理 ${event.unresolved} 项\n历史进程与工具环境需要重新创建。`);
    } else if (event.type === 'context_refresh_start') {
      if (!event.background) this.view.state.currentActivity = { id: 'context-refresh', purpose: '整理当前状态与核心参考' };
      this.view.log('状态整理', event.background ? `后台更新进展与恢复路径（覆盖至第 ${event.sourceTurn} 轮），解题继续` : '开始更新进展与恢复路径');
    } else if (event.type === 'context_summary_repair') {
      this.view.log('状态整理', `${event.reason}，正在修复（${event.attempt}/${event.maxAttempts}）${event.kind === 'compaction' ? ' · 历史压缩' : ''}`);
    } else if (event.type === 'context_refresh_end') {
      if (this.view.state.currentActivity?.id === 'context-refresh') this.view.state.currentActivity = null;
      this.view.log('状态整理', event.errorMessage ?? `已更新：进展摘要、核心参考、恢复路径${event.warnings?.length ? '\n' + event.warnings.map(warning => warning.message).join('\n') : ''}`);
    } else if (event.type === 'message_start' && event.message?.role === 'assistant') {
      this.finishThinking(undefined, true);
      this.assistantEntries = new Map();
    } else if (assistantDelta(event)) {
      const delta = assistantDelta(event);
      if (delta.kind === '思考') { this.thinking(delta); this.view.trim(); this.poll(); return; }
      this.assistantEntries ??= new Map();
      let entry = this.assistantEntries.get(delta.index);
      if (!entry || !this.view.entries.includes(entry)) {
        entry = this.view.log(delta.kind, '');
        this.assistantEntries.set(delta.index, entry);
      }
      entry.text = (entry.text + clean(delta.text)).slice(-32768);
    } else if (event.type === 'message_end' && event.message?.role === 'assistant') {
      this.finishThinking(event.message, ['error', 'aborted'].includes(event.message.stopReason));
      for (const block of assistantBlocks(event.message).filter(block => block.kind === 'Agent')) {
        const entry = this.assistantEntries?.get(block.index) ?? this.assistantEntries?.get(block.kind);
        if (entry && this.view.entries.includes(entry)) entry.text = clean(block.text).slice(-32768);
        else this.view.log(block.kind, block.text);
      }
      this.assistantEntries = new Map();
    } else if (event.type === 'tool_execution_start') {
      this.finishThinking();
      this.assistantEntries = new Map();
      this.view.state.tools++;
      const activity = this.toolActivity.start(event);
      this.view.state.currentActivity = { id: event.toolCallId, purpose: activity.purpose };
      activity.entry = this.view.log('工具', toolCardText(activity));
      activity.entry.presentation = 'tool';
    } else if (event.type === 'tool_execution_update') {
      const activity = this.toolActivity.update(event);
      if (activity?.entry) activity.entry.text = clean(toolCardText(activity));
    } else if (event.type === 'tool_execution_end') {
      const activity = this.toolActivity.finish(event);
      this.updateToolCard(activity);
      this.view.state.latestActivity = { purpose: activity.purpose, status: activity.status };
      if (this.view.state.currentActivity?.id === event.toolCallId) this.view.state.currentActivity = null;
    } else if (event.type === 'run_finished') {
      this.finishThinking(undefined, true);
      this.view.state.currentActivity = null;
      for (const activity of this.toolActivity.interrupt(event.status === 'cancelled' ? '已取消' : '已中断')) this.updateToolCard(activity);
    } else if (event.type === 'compaction_start') this.view.log('上下文', '正在压缩历史，业务状态和证据继续保留');else if (event.type === 'compaction_end') this.view.log('上下文', event.errorMessage ? '压缩失败：' + event.errorMessage : '压缩结束');else if (event.type === 'auto_retry_start') this.view.log('重试', `第 ${event.attempt}/${event.maxAttempts} 次${event.errorMessage ? '\n原因：' + safeModelError(event.errorMessage) : ''}`);else if (event.type === 'extension_error') this.view.log('错误', 'Pi 扩展发生错误，请查看运行证据');
    this.view.trim();
    this.poll();
  }
  stop() {
    if (this.controller) {
      this.view.state.status = '正在停止并清理';
      this.controller.abort();
      this.view.changed();
    }
  }
  interrupt() {
    if (this.operation) {
      this.operation.abort();
      return;
    }
    if (this.active) {
      this.stop();
      return;
    }
    if (this.view.modal) {
      this.view.modal.finish(undefined, new Error('已取消'));
      return;
    }
    return this.onQuit?.();
  }
  async document(name) {
    invariant(this.result?.run_dir, '尚无运行结果');
    if (name === 'report.md') await this.generateReport(this.result.run_dir);
    let text;
    try {
      text = (await readRegular(join(this.result.run_dir, name), 1024 * 1024)).toString();
    } catch (error) {
      if (error.code === 'ENOENT') throw new Error('本次运行尚未产生解题报告');
      throw error;
    }
    return this.view.showDocument(name, text);
  }
  async close() {
    this.closing = true;
    this.view.editor.setText('');
    this.operation?.abort();
    this.view.modal?.finish(undefined, new Error('已取消'));
    this.stop();
    await this.active;
    await this.queue;
  }
}
export async function startWorkbench(options = {}, dependencies = {}) {
  invariant(process.stdin.isTTY && process.stdout.isTTY || dependencies.terminal, '交互工作台需要 TTY；非交互运行请使用 solve --help');
  const catalog = dependencies.catalog ?? (await ModelCatalog.create());
  const terminal = dependencies.terminal ?? new ProcessTerminal();
  const tui = dependencies.tui ?? new TuiAltScreen(terminal, true, undefined, {
    mouse: false,
    copyOnSelect: false
  });
  const view = new WorkbenchView(tui, terminal);
  const fdPath = dependencies.fdPath !== undefined ? dependencies.fdPath : await findFd();
  const app = new Workbench({
    view,
    catalog,
    fdPath,
    options,
    ...dependencies.services
  });
  tui.setLayoutRoot(view);
  tui.setFocus(view.editor);
  let finish;
  const done = new Promise(resolve => {
    finish = resolve;
  });
  let closing;
  app.onQuit = () => {
    closing ??= app.close().finally(finish);
    return closing;
  };
  const remove = tui.addInputListener(data => {
    if (isKeyRelease(data)) return;
    if (matchesKey(data, 'ctrl+c')) {
      app.interrupt();
      return {
        consume: true
      };
    }
    if (matchesKey(data, 'ctrl+q')) {
      app.onQuit();
      return {
        consume: true
      };
    }
    if (matchesKey(data, 'escape') && app.operation) {
      app.operation.abort();
      return {
        consume: true
      };
    }
    if (!view.modal && matchesKey(data, 'ctrl+p')) {
      app.submit('/menu');
      return {
        consume: true
      };
    }
    if (!view.modal && matchesKey(data, 'f4')) {
      view.toggleThinking();
      return { consume: true };
    }
    if (!view.modal && matchesKey(data, 'f3')) {
      if (terminal.columns < 110) app.submit('/summary');
      else { view.summaryFocused = !view.summaryFocused; view.changed(); }
      return { consume: true };
    }
    if (!view.modal && matchesKey(data, 'pageUp')) {
      view.scrollBy(10);
      return {
        consume: true
      };
    }
    if (!view.modal && matchesKey(data, 'pageDown')) {
      view.scrollBy(-10);
      return {
        consume: true
      };
    }
  });
  const interrupt = () => app.interrupt();
  const terminate = () => app.onQuit();
  process.on('SIGINT', interrupt);
  process.on('SIGTERM', terminate);
  const keybindings = getKeybindings();
  const previousBindings = keybindings.getUserBindings();
  try {
    // Pi's fullscreen listener runs first. Our fixed-height frame owns scrolling,
    // including documents/auth prompts, rather than Pi's outer viewport.
    keybindings.setUserBindings({ ...previousBindings, 'tui.altScreen.pageUp': [], 'tui.altScreen.pageDown': [] });
    tui.start();
    if (options.workspace) app.submit('/import ' + options.workspace);
    await done;
    await closing;
  } finally {
    keybindings.setUserBindings(previousBindings);
    remove();
    process.removeListener('SIGINT', interrupt);
    process.removeListener('SIGTERM', terminate);
    tui.stop({
      preserveScreen: true
    });
  }
  return {
    status: 'closed',
    last_run: app.result
  };
}
