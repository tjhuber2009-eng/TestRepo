import http from 'node:http';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { mkdtemp, readdir, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const execFileAsync = promisify(execFile);
const PORT = Number(process.env.PORT || 3000);
const argsCommon = ['--ignore-config', '--js-runtimes', 'node'];

async function ytdlp(args, opts={}) {
  return execFileAsync('yt-dlp', [...argsCommon, ...args], {
    timeout: opts.timeout || 180000,
    maxBuffer: opts.maxBuffer || 32 * 1024 * 1024,
  });
}

async function probe() {
  const version = (await execFileAsync('yt-dlp', ['--ignore-config','--version'], {timeout:10000})).stdout.trim();
  const channelUrl = 'https://www.youtube.com/@OpenAI/videos';
  const { stdout } = await ytdlp([
    '--flat-playlist','--dump-single-json','--quiet','--no-warnings',
    '--playlist-end','5', channelUrl
  ], {timeout:600000, maxBuffer:64*1024*1024});
  const data = JSON.parse(stdout);
  const videos = (data.entries || []).slice(0,5).map(v => ({
    id:v.id, title:v.title, duration:v.duration ?? null, views:v.view_count ?? null
  })).filter(v => /^[A-Za-z0-9_-]{11}$/.test(String(v.id||'')));
  if (!videos.length) throw new Error('No videos discovered');

  let caption = null;
  const attempts = [];
  for (const video of videos) {
    try {
      const metaOut = await ytdlp([
        '--no-playlist','--skip-download','--dump-single-json','--quiet','--no-warnings',
        'https://www.youtube.com/watch?v=' + video.id
      ]);
      const meta = JSON.parse(metaOut.stdout);
      const manual = meta.subtitles || {};
      const auto = meta.automatic_captions || {};
      const candidates = [
        ...Object.keys(manual).filter(k => /^en([_-]|$)/i.test(k)).map(language=>({language,generated:false})),
        ...Object.keys(auto).filter(k => /^en([_-]|$)/i.test(k)).map(language=>({language,generated:true})),
        ...Object.keys(manual).map(language=>({language,generated:false})),
        ...Object.keys(auto).map(language=>({language,generated:true})),
      ];
      const chosen = candidates[0];
      attempts.push({videoId:video.id, title:video.title, subtitleLanguages:candidates.slice(0,10)});
      if (!chosen) continue;
      const dir = await mkdtemp(join(tmpdir(),'yke-probe-'));
      try {
        const writeArg = chosen.generated ? '--write-auto-subs' : '--write-subs';
        await ytdlp([
          '--no-playlist','--skip-download', writeArg,
          '--sub-langs', chosen.language,
          '--sub-format','vtt/best','--convert-subs','vtt',
          '--quiet','--no-warnings',
          '-o', join(dir,'caption.%(ext)s'),
          'https://www.youtube.com/watch?v=' + video.id
        ]);
        const files = await readdir(dir);
        const name = files.find(f=>f.endsWith('.vtt'));
        if (!name) continue;
        const raw = await readFile(join(dir,name),'utf8');
        if (!raw.trim()) continue;
        caption = {
          videoId:video.id, title:video.title, language:chosen.language,
          generated:chosen.generated, bytes:Buffer.byteLength(raw),
          startsWith:raw.slice(0,160)
        };
        break;
      } finally {
        await rm(dir,{recursive:true,force:true}).catch(()=>{});
      }
    } catch (e) {
      attempts.push({videoId:video.id,error:String(e.stderr||e.message).slice(0,500)});
    }
  }
  if (!caption) throw new Error('No downloadable public caption found in first five videos: ' + JSON.stringify(attempts));
  return {
    ok:true,
    testedAt:new Date().toISOString(),
    node:process.version,
    ytdlp:version,
    channel:{title:data.channel||data.uploader||data.title,id:data.channel_id||data.uploader_id||'',url:channelUrl},
    videos,
    caption,
    attempts
  };
}

const server=http.createServer(async(req,res)=>{
  res.setHeader('content-type','application/json; charset=utf-8');
  if(req.url==='/health'){res.end(JSON.stringify({ok:true,node:process.version}));return;}
  if(req.url==='/probe'){
    try{res.end(JSON.stringify(await probe()));}
    catch(e){res.statusCode=500;res.end(JSON.stringify({ok:false,error:String(e.message||e),stderr:String(e.stderr||'').slice(0,1200)}));}
    return;
  }
  res.statusCode=404;res.end(JSON.stringify({error:'not found'}));
});
server.listen(PORT,'0.0.0.0',()=>console.log('YKE live probe listening on',PORT));
