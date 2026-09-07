import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { auditReleaseTree } from '../lib/release-audit.js';

async function fixture(files, fn) {
  const root = await mkdtemp(join(tmpdir(), 'yke-release-audit-'));
  try {
    for (const [name, content] of Object.entries(files)) {
      const path = join(root, name); await mkdir(join(path, '..'), { recursive: true }); await writeFile(path, content);
    }
    return await fn(root);
  } finally { await rm(root, { recursive: true, force: true }); }
}

const currentRoot = new URL('../', import.meta.url);
test('current source tree passes behavioral release security audit', async()=>{
  const r = await auditReleaseTree(currentRoot); assert.equal(r.ok, true, JSON.stringify(r.violations));
});
test('release audit rejects configured env files and secret tokens', ()=>fixture({
  '.env':'TRANSCRIPTION_'+'API_KEY='+'sk-'+'test-secret-token-'+'abcdefghijklmnopqrstuvwxyz\n',
  'README.md':'safe docs\n',
}, async root=>{
  const r=await auditReleaseTree(root); assert.equal(r.ok,false); assert.ok(r.violations.some(v=>v.code==='ENV_FILE')); assert.ok(r.violations.some(v=>v.code==='OPENAI_STYLE_TOKEN'));
}));
test('release audit rejects persisted AI checkpoints and monitor data', ()=>fixture({
  'ai-checkpoints/abc.json':'{"text":"private transcript"}',
  'monitoring/channel/state.json':'{"transcript":"durable user data"}',
}, async root=>{
  const r=await auditReleaseTree(root); assert.equal(r.ok,false); assert.ok(r.violations.filter(v=>v.code==='RUNTIME_DATA_DIR').length>=2);
}));
test('release audit rejects cookie exports, private keys, databases, and captured media', ()=>fixture({
  'youtube-cookies.txt':'# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t0\tSID\tsecret\n',
  'server.pem':'-----BEGIN '+'PRIVATE KEY-----\nabc\n-----END '+'PRIVATE KEY-----\n',
  'capture.mp3':'not-really-audio',
  'runtime.sqlite':'db',
}, async root=>{
  const r=await auditReleaseTree(root); assert.equal(r.ok,false);
  for(const code of ['COOKIE_FILE','RUNTIME_OR_SECRET_FILE']) assert.ok(r.violations.some(v=>v.code===code),`${code} missing`);
}));
test('release audit permits the checked-in env example with non-secret placeholders', ()=>fixture({
  '.env.example':'TRANSCRIPTION_API_KEY=\nAI_ACCESS_TOKEN=use-a-long-random-secret-on-public-deployments\n',
  'src/app.js':'export const ok=true;\n',
}, async root=>{
  const r=await auditReleaseTree(root); assert.equal(r.ok,true,JSON.stringify(r.violations));
}));
