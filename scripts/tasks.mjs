import { spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { resolve } from 'node:path';

const root = fileURLToPath(new URL('../', import.meta.url));
const python = resolve(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const npmCli = process.env.npm_execpath;

function run(command, args, cwd = root) {
  const result = spawnSync(command, args, { cwd, stdio: 'inherit' });
  if (result.error) throw result.error;
  if (result.status !== 0) process.exit(result.status ?? 1);
}

function backend(...args) {
  if (!existsSync(python)) throw new Error('Create .venv and install backend dependencies first. See README.md.');
  run(python, args, resolve(root, 'backend'));
}

function frontend(task) {
  if (!npmCli) throw new Error('Run this task using npm run.');
  run(process.execPath, [npmCli, '--prefix', 'frontend', 'run', task]);
}

const tasks = {
  test: () => { backend('-m', 'pytest'); frontend('test'); },
  lint: () => { backend('-m', 'ruff', 'check', '--config', 'pyproject.toml', '.', '../scripts/smoke_audio.py', '../scripts/benchmark_noise.py'); backend('-m', 'ruff', 'format', '--check', '--config', 'pyproject.toml', '.', '../scripts/smoke_audio.py', '../scripts/benchmark_noise.py'); frontend('lint'); },
  typecheck: () => { backend('-m', 'mypy', 'app', 'tests', '../scripts/smoke_audio.py', '../scripts/benchmark_noise.py'); frontend('typecheck'); },
  backend: () => backend('-m', 'uvicorn', 'app.main:create_app', '--factory', '--reload', '--host', '127.0.0.1', '--port', '8000'),
  check: () => { tasks.lint(); tasks.typecheck(); tasks.test(); frontend('build'); },
};
const task = process.argv[2];
if (!Object.hasOwn(tasks, task)) throw new Error(`Unknown task: ${task}`);
tasks[task]();
