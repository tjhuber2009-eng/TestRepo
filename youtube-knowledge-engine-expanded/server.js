import http from 'node:http';
import { randomUUID } from 'node:crypto';
import { readFile, stat } from 'node:fs/promises';
import { extname, join, normalize } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { discoverChannel, fetchTranscript, fetchVideoComments, ytdlpAvailable, YoutubeError } from './lib/youtube.js';
import { aiRuntimeStatus, aiTranscriptionConfigured, transcribeVideoAudio } from './lib/ai-transcription.js';
import { researchConfigured, synthesizeResearch } from './lib/research.js';
import { embedTexts, embeddingProviderInfo, embeddingsConfigured } from './lib/embeddings.js';
import { analyzeVideoVisuals, visualConfigured, visualProviderInfo } from './lib/visual-ai.js';
import { LruTtlCache } from './lib/cache.js';
import { assertHost, assertJson, assertSafeOrigin, aiAllowed, monitorAllowed, limits, pruneRateLimits, rateLimit, securityHeaders } from './lib/security.js';
import { envInt, safeEqual, sha256, validLanguage, validVideoId } from './lib/util.js';
import { MonitorManager, monitorPublicInfo } from './lib/monitoring.js';
import { DesktopStore, desktopEnabled } from './lib/desktop-store.js';

if(process.env.DESKTOP_MODE==='1'&&process.platform!=='win32')process.umask(0o077);

export const VERSION='3.1.0';
const ROOT=fileURLToPath(new URL('./public/',import.meta.url));
const MIME={'.html':'text/html; charset=utf-8','.js':'text/javascript; charset=utf-8','.css':'text/css; charset=utf-8','.json':'application/json; charset=utf-8','.webmanifest':'application/manifest+json; charset=utf-8','.svg':'image/svg+xml','.png':'image/png','.ico':'image/x-icon'};
const transcriptCache=new LruTtlCache({max:envInt('TRANSCRIPT_CACHE_ITEMS',1000,10,10000),ttlMs:envInt('TRANSCRIPT_CACHE_TTL_MINUTES',360,1,10080)*60000});
const commentsCache=new LruTtlCache({max:envInt('COMMENTS_CACHE_ITEMS',300,10,5000),ttlMs:envInt('COMMENTS_CACHE_TTL_MINUTES',360,1,10080)*60000});
const aiCache=new LruTtlCache({max:envInt('AI_CACHE_ITEMS',300,1,5000),ttlMs:envInt('AI_CACHE_TTL_MINUTES',1440,1,43200)*60000});
const researchCache=new LruTtlCache({max:100,ttlMs:30*60000});
const embeddingCache=new LruTtlCache({max:envInt('EMBEDDING_CACHE_ITEMS',500,10,5000),ttlMs:envInt('EMBEDDING_CACHE_TTL_MINUTES',10080,1,43200)*60000});
const visualCache=new LruTtlCache({max:envInt('VISUAL_CACHE_ITEMS',300,10,5000),ttlMs:envInt('VISUAL_CACHE_TTL_MINUTES',43200,1,262800)*60000});
setInterval(pruneRateLimits,15*60_000).unref();

function isHttps(req){ return Boolean(req.socket?.encrypted) || String(req.headers['x-forwarded-proto']||'').split(',')[0].trim()==='https'; }
function baseHeaders(req){const duration=Math.max(0,Date.now()-Number(req.startedAt||Date.now()));return {...securityHeaders({isHttps:isHttps(req)}),'x-request-id':req.requestId||'unknown','server-timing':`app;dur=${duration}`}; }
function sendJson(req,res,status,data,extra={}){ const body=Buffer.from(JSON.stringify(data)); res.writeHead(status,{...baseHeaders(req),'content-type':'application/json; charset=utf-8','content-length':body.length,'cache-control':'no-store',...extra}); if(req.method!=='HEAD')res.end(body);else res.end(); }
async function readJson(req,maxBytes=256*1024){ assertJson(req); assertSafeOrigin(req); const chunks=[];let size=0; for await(const c of req){size+=c.length;if(size>maxBytes)throw new YoutubeError('Request body is too large.','BODY_TOO_LARGE',413);chunks.push(c)} if(!chunks.length)return{};try{return JSON.parse(Buffer.concat(chunks).toString('utf8'))}catch{throw new YoutubeError('Request body must be valid JSON.','INVALID_JSON',400)} }
function validateCommon(body){ const language=String(body.language||'').trim(); if(!validLanguage(language))throw new YoutubeError('Invalid language code.','INVALID_LANGUAGE',400); return language; }
async function staticFile(req,res,pathname){
  const requested=pathname==='/'?'/index.html':pathname; const safe=normalize(requested).replace(/^([.][.][/\\])+/, '').replace(/^[/\\]+/,''); const filePath=join(ROOT,safe); if(!filePath.startsWith(ROOT))return false;
  try{const info=await stat(filePath);if(!info.isFile())return false;const content=await readFile(filePath);const etag=`"${sha256(content).slice(0,24)}"`;if(req.headers['if-none-match']===etag){res.writeHead(304,{...baseHeaders(req),etag});res.end();return true} const immutable=/\.[a-f0-9]{8,}\./.test(filePath);res.writeHead(200,{...baseHeaders(req),'content-type':MIME[extname(filePath)]||'application/octet-stream','content-length':content.length,etag,'cache-control':pathname==='/'||pathname.endsWith('sw.js')?'no-cache':immutable?'public, max-age=31536000, immutable':'public, max-age=300'});if(req.method!=='HEAD')res.end(content);else res.end();return true}catch{return false}
}

export function createServer({monitorManager=new MonitorManager(),commentFetcher=fetchVideoComments,visualAnalyzer=analyzeVideoVisuals,desktopStore=desktopEnabled()?new DesktopStore(process.env.DESKTOP_DATA_DIR||join(process.cwd(),'data','desktop')):null}={}){
  monitorManager.startScheduler();
  const server=http.createServer({maxHeaderSize:32*1024},async(req,res)=>{
    req.requestId=randomUUID();req.startedAt=Date.now();
    let url; try{ assertHost(req); url=new URL(req.url,`http://${req.headers.host||'localhost'}`); }catch(e){return sendJson(req,res,e.status||400,{error:e.message,code:e.code||'BAD_REQUEST'})}
    if(desktopStore){const expected=String(process.env.DESKTOP_SESSION_TOKEN||'');const cookie=String(req.headers.cookie||'').split(';').map(x=>x.trim()).find(x=>x.startsWith('yke_desktop_session='))?.split('=')[1]||'';const bootstrap=url.searchParams.get('desktopToken')||'';if(req.method==='GET'&&url.pathname==='/'&&expected&&bootstrap===expected){res.writeHead(302,{'set-cookie':`yke_desktop_session=${encodeURIComponent(expected)}; Path=/; HttpOnly; SameSite=Strict`,'location':'/','cache-control':'no-store'});res.end();return}if(!expected||!safeEqual(cookie,encodeURIComponent(expected))){return sendJson(req,res,403,{error:'Desktop session authorization required.',code:'DESKTOP_SESSION_REQUIRED'})}}
    try{
      if((req.method==='GET'||req.method==='HEAD')&&url.pathname==='/api/health'){const [ytDlp,aiRuntime]=await Promise.all([ytdlpAvailable(),aiRuntimeStatus()]);const embedding=embeddingProviderInfo(),visual=visualProviderInfo();return sendJson(req,res,200,{ok:true,service:'youtube-knowledge-engine',version:VERSION,aiTranscriptionConfigured:aiTranscriptionConfigured(),aiTranscriptionReady:aiRuntime.ready,researchConfigured:researchConfigured(),embeddingsConfigured:embeddingsConfigured(),embedding,visualConfigured:visualConfigured(),visual,monitoring:monitorPublicInfo(monitorManager.status()),desktop:desktopStore?{enabled:true,...desktopStore.info()}:{enabled:false},tools:{ytDlp,ffmpeg:aiRuntime.ffmpeg},cache:{transcripts:transcriptCache.stats(),comments:commentsCache.stats(),ai:aiCache.stats(),embeddings:embeddingCache.stats(),visual:visualCache.stats()}})};

      if(desktopStore&&req.method==='POST'&&url.pathname==='/api/desktop/mirror/begin'){const body=await readJson(req);const uploadId=String(body.uploadId||'').replace(/[^A-Za-z0-9_-]/g,'').slice(0,80);if(!uploadId)throw new YoutubeError('Invalid upload id.','DESKTOP_MIRROR_INVALID',400);return sendJson(req,res,200,desktopStore.begin(uploadId));}
      if(desktopStore&&req.method==='POST'&&url.pathname==='/api/desktop/mirror/chunk'){const body=await readJson(req,32*1024*1024);try{desktopStore.put(String(body.uploadId||''),String(body.store||''),body.records);return sendJson(req,res,200,{ok:true})}catch(e){throw new YoutubeError(e.message,'DESKTOP_MIRROR_INVALID',400)}}
      if(desktopStore&&req.method==='POST'&&url.pathname==='/api/desktop/mirror/commit'){const body=await readJson(req);try{return sendJson(req,res,200,desktopStore.commit(String(body.uploadId||''),String(body.generation||Date.now())))}catch(e){throw new YoutubeError(e.message,'DESKTOP_MIRROR_INVALID',400)}}
      if(desktopStore&&(req.method==='GET'||req.method==='HEAD')&&url.pathname==='/api/desktop/mirror/snapshot'){return sendJson(req,res,200,{snapshot:desktopStore.info().snapshot});}
      if(desktopStore&&(req.method==='GET'||req.method==='HEAD')&&url.pathname==='/api/desktop/mirror/records'){const store=String(url.searchParams.get('store')||''),afterKey=String(url.searchParams.get('after')||'').slice(0,500),limit=Math.max(1,Math.min(100,Number(url.searchParams.get('limit'))||50));try{const page=desktopStore.page(store,afterKey,limit);return sendJson(req,res,200,req.method==='HEAD'?{store,records:[],nextKey:page.nextKey}:page)}catch(e){throw new YoutubeError(e.message,'DESKTOP_MIRROR_INVALID',400)}}
      if(req.method==='POST'&&url.pathname==='/api/discover'){
        rateLimit(req,'discover',limits.discover);const body=await readJson(req);const channel=String(body.channel||'').trim();if(!channel||channel.length>1000)throw new YoutubeError('Channel URL or handle is required and must be under 1,000 characters.','INVALID_CHANNEL',400);if(body.apiKey)throw new YoutubeError('YouTube Data API discovery is intentionally disabled for arbitrary-channel archives. Use the built-in public discovery paths.','YOUTUBE_DATA_API_DISABLED',400);const requestedMax=Number(body.maxVideos);const maxVideos=Number.isFinite(requestedMax)?Math.min(Math.max(Math.trunc(requestedMax),1),50000):50000;const result=await discoverChannel(channel,{maxVideos});return sendJson(req,res,200,result);
      }
      if(req.method==='POST'&&url.pathname==='/api/transcript'){
        rateLimit(req,'transcript',limits.transcript);const body=await readJson(req);const language=validateCommon(body);if(!validVideoId(body.videoId))throw new YoutubeError('Invalid YouTube video ID.','INVALID_VIDEO_ID',400);const key=`${body.videoId}:${language}`;const {value,cached}=await transcriptCache.getOrCreate(key,()=>fetchTranscript(body.videoId,{language}));return sendJson(req,res,200,{...value,cached});
      }
      if(req.method==='POST'&&url.pathname==='/api/comments'){
        rateLimit(req,'comments',limits.comments);const body=await readJson(req);if(!validVideoId(body.videoId))throw new YoutubeError('Invalid YouTube video ID.','INVALID_VIDEO_ID',400);const requested=Number(body.maxComments),maxComments=Number.isFinite(requested)?Math.min(200,Math.max(10,Math.trunc(requested))):100;const sort=body.sort==='new'?'new':'top';const key=`${body.videoId}:${sort}:${maxComments}`;const {value,cached,shared}=await commentsCache.getOrCreate(key,()=>commentFetcher(body.videoId,{maxComments,sort}));return sendJson(req,res,200,{...value,cached,shared:Boolean(shared)});
      }
      if(req.method==='POST'&&url.pathname==='/api/transcribe'){
        rateLimit(req,'ai-endpoint',Math.max(limits.ai*20,120));const body=await readJson(req);const language=validateCommon(body);if(!validVideoId(body.videoId))throw new YoutubeError('Invalid YouTube video ID.','INVALID_VIDEO_ID',400);if(!desktopStore&&!aiAllowed(req,body.accessToken||req.headers['x-ai-access-token']))throw new YoutubeError('Paid AI transcription is disabled for this client. Configure AI_ACCESS_TOKEN or use the local loopback server.','AI_ACCESS_DENIED',403);const key=`${body.videoId}:${language}`;const {value,cached,shared}=await aiCache.getOrCreate(key,async()=>{rateLimit(req,'ai-billable',limits.ai);return transcribeVideoAudio(body.videoId,{language})});return sendJson(req,res,200,{...value,cached,shared:Boolean(shared)});
      }
      if(req.method==='POST'&&url.pathname==='/api/research/synthesize'){
        rateLimit(req,'research-endpoint',limits.research*10);const body=await readJson(req,512*1024);if(!desktopStore&&!aiAllowed(req,body.accessToken||req.headers['x-ai-access-token']))throw new YoutubeError('AI research synthesis is disabled for this client.','AI_ACCESS_DENIED',403);const evidence=Array.isArray(body.evidence)?body.evidence:[];const key=sha256(JSON.stringify({q:body.question,e:evidence}));const {value,cached}=await researchCache.getOrCreate(key,async()=>{rateLimit(req,'research-billable',limits.research);return synthesizeResearch(body.question,evidence)});return sendJson(req,res,200,{...value,cached});
      }
      if(req.method==='POST'&&url.pathname==='/api/embeddings'){
        rateLimit(req,'embedding-endpoint',limits.embedding*20);const body=await readJson(req,768*1024);if(!desktopStore&&!aiAllowed(req,req.headers['x-ai-access-token']))throw new YoutubeError('Semantic embedding access is disabled for this client.','AI_ACCESS_DENIED',403);const texts=Array.isArray(body.texts)?body.texts:[];const info=embeddingProviderInfo();const key=sha256(JSON.stringify({providerId:info.providerId,texts}));const {value,cached,shared}=await embeddingCache.getOrCreate(key,async()=>{rateLimit(req,'embedding-billable',limits.embedding);return embedTexts(texts)});return sendJson(req,res,200,{...value,cached,shared:Boolean(shared)});
      }
      if(req.method==='POST'&&url.pathname==='/api/visual/analyze'){
        rateLimit(req,'visual-endpoint',Math.max(limits.visual*10,60));const body=await readJson(req,64*1024);if(!desktopStore&&!aiAllowed(req,req.headers['x-ai-access-token']))throw new YoutubeError('Visual AI access is disabled for this client. Configure AI_ACCESS_TOKEN or use the local loopback server.','AI_ACCESS_DENIED',403);if(!validVideoId(body.videoId))throw new YoutubeError('Invalid YouTube video ID.','INVALID_VIDEO_ID',400);const focus=String(body.focus||'general').trim().slice(0,600);const info=visualProviderInfo();const key=sha256(JSON.stringify({videoId:body.videoId,focus,provider:info.provider,model:info.model,processing:info.processing}));const {value,cached,shared}=await visualCache.getOrCreate(key,async()=>{rateLimit(req,'visual-billable',limits.visual);return visualAnalyzer(body.videoId,{focus})});return sendJson(req,res,200,{...value,cached,shared:Boolean(shared)});
      }
      if((req.method==='GET'||req.method==='HEAD')&&url.pathname==='/api/monitors'){
        rateLimit(req,'monitor-endpoint',limits.monitor);assertSafeOrigin(req);if(!desktopStore&&!monitorAllowed(req,req.headers['x-monitor-access-token']))throw new YoutubeError('Background monitoring access is disabled for this client. Configure MONITOR_ACCESS_TOKEN on hosted deployments.','MONITOR_ACCESS_DENIED',403);const monitors=await monitorManager.list();return sendJson(req,res,200,{monitors,monitoring:monitorPublicInfo(monitorManager.status())});
      }
      if(req.method==='POST'&&url.pathname==='/api/monitors/upsert'){
        rateLimit(req,'monitor-endpoint',limits.monitor);const body=await readJson(req);if(!desktopStore&&!monitorAllowed(req,req.headers['x-monitor-access-token']))throw new YoutubeError('Background monitoring access is disabled for this client.','MONITOR_ACCESS_DENIED',403);const monitor=await monitorManager.upsert({id:String(body.id||''),channel:String(body.channel||''),language:String(body.language||'en'),intervalMinutes:body.intervalMinutes,enabled:body.enabled!==false});return sendJson(req,res,200,{monitor});
      }
      if(req.method==='POST'&&url.pathname==='/api/monitors/enabled'){
        rateLimit(req,'monitor-endpoint',limits.monitor);const body=await readJson(req);if(!desktopStore&&!monitorAllowed(req,req.headers['x-monitor-access-token']))throw new YoutubeError('Background monitoring access is disabled for this client.','MONITOR_ACCESS_DENIED',403);const monitor=await monitorManager.setEnabled(String(body.id||''),Boolean(body.enabled));return sendJson(req,res,200,{monitor});
      }
      if(req.method==='POST'&&url.pathname==='/api/monitors/delete'){
        rateLimit(req,'monitor-endpoint',limits.monitor);const body=await readJson(req);if(!desktopStore&&!monitorAllowed(req,req.headers['x-monitor-access-token']))throw new YoutubeError('Background monitoring access is disabled for this client.','MONITOR_ACCESS_DENIED',403);const result=await monitorManager.remove(String(body.id||''));return sendJson(req,res,200,result);
      }
      if(req.method==='POST'&&url.pathname==='/api/monitors/run'){
        rateLimit(req,'monitor-endpoint',limits.monitor);const body=await readJson(req);if(!desktopStore&&!monitorAllowed(req,req.headers['x-monitor-access-token']))throw new YoutubeError('Background monitoring access is disabled for this client.','MONITOR_ACCESS_DENIED',403);const id=String(body.id||'');if(!await monitorManager.get(id))throw new YoutubeError('Monitor not found.','MONITOR_NOT_FOUND',404);void monitorManager.runNow(id).catch(e=>console.error(`[monitor ${id}]`,e?.message||e));return sendJson(req,res,202,{accepted:true,id});
      }
      if((req.method==='GET'||req.method==='HEAD')&&url.pathname==='/api/monitors/snapshot'){
        rateLimit(req,'monitor-endpoint',limits.monitor);assertSafeOrigin(req);if(!desktopStore&&!monitorAllowed(req,req.headers['x-monitor-access-token']))throw new YoutubeError('Background monitoring access is disabled for this client.','MONITOR_ACCESS_DENIED',403);const id=String(url.searchParams.get('id')||'');const iterator=monitorManager.snapshotLines(id)[Symbol.asyncIterator]();const first=await iterator.next();if(first.done)throw new YoutubeError('Snapshot is empty.','SNAPSHOT_NOT_READY',404);res.writeHead(200,{...baseHeaders(req),'content-type':'application/x-ndjson; charset=utf-8','cache-control':'no-store','content-disposition':`attachment; filename="youtube-monitor-${id}.ykm.jsonl"`});if(req.method==='HEAD'){res.end();return}res.write(first.value);try{for(;;){const n=await iterator.next();if(n.done)break;if(!res.write(n.value))await new Promise(resolve=>res.once('drain',resolve));}res.end()}catch(e){console.error(`[${req.requestId}] snapshot ${id}`,e);res.destroy(e)}return;
      }
      if(req.method==='GET'||req.method==='HEAD'){
        if(await staticFile(req,res,url.pathname))return; if(!url.pathname.startsWith('/api/')&&await staticFile(req,res,'/index.html'))return;
      }
      return sendJson(req,res,404,{error:'Not found',code:'NOT_FOUND'});
    }catch(error){const status=error instanceof YoutubeError?error.status:500;const code=error instanceof YoutubeError?error.code:'INTERNAL_ERROR';if(status>=500)console.error(`[${new Date().toISOString()}] [${req.requestId}] ${req.method} ${url.pathname}`,error);const extra=status===429&&error?.details?.resetAt?{'retry-after':String(Math.max(1,Math.ceil((error.details.resetAt-Date.now())/1000)))}:{};return sendJson(req,res,status,{error:error?.message||'Internal server error',code,...(error?.details?{details:error.details}:{})},extra);}
  });
  // Tight request/header windows limit slow-client resource exhaustion without
  // imposing a timeout on long-running transcription response generation.
  server.headersTimeout=15_000;
  server.requestTimeout=30_000;
  server.keepAliveTimeout=5_000;
  server.maxRequestsPerSocket=100;
  server.maxHeadersCount=100;
  server.maxConnections=envInt('MAX_CONNECTIONS',200,10,10000);
  server.on('close',()=>monitorManager.stopScheduler());
  return server;
}

const isMain=process.argv[1]&&import.meta.url===pathToFileURL(process.argv[1]).href;
if(isMain){const PORT=Number(process.env.PORT||3000);const HOST=process.env.HOST||(process.env.NODE_ENV==='production'?'0.0.0.0':'127.0.0.1');const server=createServer();server.listen(PORT,HOST,()=>console.log(`YouTube Knowledge Engine v${VERSION} running at http://${HOST}:${PORT}`));let shuttingDown=false;const shutdown=(signal)=>{if(shuttingDown)return;shuttingDown=true;console.log(`${signal}: stopping server`);server.closeIdleConnections?.();const force=setTimeout(()=>{server.closeAllConnections?.();process.exit(1)},10_000);force.unref();server.close(()=>{clearTimeout(force);process.exit(0)})};process.on('SIGTERM',()=>shutdown('SIGTERM'));process.on('SIGINT',()=>shutdown('SIGINT'));}
