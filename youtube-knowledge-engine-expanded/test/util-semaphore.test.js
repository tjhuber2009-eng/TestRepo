import test from 'node:test';
import assert from 'node:assert/strict';
import { AsyncSemaphore } from '../lib/semaphore.js';
import { readResponseTextLimited } from '../lib/util.js';

test('AsyncSemaphore bounds concurrent work', async()=>{
  const sem=new AsyncSemaphore(2); let active=0,max=0;
  await Promise.all(Array.from({length:8},(_,i)=>sem.run(async()=>{active++;max=Math.max(max,active);await new Promise(r=>setTimeout(r,4));active--;return i})));
  assert.equal(max,2); assert.equal(sem.active,0); assert.equal(sem.pending,0);
});

test('bounded response reader accepts small bodies and rejects oversized bodies', async()=>{
  assert.equal(await readResponseTextLimited(new Response('hello'),1024),'hello');
  await assert.rejects(()=>readResponseTextLimited(new Response('x'.repeat(2048)),1024),e=>e.code==='RESPONSE_TOO_LARGE');
  await assert.rejects(()=>readResponseTextLimited(new Response('tiny',{headers:{'content-length':'9000'}}),1024),e=>e.code==='RESPONSE_TOO_LARGE');
});
