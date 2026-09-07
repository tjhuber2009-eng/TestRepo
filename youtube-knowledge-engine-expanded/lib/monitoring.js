import { randomUUID } from 'node:crypto';
import { access, mkdir, open, readFile, readdir, rename, rm, stat, unlink, writeFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { discoverChannel, fetchTranscript, normalizeChannelUrl, YoutubeError } from './youtube.js';
import { envInt, parseBool, sha256, sleep, validLanguage, validVideoId } from './util.js';
import { AsyncSemaphore } from './semaphore.js';

const STATE_SCHEMA = 1;
const SNAPSHOT_SCHEMA = 1;
const DEFAULT_STATE = { schema: STATE_SCHEMA, updatedAt: '', monitors: [] };
const PERMANENT_TRANSCRIPT_CODES = new Set(['NO_CAPTIONS','PRIVATE_VIDEO','UNLISTED_VIDEO','INVALID_VIDEO_ID','VIDEO_UNAVAILABLE']);

function iso(value = Date.now()) { return new Date(value).toISOString(); }
function cleanText(value, max = 1000) { return String(value ?? '').replace(/\s+/g, ' ').trim().slice(0, max); }
function normalizeChannel(value) { try { return normalizeChannelUrl(value); } catch { return ''; } }
function clampInterval(value) { const n = Number(value); return Number.isFinite(n) ? Math.min(10080, Math.max(60, Math.trunc(n))) : 1440; }
function sourceId(channel = {}) { const id = cleanText(channel.id, 250); if (id) return `yt:${id}`; return `url:${sha256(cleanText(channel.url, 1500).toLowerCase().replace(/\/$/, '')).slice(0,24)}`; }
function safeVideoMeta(v = {}, order = 0) {
  const id = cleanText(v.id, 20); if (!validVideoId(id)) return null;
  return { id, title: cleanText(v.title || id, 1000), url: `https://www.youtube.com/watch?v=${id}`, thumbnail: cleanText(v.thumbnail, 1500), published: cleanText(v.published, 100), duration: cleanText(v.duration, 40), views: cleanText(v.views, 40), kind: ['video','short','stream'].includes(v.kind) ? v.kind : 'video', order: Math.max(0, Number(order) || 0) };
}
function safeTranscript(t = {}, id) {
  const text = String(t.text || '');
  if (!text) throw new YoutubeError('Transcript response was empty.', 'NO_CAPTIONS', 404);
  if (text.length > 20_000_000) throw new YoutubeError('Transcript exceeds the 20 MB monitor safety limit.', 'TRANSCRIPT_TOO_LARGE', 502);
  const segments = Array.isArray(t.segments) ? t.segments.slice(0,500000).map(s => ({ startMs: Math.max(0, Number(s?.startMs) || 0), durationMs: Math.max(0, Number(s?.durationMs) || 0), text: String(s?.text || '').slice(0,20000) })).filter(s => s.text) : [];
  return { videoId: id, language: cleanText(t.language,30), languageName: cleanText(t.languageName,100), generated: Boolean(t.generated), translated: Boolean(t.translated), originalLanguage: cleanText(t.originalLanguage,30), source: cleanText(t.source,80), trackName: cleanText(t.trackName,300), providerModel: cleanText(t.providerModel,200), retrievedAt: cleanText(t.retrievedAt || iso(),100), text, segments, words: Math.max(0, Number(t.words) || text.split(/\s+/).filter(Boolean).length) };
}
function isTransient(error) { const code = String(error?.code || ''); const status = Number(error?.status || 0); return status === 429 || status >= 500 || /NETWORK|TIMEOUT|RATE|FETCH|UPSTREAM|BLOCK|ECONN|ABORT/i.test(code + ' ' + String(error?.message || '')); }
function errorInfo(error) { return { code: cleanText(error?.code || 'ERROR',80), message: cleanText(error?.message || error,500), status: Number(error?.status || 0) || 0 }; }
async function exists(path) { try { await access(path); return true; } catch { return false; } }
async function readJson(path, fallback = null) { try { return JSON.parse(await readFile(path,'utf8')); } catch { return fallback; } }
async function atomicWrite(path, text) { await mkdir(resolve(path,'..'), { recursive: true }); const tmp = `${path}.tmp-${process.pid}-${randomUUID()}`; await writeFile(tmp, text, { mode: 0o600 }); await rename(tmp, path); }
async function atomicJson(path, value) { return atomicWrite(path, JSON.stringify(value)); }

async function withLock(path, fn, { staleMs = 6 * 60 * 60_000, waitMs = 3000 } = {}) {
  await mkdir(resolve(path,'..'), { recursive: true });
  const started = Date.now(); let handle;
  for (;;) {
    try { handle = await open(path, 'wx', 0o600); await handle.writeFile(JSON.stringify({ pid: process.pid, at: iso() })); break; }
    catch (e) {
      if (e?.code !== 'EEXIST') throw e;
      try { const s = await stat(path); if (Date.now() - s.mtimeMs > staleMs) { await unlink(path).catch(()=>{}); continue; } } catch {}
      if (Date.now() - started >= waitMs) throw new YoutubeError('This monitor is already running in another process.', 'MONITOR_LOCKED', 409);
      await sleep(100);
    }
  }
  try { return await fn(); } finally { try { await handle?.close(); } catch {} await unlink(path).catch(()=>{}); }
}

export class MonitorManager {
  constructor({
    dataDir = process.env.MONITOR_DATA_DIR || join(process.cwd(),'data','monitoring'),
    discover = discoverChannel,
    transcript = fetchTranscript,
    schedulerEnabled = parseBool(process.env.MONITORING_ENABLED, false),
    pollSeconds = envInt('MONITOR_POLL_SECONDS', 300, 30, 3600),
    captionConcurrency = envInt('MONITOR_CAPTION_CONCURRENCY', 2, 1, 8),
    jobConcurrency = envInt('MONITOR_JOB_CONCURRENCY', 1, 1, 4),
    maxVideos = envInt('MONITOR_MAX_VIDEOS', 50000, 1, 50000),
    retryNoCaptionsMinutes = envInt('MONITOR_RETRY_NO_CAPTIONS_MINUTES', 360, 30, 10080),
    retryNoCaptionsDays = envInt('MONITOR_RETRY_NO_CAPTIONS_DAYS', 14, 1, 90),
  } = {}) {
    this.dataDir = resolve(dataDir); this.statePath = join(this.dataDir,'monitors.json'); this.sourcesDir = join(this.dataDir,'sources'); this.locksDir = join(this.dataDir,'locks');
    this.discover = discover; this.transcript = transcript; this.schedulerEnabled = Boolean(schedulerEnabled); this.pollSeconds = pollSeconds; this.captionConcurrency = captionConcurrency; this.jobConcurrency = jobConcurrency; this.maxVideos = maxVideos; this.retryNoCaptionsMinutes = retryNoCaptionsMinutes; this.retryNoCaptionsDays = retryNoCaptionsDays;
    this.loaded = false; this.state = structuredClone(DEFAULT_STATE); this.writeQueue = Promise.resolve(); this.timer = null; this.running = new Map(); this.schedulerBusy = false; this.schedulerPending = false;
  }
  async ensureLoaded() {
    if (this.loaded) return;
    await mkdir(this.sourcesDir,{recursive:true}); await mkdir(this.locksDir,{recursive:true});
    const raw = await readJson(this.statePath, structuredClone(DEFAULT_STATE));
    const monitors = Array.isArray(raw?.monitors) ? raw.monitors.map(m => this.#sanitizeMonitor(m)).filter(Boolean) : [];
    for (const m of monitors) if (m.status === 'running') { m.status = 'interrupted'; m.lastError = 'Previous process stopped during refresh; captured per-video checkpoints will be reused.'; m.nextRunAt = iso(); }
    this.state = { schema: STATE_SCHEMA, updatedAt: iso(), monitors }; this.loaded = true; await this.#save();
  }
  #sanitizeMonitor(m) {
    const id = /^[a-f0-9-]{8,64}$/i.test(String(m?.id||'')) ? String(m.id) : randomUUID(); const channel = normalizeChannel(m?.channel); if (!channel) return null;
    return { id, channel, title: cleanText(m?.title || channel,1000), channelId: cleanText(m?.channelId,250), sourceId: cleanText(m?.sourceId,250), language: validLanguage(m?.language||'en') ? String(m.language||'en') : 'en', intervalMinutes: clampInterval(m?.intervalMinutes), enabled: m?.enabled !== false, createdAt: cleanText(m?.createdAt || iso(),100), updatedAt: cleanText(m?.updatedAt || iso(),100), lastRunAt: cleanText(m?.lastRunAt,100), lastSuccessAt: cleanText(m?.lastSuccessAt,100), nextRunAt: cleanText(m?.nextRunAt || iso(),100), status: ['idle','queued','running','success','failed','interrupted','disabled'].includes(m?.status) ? m.status : 'idle', lastError: cleanText(m?.lastError,500), videoCount: Math.max(0,Number(m?.videoCount)||0), readyCount: Math.max(0,Number(m?.readyCount)||0), words: Math.max(0,Number(m?.words)||0), lastNewCount: Math.max(0,Number(m?.lastNewCount)||0), noCaptions: Math.max(0,Number(m?.noCaptions)||0), failed: Math.max(0,Number(m?.failed)||0), truncated: Boolean(m?.truncated), generation: Math.max(0,Number(m?.generation)||0), snapshotReady: Boolean(m?.snapshotReady) };
  }
  async #save() { const body = JSON.stringify({ schema: STATE_SCHEMA, updatedAt: iso(), monitors: this.state.monitors }); this.writeQueue = this.writeQueue.then(() => atomicWrite(this.statePath, body)); return this.writeQueue; }
  async list() { await this.ensureLoaded(); return this.state.monitors.map(m => ({...m, running: this.running.has(m.id)})); }
  async get(id) { await this.ensureLoaded(); return this.state.monitors.find(m => m.id === id) || null; }
  async upsert({ id='', channel, language='en', intervalMinutes=1440, enabled=true } = {}) {
    await this.ensureLoaded(); const normalized = normalizeChannel(channel); if (!normalized) throw new YoutubeError('A valid YouTube channel URL or @handle is required.','INVALID_CHANNEL',400); if (!validLanguage(language)) throw new YoutubeError('Invalid language code.','INVALID_LANGUAGE',400);
    let monitor = id ? this.state.monitors.find(m=>m.id===id) : null;
    if (!monitor) monitor = this.state.monitors.find(m=>m.channel.toLowerCase()===normalized.toLowerCase());
    if (!monitor) { monitor = this.#sanitizeMonitor({ id: randomUUID(), channel: normalized, language, intervalMinutes, enabled, createdAt: iso(), nextRunAt: iso(), status: enabled?'queued':'disabled' }); this.state.monitors.push(monitor); }
    else { monitor.channel = normalized; monitor.language = language || 'en'; monitor.intervalMinutes = clampInterval(intervalMinutes); monitor.enabled = Boolean(enabled); monitor.status = monitor.enabled ? (monitor.status==='running'?'running':'queued') : 'disabled'; if (monitor.enabled && (!monitor.nextRunAt || Date.parse(monitor.nextRunAt) > Date.now()+monitor.intervalMinutes*60000)) monitor.nextRunAt = iso(); monitor.updatedAt = iso(); }
    await this.#save(); if(monitor.enabled&&this.schedulerEnabled)queueMicrotask(()=>this.#tick().catch(e=>console.error('[monitor-scheduler]',e))); return {...monitor};
  }
  async setEnabled(id, enabled) { const m = await this.get(id); if (!m) throw new YoutubeError('Monitor not found.','MONITOR_NOT_FOUND',404); m.enabled=Boolean(enabled);m.status=m.enabled?'queued':'disabled';m.nextRunAt=m.enabled?iso():'';m.updatedAt=iso();await this.#save();if(m.enabled&&this.schedulerEnabled)queueMicrotask(()=>this.#tick().catch(e=>console.error('[monitor-scheduler]',e)));return{...m}; }
  async remove(id) { await this.ensureLoaded(); if (this.running.has(id)) throw new YoutubeError('Stop/wait for the running monitor before deleting it.','MONITOR_RUNNING',409); const i=this.state.monitors.findIndex(m=>m.id===id); if(i<0)throw new YoutubeError('Monitor not found.','MONITOR_NOT_FOUND',404); this.state.monitors.splice(i,1); await this.#save(); await rm(join(this.sourcesDir,id),{recursive:true,force:true}); return {ok:true}; }
  async runNow(id, { manual = true } = {}) { await this.ensureLoaded(); const monitor=this.state.monitors.find(m=>m.id===id); if(!monitor)throw new YoutubeError('Monitor not found.','MONITOR_NOT_FOUND',404); if(this.running.has(id))return this.running.get(id); const p=withLock(join(this.locksDir,`${id}.lock`),()=>this.#run(monitor,{manual})); this.running.set(id,p); try{return await p}finally{this.running.delete(id)} }
  async #run(monitor,{manual}) {
    monitor.status='running';monitor.lastRunAt=iso();monitor.updatedAt=iso();monitor.lastError='';await this.#save();
    const dir=join(this.sourcesDir,monitor.id),videosDir=join(dir,'videos');await mkdir(videosDir,{recursive:true});
    try {
      const discovered=await this.discover(monitor.channel,{maxVideos:this.maxVideos}); if(!Array.isArray(discovered?.videos)||!discovered.videos.length)throw new YoutubeError('No public videos were discovered.','NO_VIDEOS',404);
      const srcId=sourceId(discovered.channel||{}); const oldIndex=await readJson(join(dir,'index.json'),{videos:[]}); const oldMap=new Map((Array.isArray(oldIndex?.videos)?oldIndex.videos:[]).map(v=>[v.id,v])); const oldSource=oldIndex?.source||{}; const now=Date.now(), observedAt=iso(now);
      let entries=[]; let newCount=0, transientStreak=0, failed=0, noCaptions=0, ready=0, words=0; const sem=new AsyncSemaphore(this.captionConcurrency);
      const processOne=async(raw,order)=>sem.run(async()=>{
        const meta=safeVideoMeta(raw,order);if(!meta)return null;const old=oldMap.get(meta.id);const transcriptPath=join(videosDir,`${meta.id}.json`);let status=old?.status||'pending',lastAttemptAt=old?.lastAttemptAt||'',err=old?.error||null,txMeta=old?.transcriptMeta||null;
        const fileReady=await exists(transcriptPath); if(fileReady&&old?.status==='done'){status='done';}
        // A transcript file may have been durably written just before a process crash,
        // while the atomic channel index intentionally remained on the previous generation.
        // Revalidate and reuse that orphan checkpoint instead of paying the upstream cost again.
        if(fileReady&&status!=='done'){const checkpoint=await readJson(transcriptPath);if(checkpoint?.transcript?.text&&(!checkpoint.integrityHash||sha256(checkpoint.transcript.text)===checkpoint.integrityHash)){status='done';err=null;txMeta={words:Number(checkpoint.transcript.words)||checkpoint.transcript.text.split(/\s+/).filter(Boolean).length,integrityHash:checkpoint.integrityHash||sha256(checkpoint.transcript.text),retrievedAt:checkpoint.retrievedAt||checkpoint.transcript.retrievedAt||iso(),language:checkpoint.transcript.language||'',source:checkpoint.transcript.source||''};if(!old||old.status!=='done')newCount++;}}
        let shouldFetch=!fileReady||status!=='done';
        if(status==='no_captions'&&lastAttemptAt){const ageMinutes=(now-Date.parse(lastAttemptAt))/60000;const publishedMs=Date.parse(meta.published);const publishedAgeDays=Number.isFinite(publishedMs)?(now-publishedMs)/86400000:0;shouldFetch=ageMinutes>=this.retryNoCaptionsMinutes&&publishedAgeDays<=this.retryNoCaptionsDays;}
        if(status==='failed')shouldFetch=true;
        if(shouldFetch){lastAttemptAt=iso();try{const t=await this.transcript(meta.id,{language:monitor.language});const vm=t.videoMetadata||{};delete t.videoMetadata;const transcript=safeTranscript(t,meta.id);const record={schema:1,id:meta.id,integrityHash:sha256(transcript.text),retrievedAt:iso(),transcript};await atomicJson(transcriptPath,record);status='done';err=null;txMeta={words:transcript.words,integrityHash:record.integrityHash,retrievedAt:record.retrievedAt,language:transcript.language,source:transcript.source};if(vm.title)meta.title=cleanText(vm.title,1000);if(vm.published)meta.published=cleanText(vm.published,100);if(vm.duration)meta.duration=cleanText(vm.duration,40);if(vm.views)meta.views=cleanText(vm.views,40);if(vm.kind==='stream')meta.kind='stream';transientStreak=0;if(!old||old.status!=='done')newCount++;}catch(e){err=errorInfo(e);if(e?.code==='NO_CAPTIONS'){status='no_captions';transientStreak=0;}else{status='failed';if(isTransient(e)){transientStreak++;if(transientStreak>=5)throw new YoutubeError('Upstream circuit breaker opened after five consecutive transient failures. Existing snapshot was left unchanged.','MONITOR_CIRCUIT_OPEN',503);}else transientStreak=0;}}}
        // Public-count observations are kept as a two-point history. This is intentionally
        // distinct from lifetime views/day: it measures change between monitor snapshots only.
        if(meta.views){
          if(old?.views&&old?.viewsObservedAt){meta.previousViews=cleanText(old.views,40);meta.previousViewsObservedAt=cleanText(old.viewsObservedAt,100)}
          else if(old?.views){meta.previousViews=cleanText(old.views,40);meta.previousViewsObservedAt=cleanText(oldIndex?.createdAt,100)}
          meta.viewsObservedAt=observedAt;
        }else if(old?.views){
          meta.views=cleanText(old.views,40);meta.viewsObservedAt=cleanText(old.viewsObservedAt,100);meta.previousViews=cleanText(old.previousViews,40);meta.previousViewsObservedAt=cleanText(old.previousViewsObservedAt,100);
        }
        if(status==='done'&&txMeta){ready++;words+=Number(txMeta.words||0)}else if(status==='no_captions')noCaptions++;else if(status==='failed')failed++;
        return {...meta,status,lastAttemptAt,error:err,transcriptMeta:txMeta};
      });
      // Preserve discovery order while still fetching captions concurrently in bounded windows.
      for(let start=0;start<discovered.videos.length;start+=50){const chunk=discovered.videos.slice(start,start+50);const rows=await Promise.all(chunk.map((v,i)=>processOne(v,start+i)));entries.push(...rows.filter(Boolean));monitor.videoCount=entries.length;monitor.readyCount=ready;monitor.words=words;monitor.noCaptions=noCaptions;monitor.failed=failed;monitor.lastNewCount=newCount;monitor.updatedAt=iso();await this.#save();}
      if(discovered.truncated){const seen=new Set(entries.map(v=>v.id));for(const old of oldMap.values())if(!seen.has(old.id)){entries.push(old);if(old.status==='done'){ready++;words+=Number(old.transcriptMeta?.words||0)}else if(old.status==='no_captions')noCaptions++;else if(old.status==='failed')failed++;}}
      const currentSubscribers=cleanText(discovered.channel?.subscribers,40),source={schema:1,id:srcId,title:cleanText(discovered.channel?.title||monitor.title||monitor.channel,1000),url:cleanText(discovered.channel?.url||monitor.channel,1500),channelId:cleanText(discovered.channel?.id,250),subscribers:currentSubscribers||cleanText(oldSource?.subscribers,40),verified:Boolean(discovered.channel?.verified),lastSyncedAt:iso(),videoCount:entries.length,readyCount:ready,words,truncated:Boolean(discovered.truncated),monitorId:monitor.id,generation:monitor.generation+1};
      if(currentSubscribers){if(oldSource?.subscribers){source.previousSubscribers=cleanText(oldSource.subscribers,40);source.previousSubscribersObservedAt=cleanText(oldSource.subscribersObservedAt||oldIndex?.createdAt,100)}source.subscribersObservedAt=observedAt}else if(oldSource?.subscribers){source.subscribersObservedAt=cleanText(oldSource.subscribersObservedAt,100);source.previousSubscribers=cleanText(oldSource.previousSubscribers,40);source.previousSubscribersObservedAt=cleanText(oldSource.previousSubscribersObservedAt,100)}
      const index={schema:1,monitorId:monitor.id,sourceId:srcId,generation:source.generation,createdAt:iso(),truncated:Boolean(discovered.truncated),source,videos:entries};
      await atomicJson(join(dir,'source.json'),source);await atomicJson(join(dir,'index.json'),index);
      // Clean orphaned transcript checkpoints only after a complete, non-truncated catalog commit.
      if(!discovered.truncated){const keep=new Set(entries.filter(v=>v.status==='done').map(v=>`${v.id}.json`));for(const name of await readdir(videosDir).catch(()=>[]))if(name.endsWith('.json')&&!keep.has(name))await unlink(join(videosDir,name)).catch(()=>{});}
      Object.assign(monitor,{title:source.title,channelId:source.channelId,sourceId:srcId,status:'success',lastSuccessAt:iso(),nextRunAt:iso(Date.now()+monitor.intervalMinutes*60000),updatedAt:iso(),lastError:'',videoCount:entries.length,readyCount:ready,words,lastNewCount:newCount,noCaptions,failed,truncated:Boolean(discovered.truncated),generation:source.generation,snapshotReady:true});await this.#save();return{...monitor};
    } catch(e) {
      monitor.status='failed';monitor.lastError=cleanText(e?.message||e,500);monitor.updatedAt=iso();monitor.nextRunAt=iso(Date.now()+Math.min(monitor.intervalMinutes,60)*60000);await this.#save();throw e;
    }
  }
  async *snapshotLines(id) {
    await this.ensureLoaded(); const monitor=this.state.monitors.find(m=>m.id===id);if(!monitor)throw new YoutubeError('Monitor not found.','MONITOR_NOT_FOUND',404);const dir=join(this.sourcesDir,id),index=await readJson(join(dir,'index.json')),source=index?.source||await readJson(join(dir,'source.json'));if(!source||!index)throw new YoutubeError('This monitor does not have a completed snapshot yet.','SNAPSHOT_NOT_READY',404);
    let chain='0'.repeat(64),count=0;const push=line=>{chain=sha256(`${chain}:${line}`);return `${line}\n`};
    let line=JSON.stringify({type:'monitor-snapshot',schema:SNAPSHOT_SCHEMA,monitor:{id:monitor.id,channel:monitor.channel,language:monitor.language,generation:index.generation},createdAt:iso()});yield push(line);line=JSON.stringify({type:'source',source});yield push(line);
    for(const entry of index.videos||[]){if(entry.status!=='done')continue;const rec=await readJson(join(dir,'videos',`${entry.id}.json`));if(!rec?.transcript?.text)continue;if(rec.integrityHash&&sha256(rec.transcript.text)!==rec.integrityHash)throw new YoutubeError(`Stored transcript integrity failed for ${entry.id}.`,'SNAPSHOT_CORRUPT',500);const video={...entry,status:'done',archived:false,error:'',qualityFlags:[],integrityHash:rec.integrityHash,retrievedAt:rec.retrievedAt,sourceId:source.id,sourceTitle:source.title,sourceUrl:source.url,kbSyncedAt:source.lastSyncedAt,transcript:rec.transcript};delete video.transcriptMeta;delete video.lastAttemptAt;line=JSON.stringify({type:'video',video});yield push(line);count++;}
    yield `${JSON.stringify({type:'end',videos:count,generation:index.generation,chain})}\n`;
  }
  startScheduler() { if(!this.schedulerEnabled||this.timer)return; const tick=()=>this.#tick().catch(e=>console.error('[monitor-scheduler]',e)); this.timer=setInterval(tick,this.pollSeconds*1000);this.timer.unref?.();tick(); }
  async #tick() { if(this.schedulerBusy){this.schedulerPending=true;return}this.schedulerBusy=true;try{await this.ensureLoaded();const now=Date.now(),due=this.state.monitors.filter(m=>m.enabled&&!this.running.has(m.id)&&(!m.nextRunAt||Date.parse(m.nextRunAt)<=now));for(let i=0;i<due.length;i+=this.jobConcurrency){const batch=due.slice(i,i+this.jobConcurrency);await Promise.all(batch.map(async m=>{try{await this.runNow(m.id,{manual:false})}catch(e){console.error(`[monitor ${m.id}]`,e?.message||e)}}))}}finally{this.schedulerBusy=false;if(this.schedulerPending){this.schedulerPending=false;queueMicrotask(()=>this.#tick().catch(e=>console.error('[monitor-scheduler]',e)))}} }
  stopScheduler(){if(this.timer)clearInterval(this.timer);this.timer=null;}
  status(){return{schedulerEnabled:this.schedulerEnabled,pollSeconds:this.pollSeconds,captionConcurrency:this.captionConcurrency,jobConcurrency:this.jobConcurrency,maxVideos:this.maxVideos,dataDir:this.dataDir};}
}

export function monitorPublicInfo(managerStatus={}) { return { schedulerEnabled:Boolean(managerStatus.schedulerEnabled), pollSeconds:Number(managerStatus.pollSeconds||0), jobConcurrency:Number(managerStatus.jobConcurrency||0), maxVideos:Number(managerStatus.maxVideos||0) }; }
