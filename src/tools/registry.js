import { validate } from './schema.js';
import { invariant, json } from '../util.js';
import { AsyncLocalStorage } from 'node:async_hooks';
const repeatableExecution = new Set(['command_run', 'script_run', 'tool_exec']);
export class ToolRegistry {
  constructor(state, evidence) {
    this.state = state;
    this.evidence = evidence;
    this.definitions = new Map();
    this.queue = Promise.resolve();
    this.invocations = new AsyncLocalStorage();
    this.executionRequests = new Map();
    this.repeatNotices = new Set();
    for (const record of state.sections.observations.values()) {
      if (repeatableExecution.has(record.tool)) this.rememberExecution(record.request.sha256, record.id);
    }
  }
  rememberExecution(hash, id) {
    this.executionRequests.delete(hash);
    this.executionRequests.set(hash, id);
    if (this.executionRequests.size > 256) {
      const oldest = this.executionRequests.keys().next().value;
      this.executionRequests.delete(oldest);
      this.repeatNotices.delete(oldest);
    }
  }
  define(name, description, parameters, handler, {
    observe = false
  } = {}) {
    invariant(!this.definitions.has(name), `Duplicate tool ${name}`);
    this.definitions.set(name, {
      name,
      description,
      parameters,
      handler,
      observe
    });
  }
  // Also serialize direct application callers, independent of Pi's sequential execution setting.
  invoke(name, args, signal) {
    const pending = this.queue.then(() => this.execute(name, args, signal));
    this.queue = pending.catch(() => {});
    return pending;
  }
  async execute(name, args, signal) {
    invariant(!this.state.completion, 'No tools allowed after run completion');
    signal?.throwIfAborted();
    const tool = this.definitions.get(name);
    invariant(tool, `Unknown tool ${name}`);
    invariant(Buffer.byteLength(JSON.stringify(args)) <= 128 * 1024, 'Tool arguments exceed 128 KiB');
    validate(args, tool.parameters);
    const started = Date.now();
    const request = await this.evidence.artifact({
      name,
      args
    }, 'application/json');
    await this.evidence.append('tool_started', {
      name,
      request
    });
    try {
      let payload = await this.invocations.run({ name, args, request }, () => tool.handler(args, signal));
      const previous = repeatableExecution.has(name) && this.executionRequests.get(request.sha256);
      if (previous && !this.repeatNotices.has(request.sha256)) {
        payload = { ...payload, request_history: { previous_observation_id: previous,
          notice: 'An identical tool request was executed before. Input contents, environment and randomness may differ; this is a history reference, not a finding that this attempt is redundant.' } };
        this.repeatNotices.add(request.sha256);
      }
      let observation;
      if (tool.observe) observation = await this.state.add('observations', {
        tool: name,
        request,
        result: payload.image ? {
          ...payload,
          image: {
            artifact: payload.artifact
          }
        } : payload,
        context: this.state.identity
      });
      if (observation && repeatableExecution.has(name)) this.rememberExecution(request.sha256, observation.id);
      const result = observation ? {
        ...payload,
        observation_id: observation.id
      } : payload;
      const artifact = await this.evidence.artifact(result, 'application/json');
      await this.evidence.append('tool_finished', {
        name,
        request,
        result: artifact,
        duration_ms: Date.now() - started
      });
      const full = json(result.image ? {
        ...result,
        image: {
          artifact: result.artifact
        }
      } : result);
      const content = Buffer.byteLength(full) <= 16384 ? full : json({
        truncated: true,
        full_result: artifact,
        observation_id: observation?.id,
        ...(result.request_history ? { request_history: result.request_history } : {}),
        preview: Buffer.from(full).subarray(0, 8192).toString('utf8'),
        next_step: 'Read full_result with artifact_read'
      });
      return {
        content: [{
          type: 'text',
          text: content
        }, ...(result.image ? [result.image] : [])],
        details: {
          result: artifact,
          observation_id: observation?.id
        },
        ...(result.exit_code !== undefined && result.exit_code !== null && result.exit_code !== 0 && result.state !== 'closed' || ['failed', 'timed_out', 'output_limit'].includes(result.state) || result.timed_out || result.cancelled ? {
          isError: true
        } : {}),
        ...(name === 'run_complete' ? {
          terminate: true
        } : {})
      };
    } catch (error) {
      await this.evidence.append('tool_failed', {
        name,
        request,
        error: error.message,
        duration_ms: Date.now() - started
      });
      throw error;
    }
  }
}
