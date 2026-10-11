export function memoryFixture(overrides = {}) {
  return {
    route: '识别附件结构，读取文本后获得候选，后续记录证据并整理报告。',
    goal: '找到题目要求的 Flag', user_constraints: ['使用中文报告'],
    facts: [], hypotheses: [], rejected_routes: [],
    approach: ['按证据选择最小实验'], next_steps: ['检查剩余目标'], blockers: [], corrections: [], achieved_capabilities: [], remaining_gap: '', references: [],
    ...overrides
  };
}
