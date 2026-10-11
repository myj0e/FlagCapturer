import { Input, Editor, SelectList, CURSOR_MARKER, matchesKey, visibleWidth, truncateToWidth, wrapTextWithAnsi, stripTerminalSequences } from '@earendil-works/pi-tui';
import { statusline } from './statusline.js';
import { solvingSummary } from './solving-summary.js';
import { safeTerminal } from '../util.js';
export const clean = value => safeTerminal(stripTerminalSequences(String(value))).replaceAll('\t', '  ');
const cyan = text => `\x1b[36m${text}\x1b[0m`;
const muted = text => `\x1b[90m${text}\x1b[0m`;
const listTheme = {
  selectedPrefix: cyan,
  selectedText: cyan,
  description: muted,
  scrollInfo: muted,
  noMatch: muted
};
const fit = (text, width) => truncateToWidth(text, Math.max(1, width));
const pad = (text, width) => fit(text, width) + ' '.repeat(Math.max(0, width - visibleWidth(fit(text, width))));
const wrap = (text, width) => clean(text).split('\n').flatMap(line => wrapTextWithAnsi(line, Math.max(1, width)));

/** Pure terminal presentation. No credentials, model calls or business tools live here. */
export class WorkbenchView {
  constructor(tui, terminal) {
    this.tui = tui;
    this.terminal = terminal;
    this.state = {
      status: '就绪',
      provider: '',
      model: '',
      thinking: 'off',
      challenge: '',
      consent: false,
      candidates: [],
      elapsed: 0,
      tools: 0
    };
    this.entries = [];
    this.scroll = 0;
    this.summaryScroll = 0;
    this.summaryFocused = false;
    this.modal = null;
    this.authNotes = [];
    this.editor = new Editor(tui, {
      borderColor: cyan,
      selectList: listTheme
    }, {
      paddingX: 1
    });
    this.editor.onSubmit = text => this.onSubmit?.(text);
  }
  invalidate() {
    this.editor.invalidate();
    this.modal?.input?.invalidate();
  }
  changed() {
    this.tui.requestRender();
  }
  log(kind, text) {
    const entry = {
      kind,
      text: clean(text).slice(0, 32768)
    };
    this.entries.push(entry);
    this.trim();
    this.changed();
    return entry;
  }
  renderEntry(entry, width) {
    if (entry.presentation === 'tool') {
      return [...entry.text.split('\n').flatMap((line, index) => {
        if (line.startsWith('│ ')) {
          if (width <= 3) return wrap(line.slice(2), width).map(part => fit(part, width));
          return wrap(line.slice(2), width - 2).map(part => muted('│ ') + fit(part, width - 2));
        }
        const wrapped = wrap(line, width).map(part => fit(part, width));
        return index === 0 ? wrapped.map(part => entry.kind === '工具失败' ? `\x1b[31m${part}\x1b[0m` : cyan(part)) : wrapped.map(muted);
      }), ''];
    }
    if (entry.kind === 'Agent') return [cyan(fit('◉ 模型说明（公开）', width)), ...wrap(entry.text, Math.max(1, width - 2)).map(line => width > 2 ? '  ' + line : line), ''];
    return [...wrap(`[${entry.kind}] ${entry.text}`, width), ''];
  }
  trim() {
    while (this.entries.length > 500 || this.entries.reduce((n, x) => n + x.text.length, 0) > 256 * 1024) this.entries.shift();
  }
  scrollBy(delta) {
    if (this.summaryFocused && this.terminal.columns >= 110) this.summaryScroll = Math.max(0, this.summaryScroll - delta);
    else this.scroll = Math.max(0, this.scroll + delta);
    this.changed();
  }
  renderSummary(width, height) {
    const summary = solvingSummary(this.state);
    const top = [cyan(fit(summary.title + (this.summaryFocused ? ' · 浏览中' : ''), width)), ...wrap(summary.subtitle, width).map(muted)];
    if (summary.notice) top.push(...wrap(summary.notice, width).map(muted));
    const lines = summary.sections.flatMap(section => [
      '', cyan(fit(section.title, width)),
      ...section.items.flatMap(item => wrap(item, width - 2).map((line, i) => (i === 0 ? '• ' : '  ') + line))
    ]);
    const available = Math.max(0, height - top.length - 1);
    this.summaryScroll = Math.min(this.summaryScroll, Math.max(0, lines.length - available));
    const body = lines.slice(this.summaryScroll, this.summaryScroll + available);
    while (body.length < available) body.push('');
    const range = available ? `${this.summaryScroll + 1}–${Math.min(lines.length, this.summaryScroll + available)}/${lines.length}` : '';
    return [...top, ...body, muted(fit(`F3 切换 · ${range} · /summary`, width))].slice(0, height);
  }
  scrollAuth(modal, data) {
    if (!this.authNotes.length || !['pageUp', 'pageDown'].some(key => matchesKey(data, key))) return false;
    modal.authOffset = Math.max(0, (modal.authOffset ?? 0) + (matchesKey(data, 'pageUp') ? -10 : 10));
    this.changed();
    return true;
  }
  open(modal, signal) {
    if (signal?.aborted) return Promise.reject(signal.reason ?? new Error('已取消'));
    if (this.modal) return Promise.reject(new Error('已有待完成的输入'));
    return new Promise((resolve, reject) => {
      const abort = () => finish(undefined, signal.reason ?? new Error('已取消'));
      const finish = (value, error) => {
        signal?.removeEventListener('abort', abort);
        if (this.modal === modal) this.modal = null;
        modal.input?.setValue('');
        this.tui.setFocus(this.editor);
        this.changed();
        error ? reject(error) : resolve(value);
      };
      modal.finish = finish;
      this.modal = modal;
      signal?.addEventListener('abort', abort, {
        once: true
      });
      this.tui.setFocus(modal);
      this.changed();
    });
  }
  choose(title, choices, {
    signal,
    detail = '',
    selected
  } = {}) {
    const input = new Input({
      prompt: '搜索 > '
    });
    const list = new SelectList(choices.map(item => ({
      value: item.id,
      label: clean(item.label),
      description: clean(item.description ?? '')
    })), Math.max(3, Math.min(12, this.terminal.rows - 12)), listTheme);
    const initial = choices.findIndex(item => item.id === selected);
    if (initial >= 0) list.setSelectedIndex(initial);
    const modal = {
      title,
      detail,
      input,
      list,
      focused: false,
      render: width => input.render(width),
      invalidate: () => {}
    };
    list.onSelect = item => modal.finish(item.value);
    list.onCancel = () => modal.finish(undefined, new Error('已取消'));
    modal.handleInput = data => {
      if (this.scrollAuth(modal, data)) return;
      if (['up', 'down', 'enter', 'escape'].some(key => matchesKey(data, key))) list.handleInput(data);else {
        input.handleInput(data);
        list.setFilter(input.getValue());
      }
      this.changed();
    };
    return this.open(modal, signal);
  }
  ask(title, {
    secret = false,
    value = '',
    detail = '',
    signal
  } = {}) {
    const input = new Input({
      prompt: secret ? '密钥 > ' : '> '
    });
    input.setValue(value);
    const modal = {
      title,
      detail,
      input,
      secret,
      focused: false,
      render: width => input.render(width),
      invalidate: () => {}
    };
    input.onSubmit = text => modal.finish(text);
    input.onEscape = () => modal.finish(undefined, new Error('已取消'));
    modal.handleInput = data => {
      if (this.scrollAuth(modal, data)) return;
      input.handleInput(data);
      if (input.getValue().length > 32768) input.setValue(input.getValue().slice(0, 32768));
      this.changed();
    };
    return this.open(modal, signal);
  }
  showDocument(title, text) {
    const modal = {
      title,
      document: clean(text),
      offset: 0,
      render: () => [],
      invalidate: () => {}
    };
    modal.handleInput = data => {
      if (matchesKey(data, 'escape') || matchesKey(data, 'enter')) modal.finish();else if (matchesKey(data, 'up') || matchesKey(data, 'pageUp')) modal.offset = Math.max(0, modal.offset - (matchesKey(data, 'up') ? 1 : 10));else if (matchesKey(data, 'down') || matchesKey(data, 'pageDown')) modal.offset += matchesKey(data, 'down') ? 1 : 10;
      this.changed();
    };
    return this.open(modal);
  }
  waitForAuth({
    signal,
    onOpen
  }) {
    const modal = {
      title: '等待服务认证',
      detail: '已尝试打开浏览器。也可复制下方链接；Enter 重新打开，Esc 取消。',
      authWait: true,
      offset: 0,
      render: () => [],
      invalidate: () => {}
    };
    modal.handleInput = data => {
      if (matchesKey(data, 'escape')) modal.finish(undefined, new Error('已取消'));else if (matchesKey(data, 'enter')) onOpen();else if (matchesKey(data, 'up') || matchesKey(data, 'pageUp')) modal.offset = Math.max(0, modal.offset - (matchesKey(data, 'up') ? 1 : 10));else if (matchesKey(data, 'down') || matchesKey(data, 'pageDown')) modal.offset += matchesKey(data, 'down') ? 1 : 10;
      this.changed();
    };
    return this.open(modal, signal);
  }
  render(width) {
    const rows = Math.max(1, this.terminal.rows);
    width = Math.max(1, width);
    const s = this.state;
    const header = [cyan(fit(clean(` FlagCapturer  ·  ${s.status}  ·  ${s.challenge || '尚未导入题目'}`).replaceAll('\n', ' '), width)), fit(clean(` ${s.provider || '选择服务 /login'}  /  ${s.model || '选择模型 /model'}  ·  思考 ${s.thinking}`).replaceAll('\n', ' '), width)];
    const status = statusline(s);
    const footer = [muted(fit(status, width)), muted(fit(' /help 命令  Ctrl+P 菜单  Ctrl+C 停止/退出  F3 摘要/日志  PgUp/PgDn 翻页  Shift+Enter 换行', width))];
    let editorLines;
    if (this.modal) {
      const m = this.modal;
      if (m.input) m.input.focused = m.focused;
      editorLines = m.input ? m.secret ? [fit('密钥 > ' + '*'.repeat(Math.min([...m.input.getValue()].length, Math.max(0, width - 9))), width) + CURSOR_MARKER] : m.input.render(width) : [];
      footer[1] = muted(fit(m.authWait ? ' ↑↓ / PgUp/PgDn 浏览完整链接 · Enter 打开浏览器 · Esc 取消' : m.document ? ' ↑↓ / PgUp/PgDn 浏览 · Enter / Esc 返回' : this.authNotes.length ? ' PgUp/PgDn 浏览完整链接 · Enter 提交输入 · Esc 取消' : ' 输入过滤或填写 · Enter 确认 · Esc 取消', width));
    } else {
      this.editor.setAutocompleteMaxVisible(Math.max(3, Math.min(5, rows - 8)));
      editorLines = this.editor.render(width);
      if (this.editor.isShowingAutocomplete()) footer[1] = muted(fit(' ↑↓ 选择补全 · Tab 接受 · Enter 确认 · Esc 关闭 · Ctrl+P 菜单', width));
    }
    if (rows < 8 || width < 20) {
      const m = this.modal;
      const label = m?.title ?? `FlagCapturer · ${s.status}`;
      const input = m?.input ? m.secret ? '*'.repeat(Math.min(m.input.getValue().length, width - 1)) + CURSOR_MARKER : m.input.render(width).at(-1) : m?.document ? 'Enter / Esc 返回' : fit(this.editor.getText(), width - 1) + CURSOR_MARKER;
      return [...Array.from({
        length: rows - 1
      }, (_, i) => i === 0 ? fit(clean(label), width) : ''), fit(input ?? '', width)];
    }
    const editorHeight = Math.max(1, Math.min(editorLines.length, rows - 5));
    const cursorLine = editorLines.findIndex(line => line.includes(CURSOR_MARKER));
    // Pi renders its selected menu row with this prefix. Keep it and the cursor visible on resize.
    const selectedLine = !this.modal && this.editor.isShowingAutocomplete() ? editorLines.findIndex(line => clean(line).trimStart().startsWith('→ ')) : -1;
    if (selectedLine >= 0 && cursorLine >= 0 && selectedLine - cursorLine >= editorHeight) {
      editorLines = [editorLines[cursorLine], ...editorLines.slice(selectedLine - editorHeight + 2, selectedLine + 1)];
    } else {
      const editorOffset = selectedLine >= 0 ? Math.max(0, Math.min(cursorLine, editorLines.length - editorHeight)) : Math.max(0, cursorLine - editorHeight + 1);
      editorLines = editorLines.slice(editorOffset, editorOffset + editorHeight);
    }
    const height = Math.max(0, rows - header.length - footer.length - editorLines.length);
    let body;
    if (this.modal) {
      const m = this.modal;
      body = [cyan(fit(' ' + clean(m.title), width)), ...wrap(m.detail ?? '', width)];
      if (m.authWait) {
        const lines = this.authNotes.flatMap(x => wrap(x, width));
        const available = Math.max(1, height - body.length);
        m.offset = Math.min(m.offset, Math.max(0, lines.length - available));
        body.push(...lines.slice(m.offset, m.offset + available));
      } else if (this.authNotes.length) {
        const lines = this.authNotes.flatMap(x => wrap(x, width));
        const available = Math.max(1, height - body.length);
        m.authOffset = Math.min(m.authOffset ?? 0, Math.max(0, lines.length - available));
        body.push(...lines.slice(m.authOffset, m.authOffset + available));
      }
      if (m.document) {
        const lines = wrap(m.document, width);
        m.offset = Math.min(m.offset, Math.max(0, lines.length - height + 2));
        body.push(...lines.slice(m.offset, m.offset + Math.max(0, height - body.length)));
      } else if (m.list) body.push(...m.list.render(width));
      body = body.slice(0, height);
    } else {
      const sidebar = width >= 110;
      const sidebarWidth = Math.min(48, Math.max(34, Math.floor(width * 0.3)));
      const mainWidth = sidebar ? width - sidebarWidth - 3 : width;
      const lines = this.entries.flatMap(entry => this.renderEntry(entry, mainWidth));
      const maxScroll = Math.max(0, lines.length - height);
      this.scroll = Math.min(this.scroll, maxScroll);
      const end = lines.length - this.scroll;
      body = lines.slice(Math.max(0, end - height), end);
      while (body.length < height) body.push('');
      if (sidebar) {
        const info = this.renderSummary(sidebarWidth, height);
        body = body.map((line, i) => pad(line, mainWidth) + muted(' │ ') + (info[i] ?? ''));
      }
    }
    while (body.length < height) body.push('');
    return [...header, ...body, ...footer, ...editorLines].slice(0, rows);
  }
}
