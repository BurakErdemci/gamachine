export function userStopped(tool: string, output?: string): boolean {
  return (tool.startsWith('mcp__gamachineComputer__') || tool.startsWith('computer_'))
    && /"stop_reason"\s*:\s*"halted"/.test(output?.slice(0, 400) ?? '');
}

function plainObject(value: unknown): value is Record<string, unknown> {
  if (value === null || typeof value !== 'object') return false;
  const prototype = Object.getPrototypeOf(value);
  return prototype === Object.prototype || prototype === null;
}

function truncate(text: string, limit: number): string {
  return text.length > limit ? text.slice(0, limit - 1) + '…' : text;
}

function describeStep(step: unknown): string {
  if (!plainObject(step)) return String(step);
  const shorthand = Object.keys(step).find(key => !['wait_for', 'expect', 'timeout_ms'].includes(key));
  const tool = typeof step.tool === 'string' ? step.tool : shorthand;
  if (!tool) {
    return ['wait_for', 'expect']
      .filter(key => step[key] !== undefined)
      .map(key => `${key} ${truncate(JSON.stringify(step[key]), 80)}`).join(' ');
  }
  const args = (typeof step.tool === 'string' ? step.args : step[tool]);
  if (!plainObject(args)) return tool;
  const parts = [tool];
  const text = args.text ?? args.value;
  if (text !== undefined) parts.push(truncate(JSON.stringify(text), 60));
  if (args.key !== undefined) {
    parts.push([...(Array.isArray(args.modifiers) ? args.modifiers : []), args.key].join('+'));
  } else if (Array.isArray(args.keys)) {
    parts.push(args.keys.join('+'));
  }
  if (args.role != null || args.name != null) {
    const target = [args.role == null ? '' : String(args.role)];
    if (args.name != null) target.push(JSON.stringify(args.name));
    if (args.nth != null) target.push(`#${args.nth}`);
    parts.push(`→ ${target.filter(Boolean).join(' ')}`);
  } else if (typeof args.element_token === 'string') {
    parts.push(`→ token ${args.element_token.slice(0, 8)}`);
  } else if (args.x != null && args.y != null) {
    parts.push(`→ (${args.x}, ${args.y})`);
  }
  return parts.join(' ');
}

export function describeComputerArgs(tool: string, args: unknown): { meta: string[]; steps: string[] } | null {
  if (!(tool.startsWith('mcp__gamachineComputer__') || tool.startsWith('computer_')) || !plainObject(args)) {
    return null;
  }
  return {
    meta: Object.entries(args).filter(([key]) => key !== 'steps' && key !== 'observe')
      .map(([key, value]) => `${key}: ${typeof value === 'string' ? JSON.stringify(value) : String(value)}`),
    steps: tool.endsWith('run_steps') && Array.isArray(args.steps) ? args.steps.map(describeStep) : [],
  };
}

interface ComputerOutput {
  executed: number;
  total: number;
  stopped: boolean;
  reason: string | null;
  failedStep: number | null;
  steps: { index: number; ok: boolean; message: string }[];
}

export function describeComputerOutput(output?: string): ComputerOutput | null {
  if (!output) return null;
  let report: unknown;
  try {
    report = JSON.parse(output);
  } catch {
    // Batch headers precede the step list, so they survive output truncation.
    const header: Record<string, unknown> = {};
    for (const key of ['executed', 'total', 'failed_step', 'stop_reason', 'halt_reason']) {
      const match = new RegExp(`"${key}"\\s*:\\s*("(?:\\\\.|[^"\\\\])*"|null|-?\\d+(?:\\.\\d+)?(?:[eE][+-]?\\d+)?)`).exec(output);
      if (match) header[key] = JSON.parse(match[1]);
    }
    report = header;
  }
  if (!plainObject(report) || typeof report.executed !== 'number' || typeof report.total !== 'number') {
    return null;
  }
  return {
    executed: report.executed,
    total: report.total,
    stopped: report.stop_reason === 'halted',
    reason: typeof report.halt_reason === 'string' ? report.halt_reason || null : null,
    failedStep: typeof report.failed_step === 'number' ? report.failed_step : null,
    steps: Array.isArray(report.steps) ? report.steps.filter(plainObject)
      .filter(step => typeof step.index === 'number' && typeof step.ok === 'boolean')
      .map(step => ({
        index: step.index as number,
        ok: step.ok as boolean,
        message: typeof step.message === 'string' ? truncate(step.message, 120) : '',
      })) : [],
  };
}
