// Enforce a text-only, one-turn Claude ACP session. No user/project tool setup.
import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';
const child = spawn(process.execPath, [process.argv[2]], { stdio: ['pipe', 'pipe', 'inherit'] });
const input = createInterface({ input: process.stdin });
input.on('line', (line) => {
  try {
    const msg = JSON.parse(line);
    if (['session/new', 'session/load', 'session/resume'].includes(msg.method)) {
      msg.params.mcpServers = [];
      msg.params.additionalDirectories = [];
      msg.params._meta = { claudeCode: { options: {
        tools: [], allowedTools: [], maxTurns: 1, settingSources: [],
        mcpServers: {}, agents: {}, promptSuggestions: false,
        allowDangerouslySkipPermissions: false,
        settings: { disableAllHooks: true, apiKeyHelper: '' }
      } } };
    }
    child.stdin.write(JSON.stringify(msg) + '\n');
  } catch { process.exitCode = 1; child.kill(); input.close(); }
});
input.on('close', () => child.stdin.end());
child.stdout.pipe(process.stdout);
child.on('error', () => { process.exitCode = 1; input.close(); });
child.on('exit', (code) => { process.exitCode = code ?? 1; input.close(); });
process.on('SIGTERM', () => { child.kill('SIGTERM'); input.close(); });
