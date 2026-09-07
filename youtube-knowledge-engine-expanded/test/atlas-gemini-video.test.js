import test from 'node:test';
import assert from 'node:assert/strict';
import {analyzeYouTubeVideoWithGemini,geminiAnalysisToAtlasFeed,sanitizeGeminiAnalysis} from '../lib/atlas-gemini-video.js';

test('Gemini result sanitizer keeps reproducible rules and strips malformed items',()=>{
  const x=sanitizeGeminiAnalysis({
    strategy_relevant:true,
    visual_context_used:true,
    ideas:[
      {summary:'QQE pullback confirmation',rules:['Wait for pullback','Enter when QQE confirms'],markets:['crypto'],timeframes:['15m'],tags:['qqe','pullback'],timestamps_seconds:[12,45,-2,'bad'],specificity:1.2},
      {summary:'',rules:[],specificity:1},
    ],
  },{model:'test-model'});
  assert.equal(x.strategy_relevant,true);
  assert.equal(x.visual_context_used,true);
  assert.equal(x.ideas.length,1);
  assert.equal(x.ideas[0].specificity,1);
  assert.deepEqual(x.ideas[0].timestamps_seconds,[12,45]);
  assert.equal(x.model,'test-model');
});

test('Gemini call sends public YouTube URL and API key only in header',async()=>{
  let captured;
  const fetchImpl=async(url,opts)=>{
    captured={url,opts};
    return{
      ok:true,status:200,
      text:async()=>JSON.stringify({
        steps:[{type:'model_output',content:[{type:'text',text:JSON.stringify({
          strategy_relevant:true,
          visual_context_used:true,
          ideas:[{summary:'Breakout after compression',rules:['Enter on range breakout','Exit on opposite break'],markets:['stock'],timeframes:['1h'],tags:['breakout'],timestamps_seconds:[90],specificity:.8}],
        })}]}],
      }),
    };
  };
  const out=await analyzeYouTubeVideoWithGemini({
    videoUrl:'https://www.youtube.com/watch?v=AAAAAAAAAAA',
    apiKey:'secret-test-key',
    model:'gemini-test',
    fetchImpl,
  });
  assert.equal(out.ideas.length,1);
  assert.equal(captured.opts.headers['x-goog-api-key'],'secret-test-key');
  assert.ok(!captured.opts.body.includes('secret-test-key'));
  const body=JSON.parse(captured.opts.body);
  assert.equal(captured.url,'https://generativelanguage.googleapis.com/v1beta/interactions');
  assert.equal(body.model,'gemini-test');
  assert.equal(body.input[0].type,'video');
  assert.equal(body.input[0].uri,'https://www.youtube.com/watch?v=AAAAAAAAAAA');
  assert.equal(body.response_format.mime_type,'application/json');
  assert.equal(body.response_format.type,'text');
  assert.ok(body.system_instruction.includes('untrusted source evidence'));
});

test('Atlas rows keep chronology provenance and never promote creator claims',()=>{
  const rows=geminiAnalysisToAtlasFeed({
    analysis:{
      strategy_relevant:true,visual_context_used:true,model:'gemini-test',
      ideas:[{summary:'Use visible VWAP rejection',rules:['Enter after VWAP rejection'],markets:['stock'],timeframes:['5m'],tags:['vwap'],timestamps_seconds:[100],specificity:.9}],
    },
    video:{id:'AAAAAAAAAAA',title:'VWAP setup'},
    channel:{id:'UC123',title:'Channel'},
    publishedAt:'2020-09-01',
    publicationBasis:'relative_upper_bound',
  });
  assert.equal(rows.length,1);
  assert.equal(rows[0].published_at,'2020-09-01');
  assert.equal(rows[0].source_kind,'gemini_youtube_video_understanding');
  assert.deepEqual(rows[0].claimed_metrics,{});
  assert.equal(rows[0].evidence.publication_basis,'relative_upper_bound');
  assert.equal(rows[0].evidence.creator_performance_claims_are_evidence,false);
  assert.equal(rows[0].evidence.visual_context_used,true);
});

test('missing key fails without network access',async()=>{
  await assert.rejects(
    ()=>analyzeYouTubeVideoWithGemini({videoUrl:'https://www.youtube.com/watch?v=AAAAAAAAAAA',apiKey:'',fetchImpl:async()=>{throw new Error('must not call')}}),
    e=>e?.code==='GEMINI_KEY_MISSING',
  );
});
