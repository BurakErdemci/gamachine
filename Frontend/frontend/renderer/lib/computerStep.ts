export function userStopped(tool: string, output?: string): boolean {
  if (!(tool.startsWith('mcp__gamachineComputer__') || tool.startsWith('computer_') || browserStepLabel(tool) !== null) || !output) return false;
  try {
    const report: unknown = JSON.parse(output);
    return plainObject(report) && report.stop_reason === 'halted';
  } catch {
    return /^\s*\{\s*"halted"\s*:\s*true\s*,\s*"stop_reason"\s*:\s*"halted"/.test(output);
  }
}

function plainObject(value: unknown): value is Record<string, unknown> {
  if (value === null || typeof value !== 'object') return false;
  const prototype = Object.getPrototypeOf(value);
  return prototype === Object.prototype || prototype === null;
}

function truncate(text: string, limit: number): string {
  return text.length > limit ? text.slice(0, limit - 1) + '…' : text;
}

export function unwrapComputerArgs(args: unknown): unknown {
  if (!plainObject(args)) return args;
  const keys = Reflect.ownKeys(args);
  return keys.length === 1 && keys[0] === 'args' && plainObject(args.args) ? args.args : args;
}

function formatValue(value: unknown): string {
  try {
    const text = value === null || typeof value === 'number' || typeof value === 'boolean'
      ? String(value) : JSON.stringify(value);
    return truncate(text ?? '?', 80);
  } catch {
    return '?';
  }
}

// Key names, roles and tokens read better bare; free text (typed text, names) stays quoted.
function bare(value: unknown): string {
  return typeof value === 'string' ? truncate(value, 80) : formatValue(value);
}

function describeStep(step: unknown): string {
  if (!plainObject(step)) return formatValue(step);
  const shorthand = Object.keys(step).find(key => !['wait_for', 'expect', 'timeout_ms'].includes(key));
  const tool = typeof step.tool === 'string' ? step.tool : shorthand;
  if (!tool) {
    return ['wait_for', 'expect']
      .filter(key => step[key] !== undefined)
      .map(key => `${key} ${formatValue(step[key])}`).join(' ');
  }
  const args = (typeof step.tool === 'string' ? step.args : step[tool]);
  if (!plainObject(args)) return tool;
  const parts = [tool];
  const text = args.text ?? args.value;
  if (text !== undefined) parts.push(formatValue(text));
  if (args.key !== undefined) {
    parts.push([...(Array.isArray(args.modifiers) ? args.modifiers : []), args.key].map(bare).join('+'));
  } else if (Array.isArray(args.keys)) {
    parts.push(args.keys.map(bare).join('+'));
  }
  if (args.role != null || args.name != null) {
    const target = [args.role == null ? '' : bare(args.role)];
    if (args.name != null) target.push(formatValue(args.name));
    if (args.nth != null) target.push(`#${bare(args.nth)}`);
    parts.push(`→ ${target.filter(Boolean).join(' ')}`);
  } else if (typeof args.element_token === 'string') {
    parts.push(`→ token ${bare(args.element_token.slice(0, 8))}`);
  } else if (args.x != null && args.y != null) {
    parts.push(`→ (${formatValue(args.x)}, ${formatValue(args.y)})`);
  }
  return parts.join(' ');
}

const BROWSER_STEP: Record<string, string> = {
  navigate: 'Sayfayı açtı', snapshot: 'Sayfayı okudu', click: 'Tıkladı', fill: 'Yazdı', select: 'Seçti',
  press: 'Tuşa bastı', scroll: 'Kaydırdı', back: 'Geri gitti', forward: 'İleri gitti', reload: 'Yeniledi',
  screenshot: 'Ekran görüntüsü aldı',
};

/** A short Turkish label for a browser_* tool step, or null for any other tool. */
export function browserStepLabel(tool: string): string | null {
  const name = tool.replace(/^mcp__gamachineBrowser__/, '');
  if (name === tool && !tool.startsWith('browser_')) return null;
  return BROWSER_STEP[name.replace(/^browser_/, '')] ?? 'Tarayıcıda bir adım attı';
}

export function describeComputerArgs(tool: string, args: unknown): { meta: string[]; steps: string[] } | null {
  args = unwrapComputerArgs(args);
  if (!(tool.startsWith('mcp__gamachineComputer__') || tool.startsWith('computer_') || browserStepLabel(tool) !== null) || !plainObject(args)) {
    return null;
  }
  return {
    meta: Object.entries(args).filter(([key]) => key !== 'steps' && key !== 'observe')
      .map(([key, value]) => `${key}: ${formatValue(value)}`),
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
    if (!/^\s*\{\s*"(?:executed|halted)"/.test(output)) return null;
    // Batch headers precede the step list, so they survive output truncation.
    const header: Record<string, unknown> = {};
    for (const key of ['executed', 'total', 'failed_step', 'stop_reason', 'halt_reason']) {
      const match = new RegExp(`"${key}"\\s*:\\s*("(?:\\\\.|[^"\\\\])*"|null|-?\\d+(?:\\.\\d+)?(?:[eE][+-]?\\d+)?)`).exec(output);
      if (!match) continue;
      if (/^-?\d/.test(match[1]) && !/^\s*[,}]/.test(output.slice(match.index + match[0].length))) continue;
      try {
        header[key] = JSON.parse(match[1]);
      } catch {
        // Malformed captures are missing fields, not rendering errors.
      }
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
