import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { parseArgs, classifyError } from '../scripts/live-youtube-acceptance.mjs';

test('live acceptance CLI parses bounded options',()=>{
  assert.deepEqual(parseArgs(['--channel','@Example','--max','999','--language','es','--json']),{channel:'@Example',maxVideos:50,language:'es',json:true,help:false});
  assert.equal(parseArgs([]).maxVideos,5);
});

test('live acceptance classifies network and no-caption errors',()=>{
  assert.equal(classifyError({code:'YOUTUBE_NETWORK_BLOCKED',message:'blocked'}),'BLOCKED');
  assert.equal(classifyError({code:'NO_CAPTIONS',message:'none'}),'SKIP');
  assert.equal(classifyError({code:'BROKEN_PROTOCOL',message:'bad'}),'FAIL');
});

test('live acceptance help is offline-safe',()=>{
  const r=spawnSync(process.execPath,['scripts/live-youtube-acceptance.mjs','--help'],{encoding:'utf8'});
  assert.equal(r.status,0,r.stderr);
  assert.match(r.stdout,/Exit codes:/);
  assert.match(r.stdout,/BLOCKED/);
});
