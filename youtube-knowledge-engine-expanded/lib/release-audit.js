import { lstat, readdir, readFile } from 'node:fs/promises';
import { basename, extname, join, relative, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

const FORBIDDEN_DIRS = new Set([
  '.git', 'node_modules', 'coverage', '.nyc_output', '.cache',
  'data', 'monitoring', 'ai-checkpoints', 'checkpoints',
]);
const FORBIDDEN_EXTENSIONS = new Set([
  '.pem', '.key', '.p12', '.pfx', '.sqlite', '.sqlite3', '.db',
  '.mp3', '.wav', '.m4a', '.mp4', '.webm', '.mov', '.zip', '.tar', '.tgz', '.gz', '.7z',
]);
const TEXT_EXTENSIONS = new Set([
  '', '.js', '.mjs', '.cjs', '.json', '.md', '.txt', '.yml', '.yaml', '.html', '.css',
  '.sh', '.bat', '.rs', '.toml', '.example', '.dockerignore', '.gitignore', '.webmanifest', '.svg',
]);
const SECRET_PATTERNS = [
  { code: 'PRIVATE_KEY', regex: /-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----/ },
  { code: 'OPENAI_STYLE_TOKEN', regex: /\bsk-[A-Za-z0-9_-]{20,}\b/ },
  { code: 'GITHUB_TOKEN', regex: /\bgh(?:p|o|u|s|r)_[A-Za-z0-9]{20,}\b/ },
  { code: 'GOOGLE_API_KEY', regex: /\bAIza[0-9A-Za-z_-]{30,}\b/ },
];
const SENSITIVE_ASSIGNMENT = /\b(TRANSCRIPTION_API_KEY|RESEARCH_API_KEY|EMBEDDING_API_KEY|AI_ACCESS_TOKEN|MONITOR_ACCESS_TOKEN)[ \t]*=[ \t]*([^\s#'"`]+)/g;
const PLACEHOLDER_VALUE = /^(?:<.*>|\$\{?.*|your[-_].*|example.*|placeholder.*|changeme.*|change-me.*|use-a-.*|use-an-.*|none|null)$/i;

function portablePath(root, path) {
  return relative(root, path).split(sep).join('/');
}
function forbiddenName(name) {
  const lower = name.toLowerCase();
  if (lower === '.env') return 'ENV_FILE';
  if (lower.startsWith('.env.') && lower !== '.env.example') return 'ENV_FILE';
  if (lower === '.npmrc' || lower === '.netrc') return 'AUTH_CONFIG';
  if (/^(?:youtube-)?cookies?(?:[._-].*)?\.(?:txt|json|sqlite|db)$/i.test(name)) return 'COOKIE_FILE';
  if (/^id_(?:rsa|dsa|ecdsa|ed25519)(?:\.pub)?$/i.test(name) && !lower.endsWith('.pub')) return 'PRIVATE_KEY_FILE';
  if (/checkpoint.*\.json$/i.test(name)) return 'CHECKPOINT_FILE';
  if (/credentials?.*\.json$/i.test(name) || /client[_-]?secret.*\.json$/i.test(name)) return 'CREDENTIAL_FILE';
  if (FORBIDDEN_EXTENSIONS.has(extname(lower))) return 'RUNTIME_OR_SECRET_FILE';
  return '';
}
function inspectText(text, rel) {
  const findings = [];
  for (const { code, regex } of SECRET_PATTERNS) if (regex.test(text)) findings.push({ code, path: rel });
  const base=basename(rel).toLowerCase(),ext=extname(base);
  const configLike=base.startsWith('.env')||['.json','.yml','.yaml','.ini','.conf','.toml'].includes(ext);
  if(configLike)for (const match of text.matchAll(SENSITIVE_ASSIGNMENT)) {
    const value = String(match[2] || '').trim();
    if (value.length >= 20 && !PLACEHOLDER_VALUE.test(value)) findings.push({ code: 'CONFIGURED_SECRET', path: rel, variable: match[1] });
  }
  if (/^# Netscape HTTP Cookie File\b/m.test(text) && /\.youtube\.com|youtube\.com/m.test(text)) findings.push({ code: 'YOUTUBE_COOKIE_EXPORT', path: rel });
  return findings;
}

export async function auditReleaseTree(root, { maxFileBytes = 10 * 1024 * 1024, maxTotalBytes = 50 * 1024 * 1024 } = {}) {
  root = root instanceof URL ? fileURLToPath(root) : String(root);
  const violations = [];
  let totalBytes = 0;
  async function walk(dir) {
    const entries = await readdir(dir, { withFileTypes: true });
    for (const entry of entries) {
      const path = join(dir, entry.name); const rel = portablePath(root, path);
      const stat = await lstat(path);
      if (stat.isSymbolicLink()) { violations.push({ code: 'SYMLINK', path: rel }); continue; }
      if (stat.isDirectory()) {
        if (FORBIDDEN_DIRS.has(entry.name.toLowerCase())) { violations.push({ code: 'RUNTIME_DATA_DIR', path: rel }); continue; }
        await walk(path); continue;
      }
      if (!stat.isFile()) { violations.push({ code: 'SPECIAL_FILE', path: rel }); continue; }
      totalBytes += stat.size;
      if (stat.size > maxFileBytes) violations.push({ code: 'FILE_TOO_LARGE', path: rel, bytes: stat.size });
      const nameCode = forbiddenName(basename(path)); if (nameCode) violations.push({ code: nameCode, path: rel });
      const ext = extname(entry.name.toLowerCase());
      if (stat.size <= 2 * 1024 * 1024 && TEXT_EXTENSIONS.has(ext)) {
        const buf = await readFile(path); if (!buf.includes(0)) violations.push(...inspectText(buf.toString('utf8'), rel));
      }
    }
  }
  await walk(root);
  if (totalBytes > maxTotalBytes) violations.push({ code: 'TREE_TOO_LARGE', path: '.', bytes: totalBytes });
  violations.sort((a,b)=>a.path.localeCompare(b.path)||a.code.localeCompare(b.code));
  return { ok: violations.length === 0, totalBytes, violations };
}
