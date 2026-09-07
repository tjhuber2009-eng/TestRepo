#!/usr/bin/env node
import process from 'node:process';
import { fileURLToPath } from 'node:url';
import { resolve } from 'node:path';
import { discoverChannel, fetchTranscript, ytdlpAvailable } from '../lib/youtube.js';

const DEFAULT_CHANNEL='@OpenAI';
const DEFAULT_MAX=5;
const TRANSIENT_CODES=new Set(['YOUTUBE_NETWORK_BLOCKED','YOUTUBE_BLOCKED','YTDLP_UNAVAILABLE','YTDLP_DISCOVERY_FAILED']);
const SKIPPABLE_CAPTION_CODES=new Set(['NO_CAPTIONS','EMPTY_TRANSCRIPT','NON_PUBLIC_VIDEO','VIDEO_UNAVAILABLE']);

export function parseArgs(argv=[]){
  const out={channel:DEFAULT_CHANNEL,maxVideos:DEFAULT_MAX,language:'en',json:false,help:false};
  for(let i=0;i<argv.length;i++){
    const arg=argv[i];
    if(arg==='--help'||arg==='-h')out.help=true;
    else if(arg==='--json')out.json=true;
    else if(arg==='--channel'||arg==='-c')out.channel=String(argv[++i]||'').trim();
    else if(arg==='--max'||arg==='-n')out.maxVideos=Math.max(1,Math.min(50,Number.parseInt(argv[++i]||'',10)||DEFAULT_MAX));
    else if(arg==='--language'||arg==='-l')out.language=String(argv[++i]||'').trim();
    else throw new Error(`Unknown argument: ${arg}`);
  }
  if(!out.channel&&!out.help)throw new Error('Channel is required.');
  return out;
}

export function classifyError(error){
  const code=String(error?.code||'');
  if(TRANSIENT_CODES.has(code)||/fetch failed|ENOTFOUND|ECONN|ETIMEDOUT|network/i.test(String(error?.message||'')))return 'BLOCKED';
  if(SKIPPABLE_CAPTION_CODES.has(code))return 'SKIP';
  return 'FAIL';
}

function help(){
  return `YouTube Knowledge Engine live acceptance\n\nUsage:\n  npm run acceptance:youtube -- [options]\n\nOptions:\n  -c, --channel <url|@handle>  Channel to test (default: ${DEFAULT_CHANNEL})\n  -n, --max <1-50>            Videos to sample (default: ${DEFAULT_MAX})\n  -l, --language <code>       Preferred caption language (default: en)\n      --json                  Emit one JSON result\n  -h, --help                  Show this help\n\nExit codes:\n  0  PASS\n  1  FAIL (application/protocol error)\n  2  BLOCKED (host cannot reach/extract from YouTube)\n  3  INCONCLUSIVE (catalog worked but sampled videos had no usable captions)\n`;
}

function compactError(error){return {code:String(error?.code||'ERROR'),message:String(error?.message||error).slice(0,800)}}

export async function runAcceptance(options={}){
  const opts={channel:DEFAULT_CHANNEL,maxVideos:DEFAULT_MAX,language:'en',...options};
  const started=Date.now();
  const tool={ytDlp:await ytdlpAvailable()};
  let discovery;
  try{
    discovery=await discoverChannel(opts.channel,{maxVideos:opts.maxVideos});
  }catch(error){
    const status=classifyError(error)==='BLOCKED'?'BLOCKED':'FAIL';
    return {status,phase:'discovery',channelInput:opts.channel,tool,error:compactError(error),elapsedMs:Date.now()-started};
  }
  const videos=(discovery.videos||[]).slice(0,opts.maxVideos);
  if(!videos.length)return {status:'FAIL',phase:'discovery',channelInput:opts.channel,tool,source:discovery.source,error:{code:'EMPTY_CATALOG',message:'Discovery returned no public videos.'},elapsedMs:Date.now()-started};

  const attempts=[];
  for(const video of videos){
    try{
      const transcript=await fetchTranscript(video.id,{language:opts.language,allowYtdlpFallback:true});
      if(!transcript?.text?.trim()||!Array.isArray(transcript.segments)||!transcript.segments.length)throw Object.assign(new Error('Transcript returned without readable text/segments.'),{code:'INVALID_TRANSCRIPT'});
      return {
        status:'PASS',phase:'caption',channelInput:opts.channel,tool,source:discovery.source,
        channel:{id:discovery.channel?.id||'',title:discovery.channel?.title||'',url:discovery.channel?.url||''},
        discovered:videos.length,truncated:Boolean(discovery.truncated),
        transcript:{videoId:video.id,title:video.title||'',source:transcript.source||'',language:transcript.language||'',generated:Boolean(transcript.generated),words:Number(transcript.words||0),segments:transcript.segments.length},
        attempts,elapsedMs:Date.now()-started,
      };
    }catch(error){
      const classification=classifyError(error);
      attempts.push({videoId:video.id,title:String(video.title||'').slice(0,300),classification,error:compactError(error)});
      if(classification==='BLOCKED')return {status:'BLOCKED',phase:'caption',channelInput:opts.channel,tool,source:discovery.source,channel:discovery.channel,discovered:videos.length,attempts,elapsedMs:Date.now()-started};
      if(classification==='FAIL')return {status:'FAIL',phase:'caption',channelInput:opts.channel,tool,source:discovery.source,channel:discovery.channel,discovered:videos.length,attempts,elapsedMs:Date.now()-started};
    }
  }
  return {status:'INCONCLUSIVE',phase:'caption',channelInput:opts.channel,tool,source:discovery.source,channel:discovery.channel,discovered:videos.length,attempts,elapsedMs:Date.now()-started};
}

async function main(){
  let options;
  try{options=parseArgs(process.argv.slice(2))}catch(error){console.error(error.message);console.error(help());process.exitCode=1;return}
  if(options.help){console.log(help());return}
  const result=await runAcceptance(options);
  if(options.json)console.log(JSON.stringify(result));
  else{
    console.log(`YKE live acceptance: ${result.status}`);
    console.log(`Phase: ${result.phase}`);
    if(result.source)console.log(`Discovery source: ${result.source}`);
    if(result.channel?.title)console.log(`Channel: ${result.channel.title}`);
    if(Number.isFinite(result.discovered))console.log(`Videos sampled: ${result.discovered}`);
    if(result.transcript)console.log(`Caption: ${result.transcript.videoId} | ${result.transcript.words} words | ${result.transcript.segments} segments | ${result.transcript.source}`);
    if(result.error)console.log(`Error: ${result.error.code}: ${result.error.message}`);
    if(result.attempts?.length&&result.status!=='PASS')for(const a of result.attempts)console.log(`Attempt ${a.videoId}: ${a.error.code}: ${a.error.message}`);
    console.log(`Elapsed: ${result.elapsedMs} ms`);
  }
  process.exitCode=result.status==='PASS'?0:result.status==='BLOCKED'?2:result.status==='INCONCLUSIVE'?3:1;
}

const invoked=process.argv[1]&&resolve(process.argv[1])===resolve(fileURLToPath(import.meta.url));
if(invoked)await main();
