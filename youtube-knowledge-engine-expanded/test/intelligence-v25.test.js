import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {clusterEvidence,createAnalysisAccumulator,addVideoToAnalysis,finalizeAnalysis,compareAnalysisHistory} from '../public/analysis-core.js';
import {buildAudienceOpportunities} from '../public/audience-core.js';
const root=new URL('../',import.meta.url);

test('evidence strength is bounded and rewards repeated structured support across videos',()=>{
  const one=clusterEvidence([{category:'strategy_rule',score:2,text:'Enter long on a breakout with a stop loss.',videoId:'AAAAAAAAAAA',features:{entry:true,stop:true}}],{threshold:.2})[0];
  const many=clusterEvidence([
    {category:'strategy_rule',score:3,text:'Enter long on the breakout with a stop loss and one percent risk.',videoId:'AAAAAAAAAAA',features:{entry:true,stop:true,risk:true}},
    {category:'strategy_rule',score:3,text:'Enter long on a breakout using a stop loss and risk one percent.',videoId:'BBBBBBBBBBB',features:{entry:true,stop:true,risk:true}},
    {category:'strategy_rule',score:3,text:'Enter long after the breakout and use a stop loss with one percent risk.',videoId:'CCCCCCCCCCC',features:{entry:true,stop:true,risk:true}},
  ],{threshold:.2})[0];
  assert.ok(one.evidenceStrength>=0&&one.evidenceStrength<=100);
  assert.ok(many.evidenceStrength>one.evidenceStrength);
  assert.equal(many.distinctVideos,3);
});

test('creator evolution summarizes historical quarters without requiring private analytics',()=>{
  const acc=createAnalysisAccumulator({sourceId:'evo',sourceTitle:'Evolution',asOf:Date.UTC(2026,8,6)});
  const rows=[
    ['AAAAAAAAAAA','Breakout Risk','2025-02-01','Always use a stop loss on breakout entries.'],
    ['BBBBBBBBBBB','Breakout Results','2025-05-01','The strategy returned 25 percent and had a profit factor of 1.8.'],
    ['CCCCCCCCCCC','Momentum Risk','2026-08-01','Enter long momentum with a stop loss and one percent risk.'],
  ];
  for(const [id,title,published,text] of rows)addVideoToAnalysis(acc,{id,title,published,views:'1000',duration:'10:00',status:'done',kind:'video',transcript:{text,words:100,segments:[{startMs:1000,text}]}});
  const r=finalizeAnalysis(acc);
  assert.equal(r.schema,4);
  assert.ok(r.creatorEvolution.some(x=>x.period==='2025-Q1'));
  assert.ok(r.creatorEvolution.some(x=>x.period==='2026-Q3'));
  assert.match(r.methodology.evidenceStrength,/not whether the idea is true|not whether/i);
});

test('longitudinal comparison prioritizes changed rules and major new outliers',()=>{
  const previous={generatedAt:'2026-09-01T00:00:00Z',source:{syncedAt:'2026-09-01T00:00:00Z'},summary:{videos:5},topTitleTopics:[{topic:'risk',count:2}],strategyPassages:[],outliers:[],evidence:[{category:'strategy_rule',score:3,text:'Always buy the breakout above VWAP with a stop loss.',videoId:'AAAAAAAAAAA',features:{entry:true,stop:true}}]};
  const current={generatedAt:'2026-09-06T00:00:00Z',source:{syncedAt:'2026-09-06T00:00:00Z'},summary:{videos:7},topTitleTopics:[{topic:'risk',count:4}],strategyPassages:[],outliers:[{id:'BBBBBBBBBBB',title:'New winner',performanceIndex:2.4}],evidence:[{category:'strategy_rule',score:3,text:'Never buy the breakout above VWAP; sell short instead with a stop loss.',videoId:'BBBBBBBBBBB',features:{entry:true,stop:true}}]};
  const h=compareAnalysisHistory(current,previous);
  assert.ok(h.alerts.some(x=>x.type==='changed_rule'&&x.severity==='high'));
  assert.ok(h.alerts.some(x=>x.type==='new_outlier'&&x.severity==='high'));
});

test('audience opportunities prioritize unmet repeated demand and retain evidence links',()=>{
  const audience={topTopics:[{topic:'siz',count:4,coverage:.5},{topic:'risk',count:2,coverage:.25}],requests:[{videoId:'AAAAAAAAAAA',commentId:'c1',text:'Please make a position sizing tutorial',likeCount:20}],questions:[{videoId:'AAAAAAAAAAA',commentId:'c2',text:'How should position sizing work?',likeCount:10}],painPoints:[]};
  const analysis={topTitleTopics:[{topic:'risk',count:5,medianPerformanceIndex:1}],underusedWinners:[]};
  const rows=buildAudienceOpportunities(audience,analysis);
  const sizing=rows.find(x=>x.topic==='siz');
  assert.ok(sizing);
  assert.equal(sizing.kind,'unmet audience topic');
  assert.equal(sizing.channelVideoCount,0);
  assert.ok(sizing.priority>50);
  assert.ok(sizing.evidence.length>=1);
});

test('v2.5 UI exposes evolution, evidence strength, alerts and audience opportunity mapping',async()=>{
  const html=await readFile(new URL('public/index.html',root),'utf8');
  const js=await readFile(new URL('public/analysis.js',root),'utf8');
  assert.match(html,/id="analysisEvolution"/);
  assert.match(js,/Evidence strength/);
  assert.match(js,/Priority intelligence alerts/);
  assert.match(js,/buildAudienceOpportunities/);
  assert.match(js,/audienceOpportunities/);
});
