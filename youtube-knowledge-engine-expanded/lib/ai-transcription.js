import { execFile } from 'node:child_process';
import { chmod, mkdir, mkdtemp, readFile, readdir, rename, rm, stat, writeFile } from 'node:fs/promises';
import { randomUUID } from 'node:crypto';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { promisify } from 'node:util';
import { YoutubeError, ytdlpAvailable } from './youtube.js';
import { envInt, normalizeSpaces, readResponseTextLimited, sha256, validVideoId } from './util.js';
import { AsyncSemaphore } from './semaphore.js';

const execFileAsync = promisify(execFile);
const toolChecks=new Map();
const aiWorkSemaphore = new AsyncSemaphore(envInt('AI_MAX_CONCURRENCY', 2, 1, 8));
const ytdlpBin=()=>process.env.YTDLP_BIN||'yt-dlp';
const ffmpegBin=()=>process.env.FFMPEG_BIN||'ffmpeg';
const ytdlpJsRuntime=()=>`node:${process.env.YTDLP_NODE_BIN||process.execPath}`;
async function toolAvailable(name,args){const now=Date.now(),cached=toolChecks.get(name);if(cached&&cached.expiresAt>now)return cached.promise;const promise=execFileAsync(name,args,{timeout:5000,maxBuffer:1024*1024}).then(()=>true).catch(()=>false);toolChecks.set(name,{promise,expiresAt:now+5*60_000});return promise}

function config() {
  return {
    apiUrl: process.env.TRANSCRIPTION_API_URL || '', apiKey: process.env.TRANSCRIPTION_API_KEY || '',
    auth: process.env.TRANSCRIPTION_AUTH || 'bearer', model: process.env.TRANSCRIPTION_MODEL || 'whisper-1',
    responseFormat: process.env.TRANSCRIPTION_RESPONSE_FORMAT || 'verbose_json',
    chunkSeconds: envInt('TRANSCRIPTION_CHUNK_SECONDS', 900, 60, 3600),
    timeoutMs: envInt('TRANSCRIPTION_TIMEOUT_MS', 240000, 10000, 1800000),
    maxVideoMinutes: envInt('AI_MAX_VIDEO_MINUTES', 360, 1, 1440),
    maxDownloadedBytes: envInt('AI_MAX_DOWNLOADED_AUDIO_MB', 1024, 20, 10240) * 1024 * 1024,
    maxChunkBytes: envInt('AI_MAX_CHUNK_MB', 24, 1, 100) * 1024 * 1024,
    attempts: envInt('TRANSCRIPTION_PROVIDER_ATTEMPTS', 1, 1, 3),
    checkpointDir: process.env.AI_CHECKPOINT_DIR || '',
    maxCheckpointBytes: envInt('AI_MAX_CHECKPOINT_MB', 128, 16, 512) * 1024 * 1024,
  };
}


const CHECKPOINT_VERSION=2;
function checkpointConfigFingerprint(c) {
  return sha256(JSON.stringify({ apiUrl:c.apiUrl||'', model:c.model||'', chunkSeconds:Number(c.chunkSeconds)||0, responseFormat:c.responseFormat||'' }));
}
export function checkpointKey(videoId, language, c = config()) {
  // Preserve the v2 key format so previously completed paid work remains reusable.
  return sha256(JSON.stringify({ version:CHECKPOINT_VERSION, videoId, language: language || '', model: c.model, chunkSeconds: c.chunkSeconds, responseFormat: c.responseFormat, apiUrl:c.apiUrl||'' })).slice(0, 40);
}
function validCheckpointSegment(s) {
  const startMs=Number(s?.startMs),durationMs=Number(s?.durationMs),text=normalizeSpaces(s?.text);
  if(!Number.isFinite(startMs)||startMs<0||!Number.isFinite(durationMs)||durationMs<0||!text||text.length>200000)return null;
  return {startMs:Math.round(startMs),durationMs:Math.round(durationMs),text};
}
function normalizeCheckpointChunk(x,index,c) {
  const text=normalizeSpaces(x?.text);if(!text||text.length>2_000_000)return null;
  if(x?.textHash&&x.textHash!==sha256(text))return null;
  const start=index*Number(c.chunkSeconds)*1000,end=start+Number(c.chunkSeconds)*1000;
  let segments=Array.isArray(x?.segments)?x.segments.slice(0,100000).map(validCheckpointSegment).filter(Boolean).map(s=>{
    const s0=Math.max(start,s.startMs),s1=Math.min(end,s.startMs+s.durationMs);return {...s,startMs:s0,durationMs:Math.max(0,s1-s0)};
  }).filter(s=>s.text&&s.startMs<end):[];
  // A paid provider result without timestamp segments is still reusable: synthesize the
  // same bounded fallback segment that a fresh provider response would have produced.
  if(!segments.length)segments=[{startMs:start,durationMs:Number(c.chunkSeconds)*1000,text}];
  return {text,segments,textHash:sha256(text)};
}
export function validateCheckpoint(raw,{videoId,language,c=config()}={}) {
  if(!raw||raw.version!==CHECKPOINT_VERSION||raw.videoId!==videoId||String(raw.language||'')!==String(language||'')||raw.model!==c.model||Number(raw.chunkSeconds)!==Number(c.chunkSeconds))return null;
  if(raw.configFingerprint&&raw.configFingerprint!==checkpointConfigFingerprint(c))return null;
  const chunks=Array.isArray(raw.chunks)?raw.chunks.slice(0,10000).map((x,i)=>normalizeCheckpointChunk(x,i,c)):[];
  let result=null;
  if(raw.result){
    const text=normalizeSpaces(raw.result.text);
    const derivedText=normalizeSpaces(chunks.filter(Boolean).flatMap(x=>x.segments).map(s=>s.text).join(' '));
    const resultHashOk=!raw.resultHash||raw.resultHash===sha256(text);
    const allChunksValid=chunks.length>0&&chunks.every(Boolean);
    if(raw.result.videoId===videoId&&raw.result.source==='ai-transcription'&&text&&text.length<=50_000_000&&resultHashOk&&allChunksValid&&text===derivedText){
      const segments=chunks.flatMap(x=>x.segments);
      result={
        videoId,language:String(raw.result.language||language||''),languageName:String(raw.result.languageName||language||'AI detected').slice(0,200),
        generated:true,source:'ai-transcription',trackName:String(raw.result.trackName||`AI transcription · ${c.model}`).slice(0,500),
        text,segments,words:text.split(/\s+/).filter(Boolean).length,providerModel:c.model,
        retrievedAt:String(raw.result.retrievedAt||raw.completedAt||'').slice(0,100),
      };
    }
  }
  return {version:CHECKPOINT_VERSION,videoId,language:String(language||''),model:c.model,chunkSeconds:c.chunkSeconds,configFingerprint:checkpointConfigFingerprint(c),chunks,result,createdAt:String(raw.createdAt||'').slice(0,100),updatedAt:String(raw.updatedAt||'').slice(0,100),completedAt:String(raw.completedAt||'').slice(0,100),resultHash:result?sha256(result.text):''};
}
async function readCheckpoint(c, key, expected) {
  if (!c.checkpointDir) return null;
  try {
    const path=join(c.checkpointDir, `${key}.json`); const info=await stat(path);
    if(!info.isFile()||info.size>c.maxCheckpointBytes)return null;
    return validateCheckpoint(JSON.parse(await readFile(path, 'utf8')),{...expected,c});
  } catch { return null; }
}
async function writeCheckpoint(c, key, value) {
  if (!c.checkpointDir) return;
  await mkdir(c.checkpointDir, { recursive: true, mode: 0o700 });
  await chmod(c.checkpointDir,0o700).catch(()=>{});
  const target = join(c.checkpointDir, `${key}.json`); const temp = `${target}.${process.pid}.${randomUUID()}.tmp`;
  const serialized=JSON.stringify(value);
  if(Buffer.byteLength(serialized)>c.maxCheckpointBytes)throw new YoutubeError('AI checkpoint exceeds the configured safety limit.','AI_CHECKPOINT_TOO_LARGE',500);
  try { await writeFile(temp, serialized, { mode: 0o600, flag:'wx' }); await rename(temp, target); }
  finally { await rm(temp,{force:true}).catch(()=>{}); }
}

export function aiTranscriptionConfigured() { const c=config(); return Boolean(c.apiUrl && (c.auth === 'none' || c.apiKey)); }
export async function aiRuntimeStatus(){const [ytdlp,ffmpeg]=await Promise.all([ytdlpAvailable(),toolAvailable(ffmpegBin(),['-version'])]);return{providerConfigured:aiTranscriptionConfigured(),ytdlp,ffmpeg,ready:aiTranscriptionConfigured()&&ytdlp&&ffmpeg}}

async function execChecked(bin, args, opts = {}) {
  try { return await execFileAsync(bin, args, { timeout: opts.timeout || 120000, maxBuffer: opts.maxBuffer || 8 * 1024 * 1024 }); }
  catch (e) { throw new YoutubeError(`${bin} failed: ${String(e.stderr || e.message).slice(0, 700)}`, opts.code || 'EXTERNAL_TOOL_FAILED', 502); }
}

export async function probeVideo(videoId) {
  if (!validVideoId(videoId)) throw new YoutubeError('Invalid YouTube video ID.', 'INVALID_VIDEO_ID', 400);
  const args = ['--ignore-config','--js-runtimes',ytdlpJsRuntime(),'--no-playlist','-f','bestaudio/best','--dump-single-json','--skip-download','--quiet','--no-warnings'];
  if (process.env.YTDLP_COOKIES_FILE) args.push('--cookies', process.env.YTDLP_COOKIES_FILE);
  args.push(`https://www.youtube.com/watch?v=${videoId}`);
  const { stdout } = await execChecked(ytdlpBin(), args, { timeout: 90000, code: 'VIDEO_PROBE_FAILED' });
  let data; try { data=JSON.parse(stdout); } catch { throw new YoutubeError('yt-dlp returned unreadable metadata.', 'VIDEO_PROBE_FAILED', 502); }
  const requested=Array.isArray(data.requested_downloads)?data.requested_downloads[0]:null;const estimatedAudioBytes=Number(requested?.filesize||requested?.filesize_approx||data.filesize||data.filesize_approx||0);return { durationSeconds: Number(data.duration || 0), liveStatus: data.live_status || '', title: data.title || '', availability: data.availability || '', estimatedAudioBytes:Number.isFinite(estimatedAudioBytes)?estimatedAudioBytes:0 }; 
}

function ytArgs() { const a=['--ignore-config','--js-runtimes',ytdlpJsRuntime()]; if (process.env.YTDLP_COOKIES_FILE) a.push('--cookies',process.env.YTDLP_COOKIES_FILE); return a; }

function parseProviderSegments(data, chunkOffsetMs, fallbackText, chunkSeconds) {
  const raw = data?.segments;
  if (Array.isArray(raw) && raw.length) return raw.map((s)=>({ startMs: chunkOffsetMs + Math.round(Number(s.start || 0)*1000), durationMs: Math.max(0,Math.round((Number(s.end || s.start || 0)-Number(s.start || 0))*1000)), text: normalizeSpaces(s.text) })).filter(s=>s.text);
  return fallbackText ? [{ startMs: chunkOffsetMs, durationMs: chunkSeconds*1000, text: fallbackText }] : [];
}

async function transcribeChunk(path, index, language, c) {
  const info=await stat(path); if (info.size > c.maxChunkBytes) throw new YoutubeError(`Audio chunk ${index+1} is too large for the configured provider limit.`, 'AUDIO_CHUNK_TOO_LARGE', 413);
  const bytes=await readFile(path); let last;
  for (let attempt=1; attempt<=c.attempts; attempt++) {
    const form=new FormData(); form.append('file',new Blob([bytes],{type:'audio/mpeg'}),`chunk-${String(index).padStart(3,'0')}.mp3`); form.append('model',c.model); form.append('response_format',c.responseFormat); if (language) form.append('language',String(language).split('-')[0]);
    const controller=new AbortController(); const timer=setTimeout(()=>controller.abort(),c.timeoutMs);
    try {
      const headers={}; if (c.auth !== 'none') headers.authorization=`Bearer ${c.apiKey}`;
      const response=await fetch(c.apiUrl,{method:'POST',headers,body:form,signal:controller.signal}); const raw=await readResponseTextLimited(response, envInt('TRANSCRIPTION_MAX_RESPONSE_MB', 8, 1, 64) * 1024 * 1024); let data; try{data=JSON.parse(raw)}catch{data={text:raw}}
      if (!response.ok) {
        const msg=data?.error?.message||data?.message||`Provider returned HTTP ${response.status}`;
        if (attempt < c.attempts && response.status >= 500) { last=new Error(msg); continue; }
        throw new YoutubeError(msg,'TRANSCRIPTION_PROVIDER_ERROR',502);
      }
      const text=normalizeSpaces(data?.text); if (!text) throw new YoutubeError('Speech-to-text provider returned an empty transcript.','EMPTY_AI_TRANSCRIPT',502);
      return { text, segments: parseProviderSegments(data,index*c.chunkSeconds*1000,text,c.chunkSeconds) };
    } catch (e) {
      if (e instanceof YoutubeError) throw e;
      if (e?.code === 'RESPONSE_TOO_LARGE') throw new YoutubeError('Speech-to-text provider response exceeded the configured safety limit.','TRANSCRIPTION_RESPONSE_TOO_LARGE',502);
      last=e;
      // Ambiguous network/timeout failures are not retried by default; attempts > 1 is explicit operator opt-in.
      if (attempt >= c.attempts) throw new YoutubeError(`Speech-to-text request failed: ${e.name === 'AbortError' ? 'timeout' : e.message}`,'TRANSCRIPTION_NETWORK_ERROR',502);
    } finally { clearTimeout(timer); }
  }
  throw last;
}

async function transcribeVideoAudioInner(videoId,{language=''}={}) {
  const c=config(); if (!aiTranscriptionConfigured()) throw new YoutubeError('AI transcription is not configured on this server.','AI_TRANSCRIPTION_NOT_CONFIGURED',424);
  const cpKey=checkpointKey(videoId,language,c); let checkpoint=await readCheckpoint(c,cpKey,{videoId,language});
  if (checkpoint?.result?.text) return { ...checkpoint.result, checkpoint: true };
  if (!checkpoint) checkpoint={version:CHECKPOINT_VERSION,videoId,language,model:c.model,chunkSeconds:c.chunkSeconds,configFingerprint:checkpointConfigFingerprint(c),chunks:[],createdAt:new Date().toISOString()};
  const probe=await probeVideo(videoId); if(probe.availability&&probe.availability!=='public')throw new YoutubeError('This video is not public; AI transcription is limited to public videos.','NON_PUBLIC_VIDEO',403,{availability:probe.availability}); if (['is_live','is_upcoming'].includes(probe.liveStatus)) throw new YoutubeError('Live/upcoming videos cannot be AI-transcribed safely.','LIVE_VIDEO_UNSUPPORTED',409);
  if(probe.estimatedAudioBytes>c.maxDownloadedBytes)throw new YoutubeError('The selected audio stream exceeds the configured download safety limit before download.','AUDIO_DOWNLOAD_TOO_LARGE',413,{estimatedAudioBytes:probe.estimatedAudioBytes});
  if (probe.durationSeconds > c.maxVideoMinutes*60) throw new YoutubeError(`Video is ${Math.ceil(probe.durationSeconds/60)} minutes, above the configured ${c.maxVideoMinutes}-minute AI limit.`,'VIDEO_TOO_LONG_FOR_AI',413,{durationSeconds:probe.durationSeconds});
  const dir=await mkdtemp(join(tmpdir(),'cts-ai-'));
  try {
    const output=join(dir,'audio.%(ext)s');
    await execChecked(ytdlpBin(),[...ytArgs(),'--no-playlist','--quiet','--no-warnings','-f','bestaudio/best','--max-filesize',String(c.maxDownloadedBytes),'-x','--audio-format','mp3','--audio-quality','9','-o',output,`https://www.youtube.com/watch?v=${videoId}`],{timeout:30*60_000,code:'AUDIO_DOWNLOAD_FAILED'});
    const files=await readdir(dir); const audio=files.find(n=>n==='audio.mp3')||files.find(n=>n.startsWith('audio.')); if(!audio) throw new YoutubeError('Audio download produced no file.','AUDIO_DOWNLOAD_FAILED',502);
    const audioPath=join(dir,audio); const info=await stat(audioPath); if(info.size>c.maxDownloadedBytes) throw new YoutubeError('Downloaded audio exceeds the configured safety limit.','AUDIO_DOWNLOAD_TOO_LARGE',413);
    await execChecked(ffmpegBin(),['-hide_banner','-loglevel','error','-y','-i',audioPath,'-f','segment','-segment_time',String(c.chunkSeconds),'-reset_timestamps','1','-ac','1','-ar','16000','-acodec','libmp3lame','-b:a','48k',join(dir,'chunk-%04d.mp3')],{timeout:30*60_000,code:'AUDIO_CHUNK_FAILED'});
    const chunks=(await readdir(dir)).filter(n=>/^chunk-\d+\.mp3$/.test(n)).sort(); if(!chunks.length) throw new YoutubeError('No audio chunks were produced.','AUDIO_CHUNK_FAILED',502);
    const segments=[];
    for(let i=0;i<chunks.length;i++){
      let r=checkpoint.chunks?.[i];
      if(!r?.text){ r=await transcribeChunk(join(dir,chunks[i]),i,language,c); r={...r,textHash:sha256(r.text)}; checkpoint.chunks[i]=r; checkpoint.updatedAt=new Date().toISOString(); await writeCheckpoint(c,cpKey,checkpoint); }
      segments.push(...(r.segments||[]));
    }
    const maxMs=probe.durationSeconds>0?Math.round(probe.durationSeconds*1000):Infinity;
    const boundedSegments=segments.map(s=>{const startMs=Math.max(0,Number(s.startMs)||0);const endMs=Math.min(maxMs,startMs+Math.max(0,Number(s.durationMs)||0));return {...s,startMs,durationMs:Math.max(0,endMs-startMs)}}).filter(s=>s.text&&s.startMs<maxMs);
    const text=normalizeSpaces(boundedSegments.map(s=>s.text).join(' '));
    const result={ videoId,language:language||'',languageName:language||'AI detected',generated:true,source:'ai-transcription',trackName:`AI transcription · ${c.model}`,text,segments:boundedSegments,words:text?text.split(/\s+/).length:0,providerModel:c.model,retrievedAt:new Date().toISOString() };
    checkpoint.result=result; checkpoint.resultHash=sha256(result.text); checkpoint.completedAt=new Date().toISOString(); await writeCheckpoint(c,cpKey,checkpoint); return result;
  } finally { await rm(dir,{recursive:true,force:true}).catch(()=>{}); }
}

export async function transcribeVideoAudio(videoId, options={}) { return aiWorkSemaphore.run(() => transcribeVideoAudioInner(videoId, options)); }
