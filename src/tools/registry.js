import { validate } from './schema.js';
import { invariant, json } from '../util.js';
export class ToolRegistry {
  constructor(state, evidence) {
    this.state = state;
    this.evidence = evidence;
    this.definitions = new Map();
    this.queue = Promise.resolve();
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
      const payload = await tool.handler(args, signal);
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
