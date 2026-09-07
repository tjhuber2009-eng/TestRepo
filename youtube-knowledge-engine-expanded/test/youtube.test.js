import test from 'node:test';
import assert from 'node:assert/strict';
import {
  normalizeChannelUrl, scanJsonValue, extractJsonAfterMarkers, collectVideosAndContinuations,
  parseJson3Transcript, parseXmlTranscript, parseVttTranscript, decodeHtmlEntities,
  chooseCaptionTrack, chooseYtdlpSubtitle, normalizeYtdlpComments, discoverChannelScrape, discoverChannel, fetchTranscript,
} from '../lib/youtube.js';

test('normalizes handles, channel IDs, legacy user URLs, and tab URLs',()=>{
  assert.equal(normalizeChannelUrl('@OpenAI'),'https://www.youtube.com/@OpenAI/videos');
  assert.equal(normalizeChannelUrl('youtube.com/@OpenAI'),'https://www.youtube.com/@OpenAI/videos');
  assert.equal(normalizeChannelUrl('https://youtube.com/user/legacy'),'https://www.youtube.com/user/legacy/videos');
  assert.equal(normalizeChannelUrl('https://www.youtube.com/@OpenAI/shorts'),'https://www.youtube.com/@OpenAI/shorts');
  assert.throws(()=>normalizeChannelUrl('https://example.com/@bad'),/Only youtube\.com/);
});

test('balanced JSON scanner ignores braces inside strings',()=>{
  const source='abc {"x":"} still string", "nested":{"a":1}} trailing';
  assert.deepEqual(JSON.parse(scanJsonValue(source,4)),{x:'} still string',nested:{a:1}});
});

test('extracts JSON after known marker',()=>{
  const html='<script>var ytInitialData = {"ok":true,"list":[1,2]};</script>';
  assert.deepEqual(extractJsonAfterMarkers(html,['var ytInitialData = ']),{ok:true,list:[1,2]});
});

test('collects video renderers, shorts, continuation and deduplicates',()=>{
  const data={contents:[
    {videoRenderer:{videoId:'abcdefghijk',title:{simpleText:'A'}}},
    {reelItemRenderer:{videoId:'12345678901',headline:{simpleText:'S'}}},
    {videoRenderer:{videoId:'abcdefghijk',title:{simpleText:'dup'}}},
    {continuationItemRenderer:{continuationEndpoint:{continuationCommand:{token:'NEXT'}}}},
  ]};
  const r=collectVideosAndContinuations(data); assert.equal(r.videos.length,2); assert.equal(r.videos[1].kind,'short'); assert.deepEqual(r.continuations,['NEXT']);
});

test('json3 parser preserves millisecond timings',()=>{
  assert.deepEqual(parseJson3Transcript({events:[{tStartMs:1250,dDurationMs:750,segs:[{utf8:'hello '},{utf8:'world'}]}]}),[{startMs:1250,durationMs:750,text:'hello world'}]);
});

test('XML parser decodes entities and fractional seconds',()=>{
  assert.deepEqual(parseXmlTranscript('<transcript><text start="1.25" dur="2.5">Tom &amp; Jerry</text></transcript>'),[{startMs:1250,durationMs:2500,text:'Tom & Jerry'}]);
  assert.equal(decodeHtmlEntities('&lt;a&gt; &#x41;'),'<a> A');
});

test('WebVTT parser handles cue settings and hour timestamps',()=>{
  const vtt='WEBVTT\n\n00:00:01.250 --> 00:00:03.750 align:start\nHello <b>world</b>\n\n01:02:03.000 --> 01:02:04.100\nLater';
  assert.deepEqual(parseVttTranscript(vtt),[
    {startMs:1250,durationMs:2500,text:'Hello world'},
    {startMs:3723000,durationMs:1100,text:'Later'},
  ]);
});

test('caption selection prioritizes requested language then manual track',()=>{
  const tracks=[
    {languageCode:'en',kind:'asr',name:{simpleText:'English auto'}},
    {languageCode:'es',name:{simpleText:'Spanish manual'}},
    {languageCode:'en-US',name:{simpleText:'English US manual'}},
  ];
  assert.equal(chooseCaptionTrack(tracks,'en').name.simpleText,'English auto');
  assert.equal(chooseCaptionTrack(tracks,'').name.simpleText,'English US manual');
});


test('yt-dlp subtitle selection prefers requested/manual then any available language',()=>{const meta={subtitles:{es:[{}],fr:[{}]},automatic_captions:{en:[{}],de:[{}]}};assert.deepEqual(chooseYtdlpSubtitle(meta,'fr'),{language:'fr',generated:false});assert.deepEqual(chooseYtdlpSubtitle(meta,'en'),{language:'en',generated:true});assert.deepEqual(chooseYtdlpSubtitle(meta,''),{language:'es',generated:false});assert.equal(chooseYtdlpSubtitle({subtitles:{},automatic_captions:{}},''),null)});
function mockResponse({status=200,text='',json=null,contentType='text/html'}={}){const raw=json!=null?JSON.stringify(json):text;return{ok:status>=200&&status<300,status,headers:{get(n){return n.toLowerCase()==='content-type'?contentType:null}},async text(){return raw},async json(){return json??JSON.parse(raw)}}}

test('web discovery reconciles videos, continuation, shorts and streams',async()=>{
  const original=globalThis.fetch;
  const initial=(payload)=>`<script>var ytInitialData = ${JSON.stringify(payload)};</script><script>ytcfg.set({"INNERTUBE_API_KEY":"KEY","INNERTUBE_CLIENT_VERSION":"2.20260901.00.00","VISITOR_DATA":"VIS"});</script>`;
  const videoPage=initial({metadata:{channelMetadataRenderer:{externalId:'UCabcdefghijklmnopqrstuv',title:'Fixture'}},contents:[{videoRenderer:{videoId:'abcdefghijk',title:{simpleText:'V1'}}},{continuationItemRenderer:{continuationEndpoint:{continuationCommand:{token:'NEXT'}}}}]});
  const shortPage=initial({contents:[{reelItemRenderer:{videoId:'12345678901',headline:{simpleText:'S1'}}}]});
  const streamPage=initial({contents:[{videoRenderer:{videoId:'stream12345',title:{simpleText:'Live replay'}}}]});
  globalThis.fetch=async(url,options={})=>{const u=String(url);if(u.endsWith('/videos'))return mockResponse({text:videoPage});if(u.endsWith('/shorts'))return mockResponse({text:shortPage});if(u.endsWith('/streams'))return mockResponse({text:streamPage});if(u.includes('/youtubei/v1/browse')){assert.equal(options.method,'POST');return mockResponse({contentType:'application/json',json:{contents:[{videoRenderer:{videoId:'lmnopqrstuv',title:{simpleText:'V2'}}}]}})}throw new Error(`Unexpected ${u}`)};
  try{const r=await discoverChannelScrape('@fixture');assert.deepEqual(r.videos.map(v=>v.id),['abcdefghijk','lmnopqrstuv','12345678901','stream12345']);assert.equal(r.videos.find(v=>v.id==='12345678901').kind,'short');assert.equal(r.videos.find(v=>v.id==='stream12345').kind,'stream')}finally{globalThis.fetch=original}
});

test('caption fetch parses JSON3 and marks provenance',async()=>{
  const original=globalThis.fetch;const player={videoDetails:{title:'Metadata title',lengthSeconds:'125',viewCount:'12345'},microformat:{playerMicroformatRenderer:{publishDate:'2026-09-01'}},captions:{playerCaptionsTracklistRenderer:{captionTracks:[{baseUrl:'https://captions.local/track?lang=en',languageCode:'en',name:{simpleText:'English'},kind:'asr'}]}}};
  globalThis.fetch=async(url)=>{const u=String(url);if(u.includes('youtube.com/watch'))return mockResponse({text:`<script>var ytInitialPlayerResponse = ${JSON.stringify(player)};</script>`});if(u.includes('captions.local'))return mockResponse({contentType:'application/json',json:{events:[{tStartMs:0,dDurationMs:1000,segs:[{utf8:'hello world'}]}]}});throw new Error(u)};
  try{const r=await fetchTranscript('abcdefghijk',{allowYtdlpFallback:false});assert.equal(r.source,'youtube-captions');assert.equal(r.generated,true);assert.equal(r.words,2);assert.equal(r.videoMetadata.views,'12345');assert.equal(r.videoMetadata.duration,'2:05');assert.equal(r.videoMetadata.published,'2026-09-01')}finally{globalThis.fetch=original}
});

test('caption translation adds tlang when requested language differs',async()=>{
  const original=globalThis.fetch;let captionUrl='';const player={captions:{playerCaptionsTracklistRenderer:{captionTracks:[{baseUrl:'https://captions.local/track?lang=es',languageCode:'es',name:{simpleText:'Español'}}],translationLanguages:[{languageCode:'en',languageName:{simpleText:'English'}}]}}};
  globalThis.fetch=async(url)=>{const u=String(url);if(u.includes('youtube.com/watch'))return mockResponse({text:`<script>var ytInitialPlayerResponse = ${JSON.stringify(player)};</script>`});if(u.includes('captions.local')){captionUrl=u;return mockResponse({contentType:'application/json',json:{events:[{tStartMs:0,dDurationMs:1000,segs:[{utf8:'translated'}]}]}})}throw new Error(u)};
  try{const r=await fetchTranscript('abcdefghijk',{language:'en',allowYtdlpFallback:false});assert.equal(new URL(captionUrl).searchParams.get('tlang'),'en');assert.equal(r.translated,true)}finally{globalThis.fetch=original}
});

test('direct caption path rejects unlisted videos before caption download',async()=>{const original=globalThis.fetch;let calls=0;const player={microformat:{playerMicroformatRenderer:{isUnlisted:true}},captions:{playerCaptionsTracklistRenderer:{captionTracks:[{baseUrl:'https://captions.local/track',languageCode:'en'}]}}};globalThis.fetch=async()=>{calls++;return mockResponse({text:`<script>var ytInitialPlayerResponse = ${JSON.stringify(player)};</script>`})};try{await assert.rejects(()=>fetchTranscript('abcdefghijk',{allowYtdlpFallback:false}),e=>e.code==='NON_PUBLIC_VIDEO'&&e.status===403);assert.equal(calls,1)}finally{globalThis.fetch=original}});

import { normalizeTranscriptSegments } from '../lib/youtube.js';

test('rolling caption normalization removes progressive duplicates and word overlap',()=>{
  const out=normalizeTranscriptSegments([
    {startMs:0,durationMs:1000,text:'we are testing'},
    {startMs:900,durationMs:1200,text:'we are testing the system'},
    {startMs:2100,durationMs:1000,text:'testing the system with three words'},
  ]);
  assert.equal(out[0].text,'we are testing the system');
  assert.equal(out[1].text,'with three words');
});

test('default discovery never uses the YouTube Data API even when a legacy key env var exists',async()=>{
  const originalFetch=globalThis.fetch,oldDisable=process.env.YTDLP_DISABLE_DISCOVERY,oldKey=process.env.YOUTUBE_API_KEY;process.env.YTDLP_DISABLE_DISCOVERY='1';process.env.YOUTUBE_API_KEY='legacy-key-should-be-ignored';
  const page=`<script>var ytInitialData = ${JSON.stringify({metadata:{channelMetadataRenderer:{externalId:'UCabcdefghijklmnopqrstuv',title:'Fixture'}},contents:[{videoRenderer:{videoId:'abcdefghijk',title:{simpleText:'V1'}}}]})};</script>`;
  const seen=[];globalThis.fetch=async url=>{seen.push(String(url));return mockResponse({text:page})};
  try{const r=await discoverChannel('@fixture',{maxVideos:10});assert.equal(r.source,'youtube-web');assert.ok(seen.length>=1);assert.ok(seen.every(u=>new URL(u).hostname==='www.youtube.com'))}finally{globalThis.fetch=originalFetch;if(oldDisable===undefined)delete process.env.YTDLP_DISABLE_DISCOVERY;else process.env.YTDLP_DISABLE_DISCOVERY=oldDisable;if(oldKey===undefined)delete process.env.YOUTUBE_API_KEY;else process.env.YOUTUBE_API_KEY=oldKey}
});

test('translated caption XML fallback preserves tlang and reports output language',async()=>{
  const original=globalThis.fetch;const seen=[];const player={captions:{playerCaptionsTracklistRenderer:{captionTracks:[{baseUrl:'https://captions.local/track?lang=es',languageCode:'es',name:{simpleText:'Español'}}],translationLanguages:[{languageCode:'en',languageName:{simpleText:'English'}}]}}};
  globalThis.fetch=async(url)=>{const u=String(url);seen.push(u);if(u.includes('youtube.com/watch'))return mockResponse({text:`<script>var ytInitialPlayerResponse = ${JSON.stringify(player)};</script>`});if(u.includes('captions.local')&&new URL(u).searchParams.get('fmt')==='json3')return mockResponse({contentType:'text/plain',text:'not json'});if(u.includes('captions.local'))return mockResponse({contentType:'text/xml',text:'<transcript><text start="0" dur="1">translated fallback</text></transcript>'});throw new Error(u)};
  try{const r=await fetchTranscript('abcdefghijk',{language:'en',allowYtdlpFallback:false});const fallback=seen.find(u=>u.includes('captions.local')&&!new URL(u).searchParams.has('fmt'));assert.ok(fallback);assert.equal(new URL(fallback).searchParams.get('tlang'),'en');assert.equal(r.language,'en');assert.equal(r.originalLanguage,'es');assert.match(r.languageName,/translated from es/);assert.equal(r.text,'translated fallback')}finally{globalThis.fetch=original}
});

test('duplicate tab entries inherit specialized type without losing richer duration',async()=>{const original=globalThis.fetch;const page=data=>`<script>var ytInitialData = ${JSON.stringify(data)};</script><script>ytcfg.set({"INNERTUBE_API_KEY":"K"});</script>`;globalThis.fetch=async url=>{const u=String(url);if(u.endsWith('/videos'))return mockResponse({text:page({metadata:{channelMetadataRenderer:{externalId:'UCabcdefghijklmnopqrstuv',title:'D'}},contents:[{videoRenderer:{videoId:'abcdefghijk',title:{simpleText:'Same'},lengthText:{simpleText:'1:23'}}}]})});if(u.endsWith('/shorts'))return mockResponse({text:page({contents:[{reelItemRenderer:{videoId:'abcdefghijk',headline:{simpleText:'Same'}}}]})});if(u.endsWith('/streams'))return mockResponse({text:page({contents:[]})});throw new Error(u)};try{const r=await discoverChannelScrape('@d');assert.equal(r.videos.length,1);assert.equal(r.videos[0].kind,'short');assert.equal(r.videos[0].duration,'1:23')}finally{globalThis.fetch=original}});

test('public discovery remains usable without any API credential',async()=>{const original=globalThis.fetch,old=process.env.YTDLP_DISABLE_DISCOVERY;process.env.YTDLP_DISABLE_DISCOVERY='1';const page=data=>`<script>var ytInitialData = ${JSON.stringify(data)};</script>`;globalThis.fetch=async url=>{const u=String(url);if(u.endsWith('/videos'))return mockResponse({text:page({metadata:{channelMetadataRenderer:{externalId:'UCabcdefghijklmnopqrstuv',title:'No Key'}},contents:[{videoRenderer:{videoId:'abcdefghijk',title:{simpleText:'Public'}}}]})});return mockResponse({text:page({contents:[]})})};try{const r=await discoverChannel('@nokey',{maxVideos:10});assert.equal(r.videos[0].id,'abcdefghijk');assert.equal(r.channel.title,'No Key')}finally{globalThis.fetch=original;if(old===undefined)delete process.env.YTDLP_DISABLE_DISCOVERY;else process.env.YTDLP_DISABLE_DISCOVERY=old}});


test('yt-dlp comment normalization is bounded and privacy-minimized',()=>{const rows=normalizeYtdlpComments({comments:[{id:'c1',text:' Great  explanation ',like_count:7,author:'Private Name',author_is_uploader:true,parent:'root',timestamp:123},{id:'c2',text:'',author:'Nobody'}]},50);assert.deepEqual(rows,[{commentId:'c1',text:'Great explanation',likeCount:7,timestamp:123,parent:'root',authorIsUploader:true}]);assert.equal('author' in rows[0],false)});
