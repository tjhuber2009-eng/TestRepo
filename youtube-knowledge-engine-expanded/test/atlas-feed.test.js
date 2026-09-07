import test from 'node:test';
import assert from 'node:assert/strict';
import {analysisReportToAtlasFeed,mergeAtlasFeeds,passageToAtlasIdea,titleLooksStrategyRelevant} from '../public/atlas-feed.js';

const report={
  generatedAt:'2026-09-06T00:00:00Z',
  source:{id:'yt:test',title:'Test Channel',url:'https://www.youtube.com/@test',syncedAt:'2026-09-06T00:00:00Z'},
  strategyPassages:[
    {
      category:'strategy_rule',score:3.1,videoId:'AAAAAAAAAAA',title:'15 Minute RSI Breakout Strategy',
      startMs:30000,published:'2020-06-01',text:'Enter long on a breakout when RSI confirms on the 15 minute chart, use a stop loss and take profit at the target with one percent risk.',
      features:{entry:true,stop:true,target:true,timeframe:true,risk:true,indicator:true},completeness:6,
    },
    {
      category:'strategy_rule',score:1.5,videoId:'BBBBBBBBBBB',title:'Vague Trading Talk',
      startMs:1000,published:'2024-01-01',text:'Use an indicator for the setup.',
      features:{indicator:true},completeness:1,
    },
  ],
};

test('Atlas feed preserves chronology, evidence and structured rules without creator claims',()=>{
  const rows=analysisReportToAtlasFeed(report,{minimumCompleteness:2});
  assert.equal(rows.length,1);
  const x=rows[0];
  assert.equal(x.idea_id,'yti:yt:test:AAAAAAAAAAA:2');
  assert.equal(x.published_at,'2020-06-01');
  assert.equal(x.channel_title,'Test Channel');
  assert.ok(x.strategy_rules[0].includes('Enter long'));
  assert.ok(x.timeframes.includes('15m'));
  assert.ok(x.tags.includes('rsi'));
  assert.ok(x.tags.includes('breakout'));
  assert.equal(x.evidence.start_ms,30000);
  assert.equal(x.evidence.completeness,6);
  assert.deepEqual(x.claimed_metrics,{});
  assert.ok(x.specification_quality>=0.8);
});

test('passage conversion refuses missing publication chronology',()=>{
  const p={...report.strategyPassages[0],published:''};
  assert.equal(passageToAtlasIdea(report,p),null);
});

test('feed merge is stable and keeps stronger duplicate',()=>{
  const a=analysisReportToAtlasFeed(report)[0];
  const weak={...a,specification_quality:0.2};
  const strong={...a,specification_quality:0.95,summary:'stronger structured version'};
  const out=mergeAtlasFeeds([weak],[strong]);
  assert.equal(out.length,1);
  assert.equal(out[0].summary,'stronger structured version');
});

test('strategy-title triage favors actionable trading videos',()=>{
  assert.equal(titleLooksStrategyRelevant('I Tested MACD Strategy 200 Times'),true);
  assert.equal(titleLooksStrategyRelevant('My Morning Routine'),false);
  assert.equal(titleLooksStrategyRelevant('Opening Range Breakout Setup'),true);
});
