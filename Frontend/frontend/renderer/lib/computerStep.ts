export function userStopped(tool: string, output?: string): boolean {
  return (tool.startsWith('mcp__gamachineComputer__') || tool.startsWith('computer_'))
    && /"stop_reason"\s*:\s*"halted"/.test(output?.slice(0, 400) ?? '');
}
