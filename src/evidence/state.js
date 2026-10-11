import { invariant, sha256 } from '../util.js';
export class RunState {
  constructor(evidence, identity) {
    this.evidence = evidence;
    this.identity = identity;
    this.revision = 0;
    this.completion = null;
    this.sections = Object.fromEntries(['observations', 'candidates', 'checks', 'claims', 'experiments', 'summaries', 'scripts', 'checkpoints', 'sources', 'assets', 'context_snapshots'].map(name => [name, new Map()]));
  }
  async add(section, value) {
    const records = this.sections[section];
    invariant(records, 'Unknown state section');
    const id = `${section}-${records.size + 1}`;
    const record = {
      ...value,
      id,
      time: new Date().toISOString()
    };
    const artifact = await this.evidence.artifact(record, 'application/json');
    await this.evidence.append('state_record', {
      section,
      id,
      artifact
    });
    records.set(id, record);
    this.revision++;
    return record;
  }
  select(section, ids = []) {
    invariant(Array.isArray(ids), 'Expected record IDs');
    return ids.map(id => {
      const record = this.sections[section]?.get(id);
      invariant(record, `Unknown ${section} ID: ${id}`);
      return record;
    });
  }
  read({
    section = 'overview',
    id,
    cursor,
    limit = 10
  }, sessions = []) {
    if (section === 'overview') return {
      ...this.identity,
      revision: this.revision,
      completion: this.completion,
      counts: Object.fromEntries(Object.entries(this.sections).map(([name, records]) => [name, records.size])),
      sessions
    };
    if (section === 'sessions') return {
      records: sessions,
      historical: false
    };
    const records = this.sections[section];
    invariant(records, 'Unknown state section');
    if (id === 'latest') return {
      record: [...records.values()].at(-1) ?? null
    };
    if (id) return {
      records: this.select(section, [id])
    };
    let offset = 0;
    if (cursor) {
      invariant(/^\d+:\d+$/.test(cursor), 'Invalid cursor');
      const [revision, index] = cursor.split(':').map(Number);
      invariant(revision === this.revision, 'State changed; restart pagination');
      offset = index;
    }
    const all = [...records.values()];
    return {
      records: all.slice(offset, offset + limit),
      revision: this.revision,
      cursor: offset + limit < all.length ? `${this.revision}:${offset + limit}` : null
    };
  }
  async complete(args) {
    invariant(!this.completion, 'Run already completed');
    const outcome = args.outcome === 'candidate_unverified' ? 'completed' : args.outcome;
    const ids = [...new Set(args.candidate_ids ?? [])];
    this.select('candidates', ids);
    invariant(['completed', 'partial', 'unsolved'].includes(outcome), 'Invalid completion outcome');
    invariant(outcome === 'unsolved' ? this.sections.candidates.size === 0 && ids.length === 0 : ids.length > 0, 'Completion inconsistent with candidates');
    invariant(outcome !== 'completed' || args.unresolved.length === 0, 'Completed cannot have unresolved subtasks');
    invariant(outcome !== 'partial' || args.unresolved.length > 0, 'Partial requires unfinished subtasks');
    invariant(ids.length === this.sections.candidates.size, 'Completion must include every submitted candidate');
    invariant(args.writeup.trim() && args.summary.trim(), 'Writeup and summary are required');
    const writeup = await this.evidence.artifact(args.writeup, 'text/markdown');
    await this.evidence.document('writeup.md', args.writeup);
    const result = {
      outcome,
      candidate_ids: ids,
      unresolved: args.unresolved,
      summary: args.summary,
      writeup,
      verified: false,
      platform_verification_required: false
    };
    await this.evidence.document('result.json', result);
    await this.evidence.append('run_completed', result);
    this.completion = result;
    this.revision++;
    return result;
  }
  async submit(args) {
    this.select('observations', args.observation_ids);
    const digest = sha256(args.candidate);
    const checks = this.select('checks', args.check_ids);
    invariant(checks.every(x => x.candidate_sha256 === digest), 'Check belongs to another candidate');
    return this.add('candidates', {
      ...args,
      candidate_sha256: digest,
      verified: false,
      subtask_completed: true,
      platform_verification_required: false
    });
  }
}
