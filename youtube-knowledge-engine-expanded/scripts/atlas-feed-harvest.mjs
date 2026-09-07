import {readFile,writeFile,mkdir} from 'node:fs/promises';
import {dirname,resolve} from 'node:path';
import {discoverChannel,fetchTranscript} from '../lib/youtube.js';
import {createAnalysisAccumulator,addVideoToAnalysis,finalizeAnalysis,parsePublishedMs} from '../public/analysis-core.js';
import {analysisReportToAtlasFeed,mergeAtlasFeeds,titleLooksStrategyRelevant} from '../public/atlas-feed.js';

function arg(name,fallback=''){const i=process.argv.indexOf(name);return i>=0&&process.argv[i+1]!==undefined?process.argv[i+1]:fallback}
function intArg(name,fallback,min=1,max=10000){const n=Number(arg(name,fallback));return Number.isFinite(n)?Math.max(min,Math.min(max,Math.trunc(n))):fallback}
async function readJson(path,fallback){try{return JSON.parse(await readFile(path,'utf8'))}catch(e){if(e?.code==='ENOENT')return fallback;throw e}}
async function readJsonl(path){try{return (await readFile(path,'utf8')).split(/\r?\n/).filter(Boolean).map(line=>JSON.parse(line))}catch(e){if(e?.code==='ENOENT')return[];throw e}}
async function atomicWrite(path,text){await mkdir(dirname(path),{recursive:true});const tmp=path+'.tmp';await writeFile(tmp,text,'utf8');await writeFile(path,text,'utf8').catch(()=>{});try{const {rename}=await import('node:fs/promises');await rename(tmp,path)}catch{await writeFile(path,text,'utf8')}}
function iso(){return new Date().toISOString()}
function videoDate(v){const ms=parsePublishedMs(v?.published);return Number.isFinite(ms)?new Date(ms).toISOString().slice(0,10):''}
function priority(date,cutoffs){if(!date)return 99;for(let i=0;i<cutoffs.length;i++)if(date<=cutoffs[i])return i;return cutoffs.length}
function retryDue(row,now=Date.now()){if(!row||row.status==='done')return !row;const last=Date.parse(row.lastAttemptAt||'');if(!Number.isFinite(last))return true;const days=row.status==='no_captions'?7:1;return now-last>=days*86400000}
function cleanState(raw){return raw&&raw.schema===1&&raw.videos&&typeof raw.videos==='object'?raw:{schema:1,updatedAt:'',videos:{},channels:{}}}
function sourceId(channelResult,input){return channelResult?.channel?.id||String(input).replace(/^@/,'handle:')}
function sourceTitle(channelResult,input){return channelResult?.channel?.title||String(input)}
function mergeMetadata(video,transcript){const m=transcript?.videoMetadata||{};return{
  ...video,
  title:m.title||video.title,
  published:m.published||m.uploadDate||video.published,
  duration:m.duration||video.duration,
  views:m.views||video.views,
  kind:m.kind||video.kind||'video',
  url:video.url||`https://www.youtube.com/watch?v=${video.id}`,
  status:'done',
  transcript,
}}
function classifyError(e){const code=String(e?.code||'');if(code==='NO_CAPTIONS'||code==='EMPTY_TRANSCRIPT')return'no_captions';if(/BLOCKED|RATE_LIMIT|NETWORK|HTTP/i.test(code))return'transient';return'error'}

const configPath=resolve(arg('--config','atlas-youtube-channels.json'));
const statePath=resolve(arg('--state','atlas-youtube-harvest-state.json'));
const feedPath=resolve(arg('--feed','youtube_intelligence_feed.jsonl'));
const statsPath=resolve(arg('--stats','atlas-youtube-harvest-stats.json'));
const config=await readJson(configPath,null);
if(!config||!Array.isArray(config.channels)||!config.channels.length)throw new Error('Atlas YouTube channel config has no channels.');
const state=cleanState(await readJson(statePath,null));
const existing=await readJsonl(feedPath);
const maxPerChannel=intArg('--max-per-channel',Number(config.max_per_channel_per_run)||3,1,20);
const maxCatalog=Math.max(10,Math.min(5000,Number(config.max_catalog_videos)||3000));
const minimumCompleteness=Math.max(2,Math.min(6,Number(config.minimum_completeness)||2));
const language=String(config.language||'en');
const cutoffs=(Array.isArray(config.backfill_cutoffs)?config.backfill_cutoffs:['2020-12-31','2021-12-31']).map(String).sort();
let merged=existing;
const stats={schema:1,startedAt:iso(),channels:[],existingIdeas:existing.length,newIdeas:0,transcriptsAttempted:0,transcriptsCaptured:0,blocked:false};

for(const entry of config.channels){
  const channel=String(entry.channel||entry.handle||'').trim();
  if(!channel)continue;
  const cs={channel,discovered:0,relevant:0,selected:0,captured:0,ideas:0,failures:[]};
  stats.channels.push(cs);
  let discovered;
  try{discovered=await discoverChannel(channel,{maxVideos:maxCatalog})}
  catch(e){cs.failures.push({stage:'discover',code:e?.code||'',message:String(e?.message||e).slice(0,300)});if(/BLOCKED|NETWORK|RATE_LIMIT/i.test(String(e?.code||'')))stats.blocked=true;continue}
  cs.discovered=(discovered.videos||[]).length;
  const sid=sourceId(discovered,channel);
  const candidates=(discovered.videos||[]).filter(v=>v?.id&&titleLooksStrategyRelevant(v.title||'')).map(v=>({...v,_date:videoDate(v)}));
  cs.relevant=candidates.length;
  candidates.sort((a,b)=>{
    const pa=priority(a._date,cutoffs),pb=priority(b._date,cutoffs);
    if(pa!==pb)return pa-pb;
    const da=a._date||'',db=b._date||'';
    return db.localeCompare(da)||String(a.id).localeCompare(String(b.id));
  });
  const selected=[];
  for(const v of candidates){
    const key=`${sid}:${v.id}`,prior=state.videos[key];
    if(prior?.status==='done'||!retryDue(prior))continue;
    selected.push(v);if(selected.length>=maxPerChannel)break;
  }
  cs.selected=selected.length;
  const acc=createAnalysisAccumulator({
    sourceId:sid,
    sourceTitle:sourceTitle(discovered,channel),
    sourceUrl:discovered.channel?.url||channel,
    sourceSubscribers:discovered.channel?.subscribers||'',
    syncedAt:iso(),
    asOf:Date.now(),
  });
  for(const v of selected){
    const key=`${sid}:${v.id}`,prior=state.videos[key]||{attempts:0};
    stats.transcriptsAttempted++;
    try{
      const transcript=await fetchTranscript(v.id,{language,allowYtdlpFallback:true});
      addVideoToAnalysis(acc,mergeMetadata(v,transcript));
      state.videos[key]={status:'done',lastAttemptAt:iso(),attempts:Number(prior.attempts||0)+1,published:v._date,title:String(v.title||'').slice(0,300)};
      stats.transcriptsCaptured++;cs.captured++;
    }catch(e){
      const status=classifyError(e);
      state.videos[key]={status,lastAttemptAt:iso(),attempts:Number(prior.attempts||0)+1,published:v._date,title:String(v.title||'').slice(0,300),lastError:String(e?.message||e).slice(0,300),code:String(e?.code||'')};
      cs.failures.push({stage:'transcript',videoId:v.id,code:e?.code||'',message:String(e?.message||e).slice(0,260)});
      if(status==='transient')stats.blocked=true;
    }
  }
  if(acc.processed){
    const report=finalizeAnalysis(acc);
    const ideas=analysisReportToAtlasFeed(report,{minimumCompleteness,maxIdeas:Math.max(20,maxPerChannel*20)});
    merged=mergeAtlasFeeds(merged,ideas,{maxIdeas:Number(config.max_feed_ideas)||5000});
    cs.ideas=ideas.length;stats.newIdeas+=ideas.length;
  }
  state.channels[sid]={channel,title:sourceTitle(discovered,channel),lastRunAt:iso(),discovered:cs.discovered,relevant:cs.relevant};
}

state.updatedAt=iso();
stats.completedAt=iso();stats.totalIdeas=merged.length;
await atomicWrite(statePath,JSON.stringify(state,null,2)+'\n');
await atomicWrite(feedPath,merged.map(x=>JSON.stringify(x)).join('\n')+(merged.length?'\n':''));
await atomicWrite(statsPath,JSON.stringify(stats,null,2)+'\n');
console.log(JSON.stringify(stats,null,2));
if(stats.transcriptsCaptured===0&&stats.blocked)process.exitCode=2;
