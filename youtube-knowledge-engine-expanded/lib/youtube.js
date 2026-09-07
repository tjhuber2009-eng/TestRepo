import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { mkdtemp, readFile, readdir, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { AsyncSemaphore } from './semaphore.js';
import { envInt, readResponseTextLimited } from './util.js';

const execFileAsync = promisify(execFile);
const ytdlpSemaphore = new AsyncSemaphore(envInt('YTDLP_MAX_CONCURRENCY', 4, 1, 16));
const ytdlpBin=()=>process.env.YTDLP_BIN||'yt-dlp';
const ytdlpJsRuntime=()=>`node:${process.env.YTDLP_NODE_BIN||process.execPath}`;
const runYtdlp = (args, options) => ytdlpSemaphore.run(() => execFileAsync(ytdlpBin(), args, options));
const YOUTUBE_HOST_RE = /(^|\.)youtube\.com$/i;
const DEFAULT_HEADERS = {
  'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36',
  'accept-language': 'en-US,en;q=0.9',
};

export class YoutubeError extends Error {
  constructor(message, code = 'YOUTUBE_ERROR', status = 502, details = undefined) {
    super(message);
    this.name = 'YoutubeError';
    this.code = code;
    this.status = status;
    this.details = details;
  }
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
function retryAfterMs(response){const raw=response?.headers?.get?.('retry-after');if(!raw)return 0;const seconds=Number(raw);if(Number.isFinite(seconds)&&seconds>=0)return Math.min(30_000,Math.round(seconds*1000));const date=Date.parse(raw);return Number.isFinite(date)?Math.min(30_000,Math.max(0,date-Date.now())):0}
async function youtubeText(response,maxMb=32){try{return await readResponseTextLimited(response,maxMb*1024*1024)}catch(e){if(e?.code==='RESPONSE_TOO_LARGE')throw new YoutubeError('YouTube returned an unexpectedly large response; the request was stopped to protect server memory.','YOUTUBE_RESPONSE_TOO_LARGE',502);throw e}}

export async function fetchWithRetry(url, options = {}, attempts = 3) {
  let lastError;
  for (let i = 0; i < attempts; i += 1) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), options.timeoutMs ?? 20000);
    try {
      const response = await fetch(url, {
        ...options,
        headers: { ...DEFAULT_HEADERS, ...(options.headers || {}) },
        signal: controller.signal,
      });
      if (response.ok) return response;
      if (![429, 500, 502, 503, 504].includes(response.status) || i === attempts - 1) {
        return response;
      }
      lastError = new Error(`HTTP ${response.status}`);
      const serverDelay=retryAfterMs(response);
      if(serverDelay) await sleep(serverDelay);
    } catch (error) {
      lastError = error;
      if (i === attempts - 1) throw error;
    } finally {
      clearTimeout(timeout);
    }
    await sleep(600 * (2 ** i) + Math.floor(Math.random() * 250));
  }
  throw lastError;
}

export function normalizeChannelUrl(input) {
  const raw = String(input || '').trim();
  if (!raw) throw new YoutubeError('Enter a YouTube channel URL or @handle.', 'INVALID_CHANNEL', 400);

  if (/^@[A-Za-z0-9._-]+$/.test(raw)) {
    return `https://www.youtube.com/${raw}/videos`;
  }
  if (/^UC[A-Za-z0-9_-]{20,}$/.test(raw)) {
    return `https://www.youtube.com/channel/${raw}/videos`;
  }

  let url;
  try {
    url = new URL(/^https?:\/\//i.test(raw) ? raw : `https://${raw}`);
  } catch {
    throw new YoutubeError('That does not look like a valid YouTube channel URL.', 'INVALID_CHANNEL', 400);
  }
  if (!YOUTUBE_HOST_RE.test(url.hostname.replace(/^www\./, ''))) {
    throw new YoutubeError('Only youtube.com channel URLs are supported.', 'INVALID_CHANNEL', 400);
  }
  url.protocol = 'https:';
  url.hostname = 'www.youtube.com';
  url.search = '';
  url.hash = '';
  url.pathname = url.pathname.replace(/\/$/, '');
  if (!/\/(videos|shorts|streams)$/.test(url.pathname)) url.pathname += '/videos';
  return url.toString();
}

export function scanJsonValue(text, startIndex) {
  let i = startIndex;
  while (/\s/.test(text[i] || '')) i += 1;
  const open = text[i];
  const close = open === '{' ? '}' : open === '[' ? ']' : null;
  if (!close) return null;
  let depth = 0;
  let inString = false;
  let escaped = false;
  for (let j = i; j < text.length; j += 1) {
    const ch = text[j];
    if (inString) {
      if (escaped) escaped = false;
      else if (ch === '\\') escaped = true;
      else if (ch === '"') inString = false;
      continue;
    }
    if (ch === '"') {
      inString = true;
      continue;
    }
    if (ch === open) depth += 1;
    else if (ch === close) {
      depth -= 1;
      if (depth === 0) return text.slice(i, j + 1);
    }
  }
  return null;
}

export function extractJsonAfterMarkers(html, markers) {
  for (const marker of markers) {
    const index = html.indexOf(marker);
    if (index === -1) continue;
    const jsonText = scanJsonValue(html, index + marker.length);
    if (!jsonText) continue;
    try { return JSON.parse(jsonText); } catch { /* try next marker */ }
  }
  return null;
}

function textValue(value) {
  if (!value) return '';
  if (typeof value === 'string') return value;
  if (typeof value.simpleText === 'string') return value.simpleText;
  if (Array.isArray(value.runs)) return value.runs.map((r) => r?.text || '').join('');
  return '';
}

function thumbnailValue(renderer) {
  const sources = [renderer.thumbnail, renderer.thumbnails, renderer.richThumbnail?.movingThumbnailRenderer?.movingThumbnailDetails];
  for (const source of sources) {
    const list = Array.isArray(source) ? source : source?.thumbnails;
    if (Array.isArray(list) && list.length) return list.at(-1)?.url || '';
  }
  return '';
}

function rendererToVideo(renderer, rendererType = '') {
  const id = renderer?.videoId;
  if (!id) return null;
  const endpointUrl = renderer?.navigationEndpoint?.commandMetadata?.webCommandMetadata?.url || '';
  const isShort = rendererType.toLowerCase().includes('reel') || endpointUrl.includes('/shorts/');
  return {
    id,
    title: textValue(renderer.title || renderer.headline) || `YouTube video ${id}`,
    published: textValue(renderer.publishedTimeText) || '',
    duration: textValue(renderer.lengthText) || (isShort ? 'Short' : ''),
    views: textValue(renderer.viewCountText || renderer.shortViewCountText) || '',
    thumbnail: thumbnailValue(renderer),
    url: `https://www.youtube.com/watch?v=${id}`,
    kind: isShort ? 'short' : 'video',
  };
}

function mergeCatalogVideo(existing, incoming, kind='') {
  if(!existing)return {...incoming,...(kind?{kind}: {})};const merged={...existing};for(const [k,v] of Object.entries(incoming||{})){if(v===''||v==null)continue;if(k==='duration'&&v==='Short'&&existing.duration&&existing.duration!=='Short')continue;if(k==='title'&&String(v).startsWith('YouTube video ')&&existing.title)continue;merged[k]=v}if(kind)merged.kind=kind;return merged;
}

const VIDEO_RENDERER_KEYS = new Set([
  'videoRenderer', 'gridVideoRenderer', 'playlistVideoRenderer', 'compactVideoRenderer',
  'reelItemRenderer', 'videoWithContextRenderer', 'videoCardRenderer',
]);

export function collectVideosAndContinuations(root) {
  const videos = new Map();
  const continuations = [];
  const seenTokens = new Set();
  const stack = [root];
  while (stack.length) {
    const node = stack.pop();
    if (!node || typeof node !== 'object') continue;
    if (Array.isArray(node)) {
      for (let i = node.length - 1; i >= 0; i -= 1) stack.push(node[i]);
      continue;
    }
    for (const [key, value] of Object.entries(node)) {
      if (VIDEO_RENDERER_KEYS.has(key) && value && typeof value === 'object') {
        const video = rendererToVideo(value, key);
        if (video && !videos.has(video.id)) videos.set(video.id, video);
      }
      if (key === 'continuationCommand' && value?.token && !seenTokens.has(value.token)) {
        seenTokens.add(value.token);
        continuations.push(value.token);
      }
      stack.push(value);
    }
  }
  return { videos: [...videos.values()], continuations };
}

function findFirst(root, keyName) {
  const stack = [root];
  while (stack.length) {
    const node = stack.pop();
    if (!node || typeof node !== 'object') continue;
    if (!Array.isArray(node) && node[keyName]) return node[keyName];
    for (const value of Object.values(node)) stack.push(value);
  }
  return null;
}

function getConfigValue(html, key) {
  const match = html.match(new RegExp(`"${key}"\\s*:\\s*"([^"\\\\]*(?:\\\\.[^"\\\\]*)*)"`));
  if (!match) return '';
  try { return JSON.parse(`"${match[1]}"`); } catch { return match[1]; }
}

export async function discoverChannelScrape(input, { maxVideos = 50000, maxPages = 2000 } = {}) {
  const normalized = normalizeChannelUrl(input);
  const baseChannelUrl = normalized.replace(/\/(videos|shorts|streams)$/, '');
  const tabs = ['videos', 'shorts', 'streams'];
  const all = new Map();
  let channelInfo = null;
  let totalPages = 0;
  let reachedPageLimit = false;

  for (const tab of tabs) {
    if (all.size >= maxVideos || totalPages >= maxPages) break;
    const channelUrl = `${baseChannelUrl}/${tab}`;
    const pageResponse = await fetchWithRetry(channelUrl);
    if (!pageResponse.ok) {
      if (tab === 'videos') {
        throw youtubeStatusError(pageResponse.status, 'YouTube channel page');
      }
      continue;
    }

    const html = await youtubeText(pageResponse, envInt('YOUTUBE_PAGE_MAX_MB',32,4,128));
    const initialData = extractJsonAfterMarkers(html, [
      'var ytInitialData = ', 'ytInitialData = ', 'window["ytInitialData"] = ', "window['ytInitialData'] = ",
    ]);
    if (!initialData) {
      if (tab === 'videos') {
        throw new YoutubeError('Could not read the channel page. YouTube may have changed its page format.', 'CHANNEL_PARSE_FAILED', 502);
      }
      continue;
    }

    const innertubeKey = getConfigValue(html, 'INNERTUBE_API_KEY');
    const clientVersion = getConfigValue(html, 'INNERTUBE_CLIENT_VERSION') || '2.20260801.00.00';
    const visitorData = getConfigValue(html, 'VISITOR_DATA');

    if (!channelInfo) {
      const channelMeta = findFirst(initialData, 'channelMetadataRenderer');
      const pageHeader = findFirst(initialData, 'pageHeaderRenderer');
      const channelId = channelMeta?.externalId || getConfigValue(html, 'CHANNEL_ID') || (html.match(/"channelId":"(UC[A-Za-z0-9_-]+)"/)?.[1] ?? '');
      const channelTitle = channelMeta?.title || pageHeader?.pageTitle || (html.match(/<meta property="og:title" content="([^"]+)"/)?.[1] ?? 'YouTube channel');
      const subs=textValue(findFirst(initialData,'subscriberCountText')); channelInfo = { id: channelId, title: decodeHtmlEntities(channelTitle), url: baseChannelUrl, subscribers:subs||'' };
    }

    let parsed = collectVideosAndContinuations(initialData);
    for (const video of parsed.videos) {
      if (tab === 'shorts') video.kind = 'short';
      else if (tab === 'streams') video.kind = 'stream';
      all.set(video.id, mergeCatalogVideo(all.get(video.id), video, tab === 'shorts' ? 'short' : tab === 'streams' ? 'stream' : ''));
      if (all.size >= maxVideos) break;
    }

    let token = parsed.continuations[0] || null;
    const usedTokens = new Set();
    totalPages += 1;

    while (token && innertubeKey && all.size < maxVideos && totalPages < maxPages) {
      if (usedTokens.has(token)) break;
      usedTokens.add(token);
      const body = {
        context: {
          client: {
            hl: 'en', gl: 'US', clientName: 'WEB', clientVersion,
            ...(visitorData ? { visitorData } : {}),
          },
        },
        continuation: token,
      };
      const response = await fetchWithRetry(`https://www.youtube.com/youtubei/v1/browse?key=${encodeURIComponent(innertubeKey)}&prettyPrint=false`, {
        method: 'POST',
        headers: { 'content-type': 'application/json', 'origin': 'https://www.youtube.com', 'referer': channelUrl },
        body: JSON.stringify(body),
      });
      if (!response.ok) break;
      const data = await response.json();
      parsed = collectVideosAndContinuations(data);
      for (const video of parsed.videos) {
        if (tab === 'shorts') video.kind = 'short';
        else if (tab === 'streams') video.kind = 'stream';
        all.set(video.id, mergeCatalogVideo(all.get(video.id), video, tab === 'shorts' ? 'short' : tab === 'streams' ? 'stream' : ''));
        if (all.size >= maxVideos) break;
      }
      const next = parsed.continuations.find((candidate) => !usedTokens.has(candidate));
      token = next || null;
      totalPages += 1;
      if (parsed.videos.length === 0 && !next) break;
    }

    if (totalPages >= maxPages) reachedPageLimit = true;
  }

  return {
    source: 'youtube-web',
    channel: channelInfo || { id: '', title: 'YouTube channel', url: baseChannelUrl },
    videos: [...all.values()].slice(0, maxVideos),
    pages: totalPages,
    truncated: all.size >= maxVideos || reachedPageLimit,
  };
}

function ytdlpCommonArgs() {
  const args = ['--ignore-config', '--js-runtimes', ytdlpJsRuntime()];
  if (process.env.YTDLP_COOKIES_FILE) args.push('--cookies', process.env.YTDLP_COOKIES_FILE);
  return args;
}

const binaryChecks=new Map();
async function hasBinary(name) {
  const now=Date.now(),cached=binaryChecks.get(name);if(cached&&cached.expiresAt>now)return cached.promise;
  const promise=execFileAsync(name,['--ignore-config','--version'],{timeout:10_000,maxBuffer:1024*1024}).then(()=>true).catch(()=>false);
  binaryChecks.set(name,{promise,expiresAt:now+5*60_000});return promise;
}

export async function ytdlpAvailable(){return hasBinary(ytdlpBin())}

export function normalizeYtdlpComments(meta,maxComments=100){const limit=Math.min(200,Math.max(1,Math.trunc(Number(maxComments)||100))),out=[];for(const raw of Array.isArray(meta?.comments)?meta.comments:[]){if(out.length>=limit)break;const text=String(raw?.text||'').replace(/\s+/g,' ').trim().slice(0,4000);if(!text)continue;out.push({commentId:String(raw?.id||'').slice(0,200),text,likeCount:Math.max(0,Math.min(1_000_000_000,Math.trunc(Number(raw?.like_count)||0))),timestamp:Number.isFinite(Number(raw?.timestamp))?Number(raw.timestamp):null,parent:String(raw?.parent||'').slice(0,200),authorIsUploader:Boolean(raw?.author_is_uploader)})}return out}

export async function fetchVideoComments(videoId,{maxComments=100,sort='top'}={}){if(!(await hasBinary(ytdlpBin())))throw new YoutubeError('Audience comment sampling requires yt-dlp on the server.','YTDLP_UNAVAILABLE',503);const max=Math.min(200,Math.max(10,Math.trunc(Number(maxComments)||100))),mode=sort==='new'?'new':'top';let stdout;try{({stdout}=await runYtdlp([...ytdlpCommonArgs(),'--skip-download','--write-comments','--dump-single-json','--quiet','--no-warnings','--extractor-args',`youtube:comment_sort=${mode};max_comments=${max},${max},0,0`,`https://www.youtube.com/watch?v=${videoId}`],{timeout:2*60_000,maxBuffer:24*1024*1024}))}catch(error){const message=String(error?.stderr||error?.message||error).slice(0,800);throw new YoutubeError(`yt-dlp could not sample public comments: ${message}`,'YTDLP_COMMENTS_FAILED',502)}let meta;try{meta=JSON.parse(stdout)}catch{throw new YoutubeError('yt-dlp returned malformed comment metadata.','YTDLP_COMMENTS_INVALID',502)}return{videoId,title:String(meta?.title||'').slice(0,500),commentCount:Number.isFinite(Number(meta?.comment_count))?Math.max(0,Number(meta.comment_count)):null,sort:mode,retrievedAt:new Date().toISOString(),source:'yt-dlp',comments:normalizeYtdlpComments(meta,max)}}

export async function discoverChannelYtdlp(input, { maxVideos = 50000 } = {}) {
  if (!(await hasBinary(ytdlpBin()))) throw new YoutubeError('yt-dlp is not installed.', 'YTDLP_UNAVAILABLE', 503);
  const normalized = normalizeChannelUrl(input).replace(/\/(videos|shorts|streams)$/, '');
  const tabs = ['videos', 'shorts', 'streams'];
  const videos = new Map();
  let channel = { id: '', title: 'YouTube channel', url: normalized };
  for (const tab of tabs) {
    if (videos.size >= maxVideos) break;
    const url = `${normalized}/${tab}`;
    let stdout;
    try {
      ({ stdout } = await runYtdlp([
        ...ytdlpCommonArgs(), '--flat-playlist', '--dump-single-json', '--quiet', '--no-warnings',
        '--extractor-args', 'youtubetab:approximate_date',
        '--playlist-end', String(maxVideos), url,
      ], { timeout: 10 * 60_000, maxBuffer: 64 * 1024 * 1024 }));
    } catch (error) {
      if (tab === 'videos' && videos.size === 0) throw new YoutubeError(`yt-dlp could not enumerate the channel: ${String(error.stderr || error.message).slice(0, 600)}`, 'YTDLP_DISCOVERY_FAILED', 502);
      continue;
    }
    let data; try { data = JSON.parse(stdout); } catch { continue; }
    channel = { id: data.channel_id || data.uploader_id || channel.id, title: data.channel || data.uploader || data.title || channel.title, url: normalized, subscribers:Number.isFinite(data.channel_follower_count)?String(data.channel_follower_count):(channel.subscribers||''), verified:Boolean(data.channel_is_verified||channel.verified) };
    for (const item of data.entries || []) {
      const id = item?.id; if (!/^[A-Za-z0-9_-]{11}$/.test(String(id || ''))) continue;
      const kind = tab === 'shorts' ? 'short' : tab === 'streams' ? 'stream' : 'video';
      const uploadDate=/^\d{8}$/.test(String(item.upload_date||''))?`${String(item.upload_date).slice(0,4)}-${String(item.upload_date).slice(4,6)}-${String(item.upload_date).slice(6,8)}`:(item.upload_date||'');
      const incoming={id,title:item.title||`YouTube video ${id}`,published:item.timestamp?new Date(item.timestamp*1000).toISOString():uploadDate,publishedBasis:(item.timestamp||uploadDate)?'approximate_youtubetab':'',duration:item.duration_string||(Number.isFinite(item.duration)?formatDuration(item.duration):''),views:item.view_count?String(item.view_count):'',thumbnail:item.thumbnail||item.thumbnails?.at?.(-1)?.url||'',url:`https://www.youtube.com/watch?v=${id}`,kind};
      videos.set(id, mergeCatalogVideo(videos.get(id), incoming, kind));
      if (videos.size >= maxVideos) break;
    }
  }
  return { source: 'yt-dlp', channel, videos: [...videos.values()].slice(0, maxVideos), truncated: videos.size >= maxVideos };
}

function formatDuration(seconds) {
  seconds = Math.max(0, Math.round(Number(seconds) || 0)); const h = Math.floor(seconds/3600); const m = Math.floor((seconds%3600)/60); const s = seconds%60;
  return h ? `${h}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}` : `${m}:${String(s).padStart(2,'0')}`;
}

export async function discoverChannel(input, options = {}) {
  const errors = [];
  if (process.env.YTDLP_DISABLE_DISCOVERY !== '1' && await hasBinary(ytdlpBin())) {
    try { return await discoverChannelYtdlp(input, options); } catch (e) { errors.push({ source: 'yt-dlp', message: e.message, code: e.code }); }
  }
  try { return await discoverChannelScrape(input, options); }
  catch (e) {
    if (/fetch failed|ECONN|ENOTFOUND|ETIMEDOUT|aborted/i.test(String(e.message))) throw new YoutubeError('This server could not reach YouTube. Check network access, then retry; hosted providers may also need a different outbound IP.', 'YOUTUBE_NETWORK_BLOCKED', 502, { attempts: errors });
    e.details = { ...(e.details || {}), attempts: errors }; throw e;
  }
}

export function chooseCaptionTrack(tracks, language) {
  if (!tracks?.length) return null;
  const wanted = String(language || '').trim().toLowerCase();
  const score = (track) => {
    const code = String(track.languageCode || '').toLowerCase();
    let value = track.kind === 'asr' ? 0 : 10;
    if (wanted && code === wanted) value += 100;
    else if (wanted && code.startsWith(`${wanted}-`)) value += 80;
    else if (wanted && wanted.startsWith(`${code}-`)) value += 70;
    else if (!wanted && (code === 'en' || code.startsWith('en-'))) value += 40;
    return value;
  };
  return [...tracks].sort((a, b) => score(b) - score(a))[0];
}

export function normalizeTranscriptSegments(input) {
  const out=[];
  for(const raw of input||[]){
    const seg={startMs:Math.max(0,Number(raw.startMs)||0),durationMs:Math.max(0,Number(raw.durationMs)||0),text:String(raw.text||'').replace(/\s+/g,' ').trim()};
    if(!seg.text)continue;
    const prev=out.at(-1);
    if(prev){
      if(seg.text===prev.text)continue;
      const close=seg.startMs <= prev.startMs + prev.durationMs + 5000;
      if(close && seg.text.startsWith(prev.text) && seg.text.length>prev.text.length){out[out.length-1]={...seg,startMs:prev.startMs,durationMs:Math.max(seg.startMs+seg.durationMs-prev.startMs,prev.durationMs)};continue}
      if(close && prev.text.startsWith(seg.text))continue;
      const a=prev.text.split(' '),b=seg.text.split(' ');let overlap=0;const max=Math.min(20,a.length,b.length);
      for(let n=1;n<=max;n++)if(a.slice(-n).join(' ').toLowerCase()===b.slice(0,n).join(' ').toLowerCase())overlap=n;
      if(overlap>=3){seg.text=b.slice(overlap).join(' ').trim();if(!seg.text)continue}
    }
    out.push(seg);
  }
  return out;
}

function youtubeStatusError(status, context='YouTube') {
  if(status===429)return new YoutubeError(`${context} rate-limited this server. Pause and retry later.`, 'YOUTUBE_RATE_LIMITED', 429);
  if(status===403)return new YoutubeError(`${context} blocked this request. A hosted deployment may need cookies or a different outbound IP.`, 'YOUTUBE_BLOCKED', 403);
  if(status===404)return new YoutubeError(`${context} resource was not found or is unavailable.`, 'YOUTUBE_NOT_FOUND', 404);
  return new YoutubeError(`${context} returned HTTP ${status}.`, 'YOUTUBE_HTTP_ERROR', status);
}

export function parseJson3Transcript(data) {
  const segments = [];
  for (const event of data?.events || []) {
    const text = (event.segs || []).map((seg) => seg.utf8 || '').join('').replace(/\n+/g, ' ').trim();
    if (!text) continue;
    segments.push({
      startMs: Number(event.tStartMs || 0),
      durationMs: Number(event.dDurationMs || 0),
      text,
    });
  }
  return normalizeTranscriptSegments(segments);
}

export function decodeHtmlEntities(text = '') {
  return String(text)
    .replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"').replace(/&#39;/g, "'")
    .replace(/&#x([0-9a-f]+);/gi, (_, hex) => String.fromCodePoint(parseInt(hex, 16)))
    .replace(/&#([0-9]+);/g, (_, dec) => String.fromCodePoint(parseInt(dec, 10)));
}

export function parseXmlTranscript(xml) {
  const segments = [];
  const re = /<text\b([^>]*)>([\s\S]*?)<\/text>/g;
  let match;
  while ((match = re.exec(xml))) {
    const attrs = match[1];
    const start = Number(attrs.match(/\bstart="([^"]+)"/)?.[1] || 0);
    const dur = Number(attrs.match(/\bdur="([^"]+)"/)?.[1] || 0);
    const text = decodeHtmlEntities(match[2]).replace(/<[^>]+>/g, '').replace(/\s+/g, ' ').trim();
    if (text) segments.push({ startMs: Math.round(start * 1000), durationMs: Math.round(dur * 1000), text });
  }
  return normalizeTranscriptSegments(segments);
}

export function parseVttTranscript(vtt) {
  const segments = []; const blocks = String(vtt || '').replace(/\r/g, '').split(/\n\n+/);
  const toMs = (x) => { const p=x.replace(',', '.').split(':').map(Number); return Math.round((p.length===3 ? p[0]*3600+p[1]*60+p[2] : p[0]*60+p[1]) * 1000); };
  for (const block of blocks) {
    const lines=block.split('\n').filter(Boolean); const timingIndex=lines.findIndex((l)=>l.includes('-->')); if (timingIndex<0) continue;
    const [a,bRaw]=lines[timingIndex].split('-->').map(x=>x.trim()); const b=bRaw.split(/\s+/)[0];
    const text=decodeHtmlEntities(lines.slice(timingIndex+1).join(' ').replace(/<[^>]+>/g,'')).replace(/\s+/g,' ').trim();
    if (!text) continue; const startMs=toMs(a), endMs=toMs(b); segments.push({ startMs, durationMs: Math.max(0,endMs-startMs), text });
  }
  return normalizeTranscriptSegments(segments);
}

export function chooseYtdlpSubtitle(meta, language='') {
  const clean=(obj)=>Object.keys(obj||{}).filter(k=>k&&k!=='live_chat');const manual=clean(meta?.subtitles),auto=clean(meta?.automatic_captions);const wanted=String(language||'').toLowerCase();
  const pick=(list,w)=>list.find(x=>x.toLowerCase()===w)||list.find(x=>x.toLowerCase().startsWith(`${w}-`))||list.find(x=>w.startsWith(`${x.toLowerCase()}-`));
  if(wanted){const m=pick(manual,wanted);if(m)return{language:m,generated:false};const a=pick(auto,wanted);if(a)return{language:a,generated:true};return null}
  const english=list=>pick(list,'en')||list.find(x=>x.toLowerCase()==='en-orig');const m=english(manual)||manual[0];if(m)return{language:m,generated:false};const a=english(auto)||auto[0];return a?{language:a,generated:true}:null;
}


function playerVideoMetadata(player){
  const d=player?.videoDetails||{},m=player?.microformat?.playerMicroformatRenderer||{};
  const published=m.publishDate||m.uploadDate||'';
  const duration=Number.isFinite(Number(d.lengthSeconds))&&Number(d.lengthSeconds)>0?formatDuration(Number(d.lengthSeconds)):'';
  const views=Number.isFinite(Number(d.viewCount))?String(Math.max(0,Number(d.viewCount))):'';
  const live=m.liveBroadcastDetails||{};
  const kind=live.startTimestamp||d.isLiveContent?'stream':'video';
  return{title:String(d.title||'').slice(0,1000),published:String(published||'').slice(0,100),duration,views,kind};
}
function ytdlpVideoMetadata(meta){
  const raw=String(meta?.upload_date||'');
  const upload=/^\d{8}$/.test(raw)?`${raw.slice(0,4)}-${raw.slice(4,6)}-${raw.slice(6,8)}`:raw;
  return{title:String(meta?.title||'').slice(0,1000),published:meta?.timestamp?new Date(meta.timestamp*1000).toISOString():upload,duration:Number.isFinite(meta?.duration)?formatDuration(meta.duration):String(meta?.duration_string||''),views:Number.isFinite(meta?.view_count)?String(meta.view_count):'',kind:meta?.live_status==='was_live'||meta?.media_type==='livestream'?'stream':meta?.webpage_url?.includes('/shorts/')?'short':'video'};
}

async function fetchTranscriptYtdlp(videoId, { language = '' } = {}) {
  if (!(await hasBinary(ytdlpBin()))) throw new YoutubeError('No public captions are available for this video.', 'NO_CAPTIONS', 404);
  let meta;try{const {stdout}=await runYtdlp([...ytdlpCommonArgs(),'--no-playlist','--skip-download','--dump-single-json','--quiet','--no-warnings',`https://www.youtube.com/watch?v=${videoId}`],{timeout:120_000,maxBuffer:16*1024*1024});meta=JSON.parse(stdout)}catch(e){throw new YoutubeError(`yt-dlp could not inspect subtitle tracks: ${String(e.stderr||e.message).slice(0,500)}`,'YTDLP_CAPTION_INSPECT_FAILED',502)}
  if(meta?.availability && meta.availability!=='public')throw new YoutubeError('This video is not public; transcript extraction is limited to public videos.','NON_PUBLIC_VIDEO',403,{availability:meta.availability});
  const chosen=chooseYtdlpSubtitle(meta,language);if(!chosen)throw new YoutubeError('No public captions are available for this video.', 'NO_CAPTIONS', 404);
  const dir=await mkdtemp(join(tmpdir(),'cts-captions-'));const out=join(dir,'caption.%(ext)s');
  try {
    const writeArg=chosen.generated?'--write-auto-subs':'--write-subs';
    await runYtdlp([
      ...ytdlpCommonArgs(), '--no-playlist','--skip-download',writeArg,'--sub-langs',chosen.language,
      '--sub-format', 'vtt/best', '--convert-subs', 'vtt','--quiet','--no-warnings','-o', out, `https://www.youtube.com/watch?v=${videoId}`,
    ], { timeout: 120_000, maxBuffer: 8 * 1024 * 1024 });
    const files=await readdir(dir);const found=files.find(f=>f.endsWith('.vtt'));
    if (!found) throw new YoutubeError('No public captions are available for this video.', 'NO_CAPTIONS', 404);
    const raw = await readFile(join(dir, found), 'utf8'); const segments = parseVttTranscript(raw);
    if (!segments.length) throw new YoutubeError('Caption file was empty.', 'EMPTY_TRANSCRIPT', 502);
    const text=segments.map(s=>s.text).join(' ').replace(/\s+/g,' ').trim();
    return { videoId, language: chosen.language, languageName: chosen.language, generated: chosen.generated, source: 'yt-dlp-captions', trackName: chosen.generated?'yt-dlp automatic captions':'yt-dlp manual captions', text, segments, words: text.split(/\s+/).filter(Boolean).length, videoMetadata:ytdlpVideoMetadata(meta) };
  } finally { await rm(dir,{recursive:true,force:true}).catch(()=>{}); }
}

export async function fetchTranscript(videoId, { language = '', allowYtdlpFallback = true } = {}) {
  if (!/^[A-Za-z0-9_-]{11}$/.test(String(videoId || ''))) {
    throw new YoutubeError('Invalid YouTube video ID.', 'INVALID_VIDEO_ID', 400);
  }
  const fallback = async (primary) => {
    if (allowYtdlpFallback) { try { return await fetchTranscriptYtdlp(videoId, { language }); } catch (secondary) { if (primary instanceof YoutubeError) primary.details={...(primary.details||{}),fallback:{code:secondary.code||'YTDLP_FAILED',message:secondary.message}}; } }
    if (primary instanceof YoutubeError) throw primary;
    throw new YoutubeError('This server could not reach YouTube to retrieve captions.', 'YOUTUBE_NETWORK_BLOCKED', 502, { cause: String(primary?.message||primary) });
  };
  const watchUrl = `https://www.youtube.com/watch?v=${videoId}&hl=en`;
  let response;try { response = await fetchWithRetry(watchUrl); } catch (error) { return fallback(error); }
  if (!response.ok) return fallback(youtubeStatusError(response.status, 'YouTube video page'));
  const html = await youtubeText(response, envInt('YOUTUBE_PAGE_MAX_MB',32,4,128));
  const player = extractJsonAfterMarkers(html, [
    'var ytInitialPlayerResponse = ', 'ytInitialPlayerResponse = ', 'window["ytInitialPlayerResponse"] = ',
  ]);
  if(player?.microformat?.playerMicroformatRenderer?.isUnlisted===true||player?.videoDetails?.isPrivate===true)throw new YoutubeError('This video is not public; transcript extraction is limited to public videos.','NON_PUBLIC_VIDEO',403);
  let tracks = player?.captions?.playerCaptionsTracklistRenderer?.captionTracks || [];
  if (!tracks.length) {
    const marker = '"captionTracks":';
    const idx = html.indexOf(marker);
    if (idx !== -1) {
      const raw = scanJsonValue(html, idx + marker.length);
      if (raw) try { tracks = JSON.parse(raw); } catch { /* no-op */ }
    }
  }
  if (!tracks.length) { if (allowYtdlpFallback) return fetchTranscriptYtdlp(videoId, { language }); throw new YoutubeError('No public captions are available for this video.', 'NO_CAPTIONS', 404); }

  const track = chooseCaptionTrack(tracks, language);
  if (!track?.baseUrl) throw new YoutubeError('Caption track metadata is incomplete.', 'CAPTION_TRACK_INVALID', 502);
  const captionUrl = new URL(track.baseUrl);
  const wanted = String(language || '').trim().toLowerCase();
  const actual = String(track.languageCode || '').toLowerCase();
  const canTranslate = Array.isArray(player?.captions?.playerCaptionsTracklistRenderer?.translationLanguages);
  if (wanted && actual && wanted !== actual && !actual.startsWith(`${wanted}-`) && canTranslate) captionUrl.searchParams.set('tlang', wanted.split('-')[0]);
  captionUrl.searchParams.set('fmt', 'json3');
  let captionResponse;try{captionResponse=await fetchWithRetry(captionUrl.toString())}catch(error){return fallback(error)}
  let segments = [];
  if (captionResponse.ok) {
    const contentType = captionResponse.headers.get('content-type') || '';
    if (contentType.includes('json') || captionUrl.searchParams.get('fmt') === 'json3') {
      try { segments = parseJson3Transcript(JSON.parse(await youtubeText(captionResponse, envInt('YOUTUBE_CAPTION_MAX_MB',64,1,256)))); } catch { /* fallback below */ }
    }
  }
  if (!segments.length) {
    const fallbackUrl = new URL(captionUrl);
    fallbackUrl.searchParams.delete('fmt');
    try{captionResponse=await fetchWithRetry(fallbackUrl.toString())}catch(error){return fallback(error)}
    if (!captionResponse.ok) return fallback(youtubeStatusError(captionResponse.status, 'YouTube caption download'));
    segments = parseXmlTranscript(await youtubeText(captionResponse, envInt('YOUTUBE_CAPTION_MAX_MB',64,1,256)));
  }
  if (!segments.length) return fallback(new YoutubeError('Captions were present but contained no readable transcript.', 'EMPTY_TRANSCRIPT', 502));

  const text = segments.map((segment) => segment.text).join(' ').replace(/\s+/g, ' ').trim();
  const translated = Boolean(captionUrl.searchParams.get('tlang'));
  const outputLanguage = translated ? wanted.split('-')[0] : (track.languageCode || '');
  return {
    videoId,
    language: outputLanguage,
    languageName: translated ? `${outputLanguage} (translated from ${track.languageCode || 'source'})` : (textValue(track.name) || track.languageCode || ''),
    originalLanguage: translated ? (track.languageCode || '') : '',
    generated: track.kind === 'asr',
    translated,
    source: 'youtube-captions',
    trackName: textValue(track.name) || '',
    text,
    segments,
    words: text ? text.split(/\s+/).length : 0,
    videoMetadata: playerVideoMetadata(player),
  };
}

