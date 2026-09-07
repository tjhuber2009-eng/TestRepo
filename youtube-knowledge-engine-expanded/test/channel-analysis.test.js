import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {parseHumanCount,parseDurationSeconds,parsePublishedMs,observedCountRate,createAnalysisAccumulator,addVideoToAnalysis,finalizeAnalysis,compareAnalyses,compareIdeaClusters,compareAnalysisHistory} from '../public/analysis-core.js';
const root=new URL('../',import.meta.url);

test('public metric parsers handle abbreviated views, durations and relative dates',()=>{
  assert.equal(parseHumanCount('1.2M views'),1_200_000);
  assert.equal(parseHumanCount('845K'),845_000);
  assert.equal(parseHumanCount('No views'),0);
  assert.equal(parseDurationSeconds('1:02:03'),3723);
  assert.equal(parseDurationSeconds('12:34'),754);
  const anchor=Date.UTC(2026,8,5);
  assert.equal(parsePublishedMs('2 days ago',anchor),anchor-2*86400000);
});


test('observed public-count rate distinguishes monitor growth from lifetime views/day',()=>{
  const r=observedCountRate('1.3K','1.1K','2026-09-05T12:00:00Z','2026-09-04T12:00:00Z');
  assert.equal(r.delta,200);assert.equal(r.intervalDays,1);assert.equal(r.perDay,200);assert.equal(r.correction,false);
  const correction=observedCountRate('900','1K','2026-09-05T12:00:00Z','2026-09-04T12:00:00Z');
  assert.equal(correction.correction,true);assert.equal(correction.perDay,null);
});

test('channel analysis reports monitor-observed view and subscriber growth when two observations exist',()=>{
  const now=Date.UTC(2026,8,5,12),prev=new Date(now-86400000).toISOString(),cur=new Date(now).toISOString();
  const acc=createAnalysisAccumulator({sourceId:'yt:growth',sourceTitle:'Growth',sourceSubscribers:'1200',sourcePreviousSubscribers:'1100',sourceSubscribersObservedAt:cur,sourcePreviousSubscribersObservedAt:prev,asOf:now});
  addVideoToAnalysis(acc,{id:'GGGGGGGGGGG',title:'Growth Video',published:'2026-08-01',duration:'10:00',views:'1500',previousViews:'1000',viewsObservedAt:cur,previousViewsObservedAt:prev,status:'done',kind:'video',transcript:{text:'growth analysis',words:100,segments:[]}});
  const r=finalizeAnalysis(acc);
  assert.equal(r.summary.observedViewCount,1);assert.equal(r.summary.medianObservedViewsPerDay,500);assert.equal(r.summary.totalObservedViewsGained,500);assert.equal(r.source.subscriberGrowthPerDay,100);assert.equal(r.observedVelocity[0].id,'GGGGGGGGGGG');assert.match(r.methodology.observedVelocity,/background-monitor/);
});
test('channel analysis computes age-bucket performance, topics, cadence and source-linked evidence',()=>{
  const now=Date.UTC(2026,8,5);
  const acc=createAnalysisAccumulator({sourceId:'yt:test',sourceTitle:'Test Channel',sourceUrl:'https://www.youtube.com/@test',syncedAt:new Date(now).toISOString(),asOf:now});
  const rows=[
    {id:'AAAAAAAAAAA',title:'How to Trade Breakouts',published:'2026-08-20',duration:'10:00',views:'10K',kind:'video',status:'done',url:'https://www.youtube.com/watch?v=AAAAAAAAAAA',transcript:{words:1200,text:'breakout risk entry stop loss breakout trading',segments:[{startMs:10000,durationMs:4000,text:'My entry uses a breakout and a stop loss with one percent risk.'}]}},
    {id:'BBBBBBBBBBB',title:'Breakout Strategy Results 25%',published:'2026-08-18',duration:'12:00',views:'40K',kind:'video',status:'done',url:'https://www.youtube.com/watch?v=BBBBBBBBBBB',transcript:{words:1400,text:'breakout result profit factor risk entry',segments:[{startMs:20000,durationMs:5000,text:'The backtest returned 25% with a profit factor of 1.8.'}]}},
    {id:'CCCCCCCCCCC',title:'Mean Reversion Basics',published:'2026-08-17',duration:'8:00',views:'8K',kind:'video',status:'done',url:'https://www.youtube.com/watch?v=CCCCCCCCCCC',transcript:{words:900,text:'mean reversion indicator risk',segments:[{startMs:30000,durationMs:5000,text:'I recommend a stop loss for every mean reversion setup.'}]}},
  ];
  rows.forEach(v=>addVideoToAnalysis(acc,v));
  const r=finalizeAnalysis(acc);
  assert.equal(r.summary.videos,3);
  assert.equal(r.summary.withViews,3);
  assert.ok(r.topTitleTopics.some(x=>x.topic==='breakout'&&x.count===2));
  assert.ok(r.evidence.some(x=>x.category==='performance_claim'&&x.videoId==='BBBBBBBBBBB'));
  assert.ok(r.evidence.some(x=>x.startMs===20000));
  assert.ok(r.cadence.lastPublishedMs>r.cadence.firstPublishedMs);
  assert.ok(r.outliers.some(x=>x.id==='BBBBBBBBBBB'));
  assert.match(r.methodology.limitations,/CTR/);
});

test('channel comparison identifies shared topics and comparison-channel gaps',()=>{
  const make=(id,title,topicA,topicB,base)=>{const acc=createAnalysisAccumulator({sourceId:id,sourceTitle:title,asOf:Date.UTC(2026,8,5)});for(let i=0;i<4;i++)addVideoToAnalysis(acc,{id:`${base}${String(i).padStart(10,'0')}`.slice(0,11),title:`${topicA} ${i>=2?topicB:'Guide'} ${i}`,published:`2026-08-${10+i}`,duration:'10:00',views:String((i+1)*10000),status:'done',kind:'video',transcript:{text:`${topicA} ${topicB}`,words:100,segments:[]}});return finalizeAnalysis(acc)};
  const a=make('a','A','breakout','risk','A');
  const b=make('b','B','breakout','momentum','B');
  const c=compareAnalyses(a,b);
  assert.ok(c.commonTopics.some(x=>x.topic==='breakout'));
  assert.ok(c.contentGaps.some(x=>x.topic==='momentum'));
});


test('analysis detects topic momentum and structured strategy passages',()=>{
  const now=Date.UTC(2026,8,5),acc=createAnalysisAccumulator({sourceId:'m',sourceTitle:'Momentum',asOf:now});
  const day=86400000;
  for(let i=0;i<4;i++){const recent=i<2;const ms=now-(recent?(30+i*20):(220+(i-2)*30))*day;addVideoToAnalysis(acc,{id:`M${String(i).padStart(10,'0')}`.slice(0,11),title:`${recent?'Momentum':'Value'} Alpha ${i}`,published:new Date(ms).toISOString(),duration:'15:00',views:String(10000+i*1000),status:'done',kind:'video',transcript:{words:1000,text:'risk entry stop target timeframe',segments:[{startMs:1000,durationMs:5000,text:'Enter long on the breakout, use a stop loss, take profit at the target, and risk one percent on the 15 minute timeframe.'}]}})}
  // Add enough videos in each 180-day comparison window for momentum analysis.
  for(let i=4;i<8;i++){const recent=i<6;const ms=now-(recent?(70+(i-4)*20):(280+(i-6)*20))*day;addVideoToAnalysis(acc,{id:`N${String(i).padStart(10,'0')}`.slice(0,11),title:`${recent?'Momentum':'Value'} Signal ${i}`,published:new Date(ms).toISOString(),duration:'15:00',views:'12000',status:'done',kind:'video',transcript:{words:900,text:'entry risk',segments:[]}})}
  const r=finalizeAnalysis(acc);
  assert.ok(r.topicMomentum.rising.some(x=>x.topic==='momentum'));
  assert.ok(r.strategyPassages.some(x=>x.completeness>=4&&x.features.entry&&x.features.stop&&x.features.target&&x.features.risk));
});

test('v2.2 channel analysis UI is worker-backed, cached and source-linked',async()=>{
  const html=await readFile(new URL('public/index.html',root),'utf8');
  const js=await readFile(new URL('public/analysis.js',root),'utf8');
  const worker=await readFile(new URL('public/analysis-worker.js',root),'utf8');
  const sw=await readFile(new URL('public/sw.js',root),'utf8');
  const kb=await readFile(new URL('public/kb.js',root),'utf8');
  assert.match(html,/id="channelAnalysis"/);
  assert.match(html,/id="analysisCompare"/);
  assert.match(html,/id="analysisClaims"/);assert.match(html,/id="analysisVelocity"/);
  assert.match(html,/src="\/analysis\.js"/);
  assert.match(js,/new Worker\('\/analysis-worker\.js'/);
  assert.match(js,/analysis:\$\{id\}/);
  assert.match(js,/timestampUrl\(e\.videoId,e\.startMs\)/);assert.match(js,/Observed views\/day/);assert.match(js,/renderVelocity/);
  assert.match(worker,/index\.openCursor\(IDBKeyRange\.only\(source\.id\)\)/);
  for(const f of ['analysis.js','analysis-core.js','analysis-worker.js'])assert.match(sw,new RegExp(f.replace('.','\\.')));
  assert.match(kb,/kb_meta'\],?'?readwrite|kb_meta/);
  assert.match(kb,/delete\(`analysis:\$\{sourceId\}`\)/);
});


test('cross-channel intelligence finds repeated ideas and conservative potential conflicts',()=>{
  const a=[
    {category:'strategy_rule',score:3,text:'Always buy the breakout above VWAP and use a stop loss with one percent risk.',videoId:'AAAAAAAAAAA',title:'A',startMs:1000,features:{entry:true,stop:true,risk:true}},
    {category:'recommendation',score:2,text:'Avoid oversized positions and keep risk small.',videoId:'BBBBBBBBBBB',title:'B',startMs:2000},
  ];
  const b=[
    {category:'strategy_rule',score:3,text:'Never buy the breakout above VWAP; sell short instead and use a stop loss with one percent risk.',videoId:'CCCCCCCCCCC',title:'C',startMs:3000,features:{entry:true,stop:true,risk:true}},
    {category:'recommendation',score:2,text:'Avoid oversized positions and keep position risk small.',videoId:'DDDDDDDDDDD',title:'D',startMs:4000},
  ];
  const x=compareIdeaClusters(a,b,{matchThreshold:.25});
  assert.ok(x.potentialConflicts.some(row=>row.reasons.includes('opposite direction')||row.reasons.includes('use vs avoid')));
  assert.ok(x.consensus.some(row=>row.category==='recommendation'));
});

test('analysis history highlights newly introduced and changed strategy families',()=>{
  const previous={generatedAt:'2026-09-01T00:00:00Z',source:{syncedAt:'2026-09-01T00:00:00Z'},summary:{videos:10},topTitleTopics:[{topic:'breakout',count:2}],strategyPassages:[{videoId:'AAAAAAAAAAA',startMs:1000}],outliers:[],evidence:[{category:'strategy_rule',score:3,text:'Always buy the breakout above VWAP with a stop loss.',videoId:'AAAAAAAAAAA',title:'Old',startMs:1000,features:{entry:true,stop:true}}]};
  const current={generatedAt:'2026-09-06T00:00:00Z',source:{syncedAt:'2026-09-06T00:00:00Z'},summary:{videos:12},topTitleTopics:[{topic:'breakout',count:4},{topic:'momentum',count:2}],strategyPassages:[{videoId:'BBBBBBBBBBB',startMs:2000}],outliers:[{id:'BBBBBBBBBBB',title:'New outlier',performanceIndex:2}],evidence:[{category:'strategy_rule',score:3,text:'Never buy the breakout above VWAP; sell short instead with a stop loss.',videoId:'BBBBBBBBBBB',title:'New',startMs:2000,features:{entry:true,stop:true}},{category:'recommendation',score:2,text:'Use a volatility filter before every entry.',videoId:'BBBBBBBBBBB',title:'New',startMs:4000}]};
  const h=compareAnalysisHistory(current,previous);
  assert.equal(h.videoDelta,2);
  assert.ok(h.changedIdeas.length>=1);
  assert.ok(h.newIdeas.some(x=>x.category==='recommendation'));
  assert.equal(h.newOutliers[0].id,'BBBBBBBBBBB');
});

test('v2.4 analysis UI exposes longitudinal and privacy-minimized audience intelligence',async()=>{
  const html=await readFile(new URL('public/index.html',root),'utf8');
  const js=await readFile(new URL('public/analysis.js',root),'utf8');
  const sw=await readFile(new URL('public/sw.js',root),'utf8');
  assert.match(html,/id="analysisHistory"/);assert.match(html,/id="analysisAudience"/);assert.match(html,/id="analysisAudienceRun"/);
  assert.match(js,/compareAnalysisHistory/);assert.match(js,/analysis-history:/);assert.match(js,/\/api\/comments/);assert.match(js,/analyzeAudienceSamples/);
  assert.match(sw,/audience-core\.js/);
});
