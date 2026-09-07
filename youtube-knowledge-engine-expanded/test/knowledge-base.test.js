import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const root=new URL('../',import.meta.url);

test('v2 ships a persistent multi-channel knowledge-base UI',async()=>{const html=await readFile(new URL('public/index.html',root),'utf8');assert.match(html,/id="knowledgeBase"/);assert.match(html,/id="kbSyncButton"/);assert.match(html,/id="kbCollectionFilter"/);assert.match(html,/id="kbSourceFilter"/);assert.match(html,/id="kbQuestion"/);assert.match(html,/id="kbTopics"/);assert.match(html,/src="\/kb\.js"/)});

test('knowledge-base stores are created consistently',async()=>{for(const file of ['public/app.js','public/kb.js','public/search-worker.js']){const src=await readFile(new URL(file,root),'utf8');assert.match(src,/kb_sources/);assert.match(src,/kb_videos/);assert.match(src,/kb_meta/);assert.match(src,/kb_staging/)}const kb=await readFile(new URL('public/kb.js',root),'utf8');assert.match(kb,/createIndex\('by-source','sourceId'\)/);assert.match(kb,/createIndex\('by-collection','collectionId'\)/)});

test('channel sync stages before atomic source replacement',async()=>{const kb=await readFile(new URL('public/kb.js',root),'utf8');assert.match(kb,/stageCurrentLibrary/);assert.match(kb,/commitSourceSync/);assert.match(kb,/transaction\(\['kb_videos','kb_sources','kb_staging'\],'readwrite'\)/);assert.match(kb,/staging\.openCursor/);assert.match(kb,/videos\.index\('by-source'\)/);assert.doesNotMatch(kb,/dbClear\('videos'\)/)});

test('knowledge retrieval supports search, cross-channel evidence and topic discovery',async()=>{const worker=await readFile(new URL('public/search-worker.js',root),'utf8');assert.match(worker,/type==='kb-search'/);assert.match(worker,/type==='kb-research'/);assert.match(worker,/type==='kb-topics'/);assert.match(worker,/perSource:6/);assert.match(worker,/matchesKbFilters/)});

test('knowledge backup is integrity chained and restored atomically',async()=>{const kb=await readFile(new URL('public/kb.js',root),'utf8');assert.match(kb,/type:'ykb-backup',schema:1/);assert.match(kb,/type:'end',sources,videos,chain/);assert.match(kb,/footer\.chain!==chain/);assert.match(kb,/transaction\(\['kb_sources','kb_videos','kb_meta','kb_staging'\],'readwrite'\)/);assert.match(kb,/Transcript integrity failed/)});

test('research evidence preserves channel provenance',async()=>{const research=await readFile(new URL('lib/research.js',root),'utf8');assert.match(research,/sourceId:/);assert.match(research,/sourceTitle:/);assert.match(research,/Preserve channel\/source distinctions/)});

test('service worker caches the knowledge-base module',async()=>{const sw=await readFile(new URL('public/sw.js',root),'utf8');assert.match(sw,/\/kb\.js/)});

test('startup avoids automatic whole-corpus search and topic scans',async()=>{const kb=await readFile(new URL('public/kb.js',root),'utf8');assert.match(kb,/initWorker\(\);await Promise\.all\(\[refreshUi\(\),checkHealth\(\)\]\);renderResults\(\);renderEvidence\(\);markTopicsStale\(\);/);assert.match(kb,/function knowledgeStats\(\)\{const videos=state\.sources\.reduce/)});

test('cross-channel search reports source coverage',async()=>{const html=await readFile(new URL('public/index.html',root),'utf8');const worker=await readFile(new URL('public/search-worker.js',root),'utf8');const kb=await readFile(new URL('public/kb.js',root),'utf8');assert.match(html,/id="kbCoverage"/);assert.match(worker,/sourceMatches=new Map/);assert.match(worker,/sources=\[\.\.\.sourceMatches\.values\(\)\]/);assert.match(kb,/matches across \$\{fmt\(state\.searchSources\.length\)\} channels/)});

test('v2.1 ships resumable multi-channel ingestion queue controls',async()=>{const html=await readFile(new URL('public/index.html',root),'utf8');const ingest=await readFile(new URL('public/ingest.js',root),'utf8');assert.match(html,/id="kbQueueChannels"/);assert.match(html,/id="kbQueueRun"/);assert.match(ingest,/youtube-knowledge-ingest-queue/);assert.match(ingest,/kb_staging/);assert.match(ingest,/Upstream circuit breaker/);assert.match(ingest,/type:'queue-video'/)});

test('v2.1 semantic retrieval is derived, hash-bound, and optional',async()=>{const html=await readFile(new URL('public/index.html',root),'utf8');const semantic=await readFile(new URL('public/semantic.js',root),'utf8');const worker=await readFile(new URL('public/semantic-worker.js',root),'utf8');assert.match(html,/id="semanticIndexButton"/);assert.match(html,/id="semanticSearchMode"/);assert.match(semantic,/youtube-knowledge-semantic/);assert.match(semantic,/contentHash/);assert.match(semantic,/videoHash/);assert.match(semantic,/Unchanged passages are reused/);assert.match(semantic,/mode==='semantic'\?semanticNorm/);assert.match(semantic,/semanticNorm\*\.82\+lexNorm\*\.18/)});

test('semantic and queue scripts are loaded and cached for offline shell',async()=>{const html=await readFile(new URL('public/index.html',root),'utf8');const sw=await readFile(new URL('public/sw.js',root),'utf8');for(const file of ['ingest.js','semantic.js'])assert.match(html,new RegExp(file.replace('.','\\.')));for(const file of ['ingest.js','semantic.js','semantic-worker.js'])assert.match(sw,new RegExp(file.replace('.','\\.')))});

test('queue and manual knowledge mutations share an interlock',async()=>{const kb=await readFile(new URL('public/kb.js',root),'utf8');const ingest=await readFile(new URL('public/ingest.js',root),'utf8');assert.match(kb,/yke-kb-mutation-lock/);assert.match(kb,/blockedByExternalLock/);assert.match(ingest,/yke-kb-mutation-lock/);assert.match(ingest,/expiresAt:Date\.now\(\)\+30000/)});

test('semantic index compresses vectors and prunes large searches with locality buckets',async()=>{const semantic=await readFile(new URL('public/semantic.js',root),'utf8');const worker=await readFile(new URL('public/semantic-worker.js',root),'utf8');assert.match(semantic,/Int8Array/);assert.match(semantic,/quantized:'int8-v1'/);assert.match(semantic,/bucket:vectorBucket/);assert.match(worker,/by-bucket/);assert.match(worker,/neighborBuckets/);assert.match(worker,/indexTotal>10000/);assert.match(worker,/approximate=true/)});

test('semantic vector store contains no transcript plaintext and clear/import invalidate derived vectors',async()=>{const semantic=await readFile(new URL('public/semantic.js',root),'utf8');assert.match(semantic,/plaintextStored:false/);assert.match(semantic,/chunkIndex:c\.index/);assert.match(semantic,/currentHash=await hash\(embeddingText\(v,c\)\)/);assert.match(semantic,/d\.kind==='clear'\|\|d\.kind==='import'/);assert.match(semantic,/await clearIndexData\(\)/)});


test('removing a source also removes derived longitudinal and audience caches',async()=>{const kb=await readFile(new URL('public/kb.js',root),'utf8');assert.match(kb,/analysis-history:\$\{sourceId\}/);assert.match(kb,/audience:\$\{sourceId\}/);assert.match(kb,/visual:\$\{sourceId\}/)});
