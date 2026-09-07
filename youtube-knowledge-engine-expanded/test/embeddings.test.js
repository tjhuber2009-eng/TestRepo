import test from 'node:test';
import assert from 'node:assert/strict';
import { normalizeVector, parseEmbeddingPayload, embeddingProviderInfo } from '../lib/embeddings.js';

test('normalizes finite embedding vectors',()=>{const v=normalizeVector([3,4]);assert.ok(Math.abs(v[0]-.6)<1e-12);assert.ok(Math.abs(v[1]-.8)<1e-12)});
test('rejects zero and malformed embedding vectors',()=>{assert.throws(()=>normalizeVector([0,0]),/zero-length/i);assert.throws(()=>normalizeVector([1,Number.NaN]),/non-finite/i)});
test('parses OpenAI-compatible indexed embedding payloads in order',()=>{const out=parseEmbeddingPayload({data:[{index:1,embedding:[0,2]},{index:0,embedding:[2,0]}]},2);assert.equal(out.dimensions,2);assert.deepEqual(out.vectors,[[1,0],[0,1]])});
test('embedding provider fingerprint changes with model',()=>{const old={...process.env};process.env.EMBEDDING_API_URL='https://example.test/embed';process.env.EMBEDDING_MODEL='m1';process.env.EMBEDDING_API_KEY='k';delete process.env.EMBEDDING_AUTH;const a=embeddingProviderInfo();process.env.EMBEDDING_MODEL='m2';const b=embeddingProviderInfo();assert.notEqual(a.providerId,b.providerId);process.env=old});
