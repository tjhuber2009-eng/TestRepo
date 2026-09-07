import {readFile,writeFile,mkdir,rename} from 'node:fs/promises';
import {dirname,resolve} from 'node:path';
import {discoverChannel,discoverChannelScrape,fetchTranscript} from '../lib/youtube.js';
import {createAnalysisAccumulator,addVideoToAnalysis,finalizeAnalysis} from '../public/analysis-core.js';
import {analysisReportToAtlasFeed,mergeAtlasFeeds,titleLooksStrategyRelevant} from '../public/atlas-feed.js';
import {analyzeYouTubeVideoWithGemini,geminiAnalysisToAtlasFeed} from '../lib/atlas-gemini-video.js';

function arg(name,fallback=''){const i=process.argv.indexOf(name);return i>=0&&process.argv[i+1]!==undefined?process.argv[i+1]:fallback}
function intArg(name,fallback,min=1,max=10000){const n=Number(arg(name,fallback));return Number.isFinite(n)?Math.max(min,Math.min(max,Math.trunc(n))):fallback}
async function readJson(path,fallback){try{return JSON.parse(await readFile(path,'utf8'))}catch(e){if(e?.code==='ENOENT')return fallback;throw e}}
async function readJsonl(path){try{return (await readFile(path,'utf8')).split(/\r?\n/).filter(Boolean).map(line=>JSON.parse(line))}catch(e){if(e?.code==='ENOENT')return[];throw e}}
async function atomicWrite(path,text){await mkdir(dirname(path),{recursive:true});const tmp=path+'.tmp';await writeFile(tmp,text,'utf8');try{await rename(tmp,path)}catch{await writeFile(path,text,'utf8')}}
function iso(){return new Date().toISOString()}
function isoDay(ms){return new Date(ms).toISOString().slice(0,10)}
function publicationBound(value,sourceBasis='',anchor=Date.now()){
  const s=String(value||'').trim();if(!s)return null;
  const approximate=sourceBasis==='approximate_youtubetab';
  if(/^\d{8}$/.test(s)){const t=Date.parse(`${s.slice(0,4)}-${s.slice(4,6)}-${s.slice(6,8)}T00:00:00Z`);return Number.isFinite(t)?{date:isoDay(t),basis:approximate?'relative_upper_bound':'exact'}:null}
  if(/^\d{4}-\d{2}-\d{2}(?:T|$)/.test(s)){const t=Date.parse(s);return Number.isFinite(t)?{date:isoDay(t),basis:approximate?'relative_upper_bound':'exact'}:null}
  const direct=Date.parse(s);if(Number.isFinite(direct)&&/\d{4}/.test(s))return{date:isoDay(direct),basis:approximate?'relative_upper_bound':'exact'};
  const m=s.toLowerCase().match(/(?:streamed|premiered)?\s*(\d+)\s*(day|week|month|year)s?\s+ago/);if(!m)return null;
  const n=Number(m[1]),d=new Date(anchor);
  if(m[2]==='year')d.setUTCFullYear(d.getUTCFullYear()-n);
  else if(m[2]==='month')d.setUTCMonth(d.getUTCMonth()-n);
  else d.setUTCDate(d.getUTCDate()-n*(m[2]==='week'?7:1));
  return{date:d.toISOString().slice(0,10),basis:'relative_upper_bound'};
}
function priority(date,cutoffs){if(!date)return 99;for(let i=0;i<cutoffs.length;i++)if(date<=cutoffs[i])return i;return cutoffs.length}
function retryDue(row,now=Date.now()){
  if(!row)return true;
  if(row.status==='done'||row.status==='done_gemini')return false;
  const last=Date.parse(row.lastAttemptAt||'');if(!Number.isFinite(last))return true;
  const days=row.status==='no_captions'?7:row.status==='chronology_unknown'?30:1;
  return now-last>=days*86400000;
}
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
function classifyError(e){
  const code=String(e?.code||''),message=String(e?.message||'');
  if(code==='NO_CAPTIONS'||code==='EMPTY_TRANSCRIPT')return'no_captions';
  if(/BLOCKED|RATE_LIMIT|NETWORK|HTTP|CAPTION_INSPECT_FAILED/i.test(code)||/sign in to confirm you.re not a bot/i.test(message))return'transient';
  return'error';
}
function durationMinutes(raw){
  const s=String(raw||'').trim();if(!s||s==='Short')return s==='Short'?1:null;
  if(/^\d+(?::\d+){1,2}$/.test(s)){const p=s.split(':').map(Number);const sec=p.length===3?p[0]*3600+p[1]*60+p[2]:p[0]*60+p[1];return sec/60}
  const m=s.match(/(\d+(?:\.\d+)?)\s*(?:min|minute)/i);return m?Number(m[1]):null;
}
function mergeDiscovery(primary,secondary){
  if(!secondary?.videos?.length)return primary;
  const extra=new Map(secondary.videos.map(v=>[v.id,v]));
  return{
    ...primary,
    channel:{...(primary.channel||{}),...(secondary.channel||{})},
    videos:(primary.videos||[]).map(v=>{
      const x=extra.get(v.id)||{};
      return{...v,published:v.published||x.published||'',publishedBasis:v.publishedBasis||x.publishedBasis||'',duration:v.duration||x.duration||'',views:v.views||x.views||'',thumbnail:v.thumbnail||x.thumbnail||''};
    }),
  };
}

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
const legacyOnly=config.legacy_only!==false;
const maxLegacyCutoff=cutoffs.at(-1)||'2021-12-31';
const geminiKey=String(process.env.GEMINI_API_KEY||'');
const cloud=String(process.env.GITHUB_ACTIONS||'').toLowerCase()==='true';
const maxGeminiMinutes=Math.max(30,Math.min(450,Number(config.max_gemini_minutes_per_run)||180));
let geminiMinutes=0,merged=existing;
const stats={
  schema:2,startedAt:iso(),channels:[],existingIdeas:existing.length,newIdeas:0,
  transcriptsAttempted:0,transcriptsCaptured:0,geminiVideosAnalyzed:0,
  geminiMinutesEstimated:0,geminiIdeas:0,blocked:false,
  cloudRunner:cloud,geminiConfigured:Boolean(geminiKey),needsGeminiKey:cloud&&!geminiKey,
};

for(const entry of config.channels){
  const channel=String(entry.channel||entry.handle||'').trim();if(!channel)continue;
  const cs={channel,discovered:0,relevant:0,chronologyEligible:0,selected:0,captured:0,geminiAnalyzed:0,ideas:0,failures:[]};
  stats.channels.push(cs);
  let discovered;
  try{discovered=await discoverChannel(channel,{maxVideos:maxCatalog})}
  catch(e){cs.failures.push({stage:'discover',code:e?.code||'',message:String(e?.message||e).slice(0,300)});if(/BLOCKED|NETWORK|RATE_LIMIT/i.test(String(e?.code||'')))stats.blocked=true;continue}

  const missingPublished=(discovered.videos||[]).filter(v=>!v.published).length;
  if(missingPublished>Math.max(5,(discovered.videos||[]).length*.20)){
    try{
      const scraped=await discoverChannelScrape(channel,{maxVideos:maxCatalog,maxPages:Math.max(20,Math.ceil(maxCatalog/20))});
      discovered=mergeDiscovery(discovered,scraped);
    }catch(e){cs.failures.push({stage:'publication_metadata',code:e?.code||'',message:String(e?.message||e).slice(0,240)})}
  }

  cs.discovered=(discovered.videos||[]).length;
  const sid=sourceId(discovered,channel);
  const candidates=(discovered.videos||[])
    .filter(v=>v?.id&&titleLooksStrategyRelevant(v.title||''))
    .filter(v=>config.include_streams===true||((v.kind||'')!=='stream'&&!/\blive\b/i.test(String(v.title||''))))
    .map(v=>{const pub=publicationBound(v.published,v.publishedBasis||'');return{...v,_publication:pub,_date:pub?.date||''}})
    .filter(v=>!legacyOnly||Boolean(v._date&&v._date<=maxLegacyCutoff));
  cs.relevant=candidates.length;
  cs.chronologyEligible=candidates.filter(v=>Boolean(v._date&&v._date<=maxLegacyCutoff)).length;
  candidates.sort((a,b)=>{
    const pa=priority(a._date,cutoffs),pb=priority(b._date,cutoffs);
    if(pa!==pb)return pa-pb;
    const da=a._date||'',db=b._date||'';
    return db.localeCompare(da)||String(a.id).localeCompare(String(b.id));
  });
  const selected=[];
  for(const v of candidates){
    const key=`${sid}:${v.id}`,prior=state.videos[key];
    if(!retryDue(prior))continue;
    selected.push(v);if(selected.length>=maxPerChannel)break;
  }
  cs.selected=selected.length;

  if(cloud&&!geminiKey){
    cs.failures.push({stage:'cloud_video_analysis',code:'GEMINI_KEY_MISSING',message:'GitHub-hosted YouTube caption retrieval is challenged; configure the free-tier GEMINI_API_KEY to use direct public YouTube URL video understanding.'});
    state.channels[sid]={channel,title:sourceTitle(discovered,channel),lastRunAt:iso(),discovered:cs.discovered,relevant:cs.relevant,chronologyEligible:cs.chronologyEligible};
    continue;
  }

  const acc=createAnalysisAccumulator({
    sourceId:sid,sourceTitle:sourceTitle(discovered,channel),
    sourceUrl:discovered.channel?.url||channel,
    sourceSubscribers:discovered.channel?.subscribers||'',syncedAt:iso(),asOf:Date.now(),
  });
  let directIdeas=[];

  for(const v of selected){
    const key=`${sid}:${v.id}`,prior=state.videos[key]||{attempts:0};
    const attempt=Number(prior.attempts||0)+1;
    const publication=v._publication;
    if(cloud&&geminiKey){
      if(!publication){
        state.videos[key]={status:'chronology_unknown',lastAttemptAt:iso(),attempts:attempt,published:'',title:String(v.title||'').slice(0,300),lastError:'No independently derived publication bound; Gemini analysis skipped.'};
        continue;
      }
      const estimate=Math.max(1,durationMinutes(v.duration)??30);
      if(geminiMinutes+estimate>maxGeminiMinutes)continue;
      try{
        const analysis=await analyzeYouTubeVideoWithGemini({videoUrl:`https://www.youtube.com/watch?v=${v.id}`});
        const ideas=geminiAnalysisToAtlasFeed({
          analysis,video:v,channel:{id:sid,title:sourceTitle(discovered,channel)},
          publishedAt:publication.date,publicationBasis:publication.basis,
        });
        directIdeas.push(...ideas);geminiMinutes+=estimate;stats.geminiVideosAnalyzed++;cs.geminiAnalyzed++;stats.geminiIdeas+=ideas.length;
        state.videos[key]={status:'done_gemini',lastAttemptAt:iso(),attempts:attempt,published:publication.date,publicationBasis:publication.basis,title:String(v.title||'').slice(0,300),ideaCount:ideas.length,model:analysis.model};
      }catch(e){
        const status=classifyError(e);
        state.videos[key]={status,lastAttemptAt:iso(),attempts:attempt,published:publication.date,publicationBasis:publication.basis,title:String(v.title||'').slice(0,300),lastError:String(e?.message||e).slice(0,300),code:String(e?.code||'')};
        cs.failures.push({stage:'gemini_video',videoId:v.id,code:e?.code||'',message:String(e?.message||e).slice(0,260)});
        if(status==='transient')stats.blocked=true;
      }
      continue;
    }

    stats.transcriptsAttempted++;
    try{
      const transcript=await fetchTranscript(v.id,{language,allowYtdlpFallback:true});
      const normalized={...v,published:v._date||v.published};
      addVideoToAnalysis(acc,mergeMetadata(normalized,transcript));
      state.videos[key]={status:'done',lastAttemptAt:iso(),attempts:attempt,published:v._date,title:String(v.title||'').slice(0,300)};
      stats.transcriptsCaptured++;cs.captured++;
    }catch(e){
      if(geminiKey&&publication){
        const estimate=Math.max(1,durationMinutes(v.duration)??30);
        if(geminiMinutes+estimate<=maxGeminiMinutes){
          try{
            const analysis=await analyzeYouTubeVideoWithGemini({videoUrl:`https://www.youtube.com/watch?v=${v.id}`});
            const ideas=geminiAnalysisToAtlasFeed({analysis,video:v,channel:{id:sid,title:sourceTitle(discovered,channel)},publishedAt:publication.date,publicationBasis:publication.basis});
            directIdeas.push(...ideas);geminiMinutes+=estimate;stats.geminiVideosAnalyzed++;cs.geminiAnalyzed++;stats.geminiIdeas+=ideas.length;
            state.videos[key]={status:'done_gemini',lastAttemptAt:iso(),attempts:attempt,published:publication.date,publicationBasis:publication.basis,title:String(v.title||'').slice(0,300),ideaCount:ideas.length,model:analysis.model};
            continue;
          }catch(ge){e=ge}
        }
      }
      const status=classifyError(e);
      state.videos[key]={status,lastAttemptAt:iso(),attempts:attempt,published:v._date,title:String(v.title||'').slice(0,300),lastError:String(e?.message||e).slice(0,300),code:String(e?.code||'')};
      cs.failures.push({stage:'transcript',videoId:v.id,code:e?.code||'',message:String(e?.message||e).slice(0,260)});
      if(status==='transient')stats.blocked=true;
    }
  }

  let ideas=[...directIdeas];
  if(acc.processed){
    const report=finalizeAnalysis(acc);
    ideas.push(...analysisReportToAtlasFeed(report,{minimumCompleteness,maxIdeas:Math.max(20,maxPerChannel*20)}));
  }
  ideas=mergeAtlasFeeds([],ideas,{maxIdeas:Math.max(20,maxPerChannel*20)});
  if(ideas.length){
    merged=mergeAtlasFeeds(merged,ideas,{maxIdeas:Number(config.max_feed_ideas)||5000});
    cs.ideas=ideas.length;stats.newIdeas+=ideas.length;
  }
  state.channels[sid]={channel,title:sourceTitle(discovered,channel),lastRunAt:iso(),discovered:cs.discovered,relevant:cs.relevant,chronologyEligible:cs.chronologyEligible};
}

state.updatedAt=iso();
stats.completedAt=iso();stats.totalIdeas=merged.length;stats.geminiMinutesEstimated=Math.round(geminiMinutes*10)/10;
await atomicWrite(statePath,JSON.stringify(state,null,2)+'\n');
await atomicWrite(feedPath,merged.map(x=>JSON.stringify(x)).join('\n')+(merged.length?'\n':''));
await atomicWrite(statsPath,JSON.stringify(stats,null,2)+'\n');
console.log(JSON.stringify(stats,null,2));
if(stats.transcriptsCaptured===0&&stats.geminiVideosAnalyzed===0&&stats.blocked)process.exitCode=2;
